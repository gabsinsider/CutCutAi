import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .media import audio_metrics, burn_subtitles, duration, make_clip_range, scene_score, thumbnail
from .metadata import suggest_metadata
from .models import Clip
from .proxy import normalize_proxy_url
from .ranking import upsert_clip
from .scoring import audio_score, combine_scores, transcript_score
from .transcription import transcribe
from .validation import validate_source_url

MIN_CLIP_SECONDS = 60.0
MAX_CLIP_SECONDS = 150.0
DEFAULT_CAPTURE_SECONDS = 300
RANKING_PATH = Path("data/ranking.json")


def download(url: str, output: Path, seconds: int) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    base = ["yt-dlp", "--no-playlist", "--no-progress", "--no-simulate", "--impersonate", "chrome", "--extractor-retries", "5", "--fragment-retries", "5", "--retry-sleep", "extractor:2", "--js-runtimes", "node", "--downloader", "ffmpeg", "--downloader-args", f"ffmpeg_i:-t {seconds}", "--merge-output-format", "mkv", "-f", "bestvideo[height=1080][fps<=30]+bestaudio/bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080][fps<=30]/best", "-o", str(output), "--print", "title"]
    proxy_url = normalize_proxy_url(os.getenv("CUTAI_PROXY_URL", "")); cookie_file = os.getenv("CUTAI_YOUTUBE_COOKIES_FILE", "").strip(); user_agent = os.getenv("CUTAI_YOUTUBE_USER_AGENT", "").strip()
    attempts = ["youtube:player_client=tv,web_safari;formats=missing_pot", "youtube:player_client=web_safari,android;formats=missing_pot", "youtube:player_client=default,android;formats=missing_pot"]
    errors = []
    for extractor_args in attempts:
        command = base + ["--extractor-args", extractor_args]
        if cookie_file and Path(cookie_file).exists(): command += ["--cookies", cookie_file]
        if user_agent: command += ["--user-agent", user_agent]
        if proxy_url: command += ["--proxy", proxy_url]
        command.append(url); result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0: return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "Live"
        detail = result.stderr.strip()[-1600:] or result.stdout.strip()[-1600:]
        if proxy_url: detail = detail.replace(proxy_url, "[proxy protegido]")
        errors.append(detail); output.unlink(missing_ok=True)
    raise RuntimeError("yt-dlp não conseguiu acessar esta transmissão após tentar múltiplos clientes do YouTube:\n" + "\n--- tentativa seguinte ---\n".join(errors))

REACTION_PHRASES = {"meu deus", "não acredito", "que isso", "que loucura", "que absurdo", "olha isso", "olha só", "caramba", "nossa", "incrível", "absurdo", "sensacional", "impressionante", "histórico", "golaço", "gol"}
STORY_TERMS = {"porque", "então", "mas", "porém", "aconteceu", "depois", "antes", "agora", "resultado", "problema", "verdade", "segredo", "explicar", "entender", "motivo", "finalmente", "conseguiu", "ganhou", "perdeu"}
OPENING_TERMS = {"olha", "seguinte", "aconteceu", "começou", "primeiro", "hoje", "agora", "entenda", "explicar", "questão", "problema", "motivo"}
CONTINUATION_ENDINGS = {"e", "mas", "porque", "pois", "que", "quando", "então", "porém", "só", "se", "como", "para", "pra", "de", "do", "da", "dos", "das", "com"}
CONCLUSION_TERMS = {"enfim", "finalmente", "pronto", "acabou", "terminou", "resolveu", "resolvido", "resultado", "conclusão", "concluindo", "fim", "ganhou", "perdeu", "conseguiu"}


def _words(text: str) -> list[str]: return re.findall(r"[\wÀ-ÿ]+", text.lower())
def _looks_complete(text: str) -> bool:
    words = _words(text.strip()); return bool(words and words[-1] not in CONTINUATION_ENDINGS and text.strip().endswith((".", "!", "?")))
def _has_conclusion(text: str) -> bool:
    words = _words(text); return _looks_complete(text) and (any(term in words[-24:] for term in CONCLUSION_TERMS) or text.strip().endswith(("!", "?")))

def _make_block(items: list[dict]) -> dict:
    return {"start": float(items[0].get("start", 0)), "end": float(items[-1].get("end", 0)), "segments": list(items), "text": " ".join(str(s.get("text", "")) for s in items).strip()}

def _topic_blocks(segments: list[dict], source_duration: float) -> list[dict]:
    if not segments: return []
    blocks, current = [], []
    for segment in segments:
        if current:
            gap = float(segment.get("start", 0)) - float(current[-1].get("end", 0)); elapsed = float(current[-1].get("end", 0)) - float(current[0].get("start", 0))
            if gap >= 1.8 or (elapsed >= 40.0 and gap >= 0.9): blocks.append(_make_block(current)); current = []
        current.append(segment)
    if current: blocks.append(_make_block(current))
    return blocks

def _block_score(block: dict) -> float:
    text = block["text"]; normalized = text.lower(); words = _words(text); duration_s = max(1.0, block["end"] - block["start"])
    reaction_hits = sum(1 for p in REACTION_PHRASES if p in normalized); story_hits = sum(1 for t in STORY_TERMS if t in words)
    return min(100.0, min(30.0, len(words)*0.13) + min(25.0, reaction_hits*7 + text.count("!")*2) + min(25.0, story_hits*2.8 + text.count("?")*2.5) + min(15.0, duration_s*0.25) + (5 if _looks_complete(text) else 0))

def _context_range(blocks: list[dict], index: int, source_duration: float) -> tuple[float, float]:
    main = blocks[index]; start = main["start"]; end = main["end"]
    if index > 0:
        previous = blocks[index-1]; gap = start - previous["end"]
        first_words = set(_words(main["text"])[:12])
        needs_setup = end-start < 50.0 or gap < 1.2 or not (first_words & OPENING_TERMS)
        if needs_setup and end-previous["start"] <= MAX_CLIP_SECONDS: start = previous["start"]
    cursor = index + 1
    while cursor < len(blocks) and end-start < MIN_CLIP_SECONDS:
        nxt = blocks[cursor]
        if nxt["end"]-start > MAX_CLIP_SECONDS: break
        end = nxt["end"]; cursor += 1
    while cursor < len(blocks) and end-start < MAX_CLIP_SECONDS:
        current_text = blocks[cursor-1]["text"] if cursor else main["text"]; gap = blocks[cursor]["start"]-end
        if _has_conclusion(current_text) and gap >= .6: break
        if _looks_complete(current_text) and gap >= 2.0: break
        nxt = blocks[cursor]
        if nxt["end"]-start > MAX_CLIP_SECONDS: break
        end = nxt["end"]; cursor += 1
    if end-start < MIN_CLIP_SECONDS:
        start = max(0.0, start-min(12.0, MIN_CLIP_SECONDS-(end-start))); end = min(source_duration, max(end, start+MIN_CLIP_SECONDS))
    return max(0.0,start), min(source_duration,min(end,start+MAX_CLIP_SECONDS))

def _story_quality(blocks: list[dict], index: int, start: float, end: float, source_duration: float) -> float:
    main = blocks[index]; first_words = set(_words(main["text"])[:15]); quality = _block_score(main)
    if first_words & OPENING_TERMS: quality += 10
    if _has_conclusion(main["text"]): quality += 10
    if end-start >= MIN_CLIP_SECONDS: quality += 5
    # Penaliza candidatos encostados no fim da captura: podem estar sem o desfecho real.
    if source_duration-end < 20.0: quality -= 25
    elif source_duration-end < 40.0: quality -= 10
    # Penaliza assunto principal iniciado logo no começo da amostra, pois contexto anterior pode estar ausente.
    if main["start"] < 8.0: quality -= 15
    return max(0.0, min(100.0, quality))

def choose_top_ranges(segments: list[dict], source_duration: float, limit: int=3) -> list[tuple[float,float,float]]:
    blocks = _topic_blocks(segments, source_duration)
    if not blocks: return [(50.0,0.0,min(source_duration,max(MIN_CLIP_SECONDS,source_duration)))]
    candidates=[]
    for i, block in enumerate(blocks):
        start,end=_context_range(blocks,i,source_duration); candidates.append((_story_quality(blocks,i,start,end,source_duration),start,end))
    candidates.sort(key=lambda x:x[0], reverse=True); selected=[]
    for candidate in candidates:
        _,start,end=candidate
        if any(max(0.0,min(end,ce)-max(start,cs))/max(1.0,min(end-start,ce-cs)) > .25 for _,cs,ce in selected): continue
        selected.append(candidate)
        if len(selected)==limit: break
    return selected or [candidates[0]]

def process(url: str, workdir: Path, capture_seconds: int=DEFAULT_CAPTURE_SECONDS) -> list[Clip]:
    validate_source_url(url); workdir.mkdir(parents=True,exist_ok=True); source=workdir/"capture.mkv"
    source_title=download(url,source,max(60,min(capture_seconds,900))); source_duration=duration(source)
    if source_duration<MIN_CLIP_SECONDS: raise RuntimeError(f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. São necessários pelo menos 60s para publicar um corte.")
    _,segments=transcribe(source); ranges=choose_top_ranges(segments,source_duration,3); clips=[]
    for rank,(context_score,start,end) in enumerate(ranges,1):
        clip_id=hashlib.sha1(f"{url}:{datetime.now(UTC).isoformat()}:{rank}".encode()).hexdigest()[:12]; raw=workdir/f"{clip_id}.raw.mp4"; clip_path=workdir/f"{clip_id}.mp4"; thumb=workdir/f"{clip_id}.jpg"
        make_clip_range(source,raw,start,end); local=[]
        for s in segments:
            ss=float(s.get("start",0)); se=float(s.get("end",0))
            if se>start and ss<end: local.append({"start":max(0.0,ss-start),"end":min(end-start,se-start),"text":str(s.get("text",""))})
        burn_subtitles(raw,clip_path,local); raw.unlink(missing_ok=True); thumbnail(clip_path,thumb); transcript=" ".join(s["text"] for s in local).strip(); mean,peak=audio_metrics(clip_path); a_score,a_reasons=audio_score(mean,peak); t_score,t_reasons=transcript_score(transcript); s_score=scene_score(clip_path); base=combine_scores(a_score,t_score,s_score); base.total=round(min(100.0,base.total*.60+context_score*.40),2); description,hashtags=suggest_metadata(transcript)
        reasons=a_reasons+t_reasons+[f"história {context_score:.0f}/100",f"cena {s_score:.0f}/100",f"duração {end-start:.1f}s","início, desenvolvimento e desfecho","legendado"]; breakdown={"audio":round(a_score,2),"transcript":round(t_score,2),"scene":round(s_score,2),"context":round(context_score,2)}
        clip=Clip(id=clip_id,title=source_title,source_title=source_title,score=base,score_breakdown=breakdown,duration=round(end-start,2),source_url=url,asset_url="",thumbnail_url="",transcript=transcript,reasons=reasons,description=description,hashtags=hashtags,created_at=datetime.now(UTC).isoformat()); upsert_clip(RANKING_PATH,clip); clips.append(clip)
    clips.sort(key=lambda c:c.score.total,reverse=True); return clips[:3]

def main()->None:
    parser=argparse.ArgumentParser(); parser.add_argument("--url",required=True); parser.add_argument("--capture-seconds",type=int,default=DEFAULT_CAPTURE_SECONDS); parser.add_argument("--workdir",default="work"); args=parser.parse_args(); clips=process(args.url,Path(args.workdir),args.capture_seconds); Path(args.workdir,"result.json").write_text(json.dumps({"clips":[c.to_dict() for c in clips]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__": main()
