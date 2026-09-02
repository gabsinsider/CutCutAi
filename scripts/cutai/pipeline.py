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
DEFAULT_CAPTURE_SECONDS = 360
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
RESET_TERMS = {"agora", "mudando", "seguinte", "outra", "outro", "próximo", "próxima", "falando", "voltando"}
CONTINUATION_STARTS = {"e", "mas", "porque", "pois", "então", "porém", "aí", "ele", "ela", "eles", "elas", "isso", "esse", "essa", "também", "só"}
CONTINUATION_ENDINGS = {"e", "mas", "porque", "pois", "que", "quando", "então", "porém", "só", "se", "como", "para", "pra", "de", "do", "da", "dos", "das", "com"}
CONCLUSION_TERMS = {"enfim", "finalmente", "pronto", "acabou", "terminou", "resolveu", "resolvido", "conclusão", "concluindo", "fim", "é isso", "foi isso", "ficou assim"}
STOPWORDS = {"a","o","as","os","um","uma","de","da","do","das","dos","e","é","em","no","na","nos","nas","que","se","por","para","pra","com","como","mais","muito","já","não","sim","eu","você","vocês","ele","ela","eles","elas","isso","esse","essa","aí","foi","vai","tem","tá","está"}


def _words(text: str) -> list[str]: return re.findall(r"[\wÀ-ÿ]+", text.lower())
def _keywords(text: str) -> set[str]: return {w for w in _words(text) if len(w) >= 4 and w not in STOPWORDS}
def _looks_complete(text: str) -> bool:
    words = _words(text.strip()); return bool(words and words[-1] not in CONTINUATION_ENDINGS and text.strip().endswith((".", "!")))
def _explicit_conclusion(text: str) -> bool:
    normalized = text.lower().strip(); return _looks_complete(text) and any(term in normalized[-180:] for term in CONCLUSION_TERMS)
def _starts_as_continuation(text: str) -> bool:
    words = _words(text); return bool(words and words[0] in CONTINUATION_STARTS)
def _similarity(a: str, b: str) -> float:
    ka, kb = _keywords(a), _keywords(b)
    if not ka or not kb: return 0.0
    return len(ka & kb) / max(1, min(len(ka), len(kb)))

def _make_block(items: list[dict]) -> dict:
    return {"start": float(items[0].get("start", 0)), "end": float(items[-1].get("end", 0)), "segments": list(items), "text": " ".join(str(s.get("text", "")) for s in items).strip()}

def _topic_blocks(segments: list[dict], source_duration: float) -> list[dict]:
    if not segments: return []
    blocks, current = [], []
    for segment in segments:
        if current:
            gap = float(segment.get("start", 0)) - float(current[-1].get("end", 0))
            elapsed = float(current[-1].get("end", 0)) - float(current[0].get("start", 0))
            recent = " ".join(str(s.get("text", "")) for s in current[-5:])
            incoming = str(segment.get("text", ""))
            first = set(_words(incoming)[:4])
            semantic_reset = bool(first & RESET_TERMS) and _similarity(recent, incoming) < .18
            long_pause_reset = gap >= 2.2 and _similarity(recent, incoming) < .12
            if (elapsed >= 35.0 and semantic_reset) or long_pause_reset:
                blocks.append(_make_block(current)); current = []
        current.append(segment)
    if current: blocks.append(_make_block(current))
    return blocks

def _block_score(block: dict) -> float:
    text = block["text"]; normalized = text.lower(); words = _words(text); duration_s = max(1.0, block["end"] - block["start"])
    reaction_hits = sum(1 for p in REACTION_PHRASES if p in normalized); story_hits = sum(1 for t in STORY_TERMS if t in words)
    return min(100.0, min(30.0, len(words)*0.13) + min(25.0, reaction_hits*7 + text.count("!")*2) + min(25.0, story_hits*2.8) + min(15.0, duration_s*0.25) + (5 if _looks_complete(text) else 0))

def _natural_boundary(current: dict, nxt: dict) -> bool:
    if not _looks_complete(current["text"]): return False
    gap = nxt["start"] - current["end"]
    cohesion = _similarity(current["text"], nxt["text"])
    reset = bool(set(_words(nxt["text"])[:5]) & RESET_TERMS)
    return cohesion < .14 and (reset or gap >= 1.0)

def _context_range(blocks: list[dict], index: int, source_duration: float) -> tuple[float, float]:
    main = blocks[index]; start = main["start"]; end = main["end"]
    if index > 0 and (_starts_as_continuation(main["text"]) or main["start"] < 12.0):
        previous = blocks[index-1]
        if end-previous["start"] <= MAX_CLIP_SECONDS: start = previous["start"]
    cursor = index + 1
    while cursor < len(blocks):
        elapsed = end-start; current = blocks[cursor-1]; nxt = blocks[cursor]
        if elapsed >= MIN_CLIP_SECONDS and (_explicit_conclusion(current["text"]) or _natural_boundary(current, nxt)): break
        if nxt["end"]-start > MAX_CLIP_SECONDS: return start, end
        end = nxt["end"]; cursor += 1
    return max(0.0,start), min(source_duration,min(end,start+MAX_CLIP_SECONDS))

def _range_has_ending(blocks: list[dict], start: float, end: float, source_duration: float) -> bool:
    if end-start < MIN_CLIP_SECONDS: return False
    included = [b for b in blocks if b["end"] > start and b["start"] < end]
    if not included: return False
    last = included[-1]
    if _explicit_conclusion(last["text"]): return True
    following = next((b for b in blocks if b["start"] >= end-.1), None)
    if following and _natural_boundary(last, following): return True
    # O fim da janela também pode ser natural quando a última fala fecha uma frase
    # e há margem suficiente para não estarmos simplesmente cortando a captura.
    return _looks_complete(last["text"]) and source_duration-end >= 20.0 and not _starts_as_continuation(last["text"])

def _story_quality(blocks: list[dict], index: int, start: float, end: float, source_duration: float) -> float:
    main=blocks[index]; quality=_block_score(main); first=_words(main["text"])[:8]
    if set(first) & OPENING_TERMS: quality += 10
    if _starts_as_continuation(main["text"]): quality -= 25
    if _range_has_ending(blocks,start,end,source_duration): quality += 30
    else: quality -= 60
    if end-start >= MIN_CLIP_SECONDS: quality += 5
    if source_duration-end < 30.0: quality -= 20
    if start < 10.0: quality -= 20
    return max(0.0,min(100.0,quality))

def choose_top_ranges(segments: list[dict], source_duration: float, limit: int=3) -> list[tuple[float,float,float]]:
    blocks=_topic_blocks(segments,source_duration)
    if not blocks: return []
    candidates=[]
    for i in range(len(blocks)):
        start,end=_context_range(blocks,i,source_duration)
        if end-start > MAX_CLIP_SECONDS or not _range_has_ending(blocks,start,end,source_duration): continue
        candidates.append((_story_quality(blocks,i,start,end,source_duration),start,end))
    candidates.sort(key=lambda x:x[0],reverse=True); selected=[]
    for candidate in candidates:
        _,start,end=candidate
        if any(max(0.0,min(end,ce)-max(start,cs))/max(1.0,min(end-start,ce-cs))>.25 for _,cs,ce in selected): continue
        selected.append(candidate)
        if len(selected)==limit: break
    return selected

def process(url: str, workdir: Path, capture_seconds: int=DEFAULT_CAPTURE_SECONDS) -> list[Clip]:
    validate_source_url(url); workdir.mkdir(parents=True,exist_ok=True); source=workdir/"capture.mkv"
    source_title=download(url,source,max(60,min(capture_seconds,900))); source_duration=duration(source)
    if source_duration<MIN_CLIP_SECONDS: raise RuntimeError(f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. São necessários pelo menos 60s para publicar um corte.")
    _,segments=transcribe(source); ranges=choose_top_ranges(segments,source_duration,3); clips=[]
    if not ranges: raise RuntimeError("Nenhuma história autocontida com final natural foi encontrada. O sistema recusou cortar um assunto claramente em andamento.")
    for rank,(context_score,start,end) in enumerate(ranges,1):
        clip_id=hashlib.sha1(f"{url}:{datetime.now(UTC).isoformat()}:{rank}".encode()).hexdigest()[:12]; raw=workdir/f"{clip_id}.raw.mp4"; clip_path=workdir/f"{clip_id}.mp4"; thumb=workdir/f"{clip_id}.jpg"
        make_clip_range(source,raw,start,end); local=[]
        for s in segments:
            ss=float(s.get("start",0)); se=float(s.get("end",0))
            if se>start and ss<end: local.append({"start":max(0.0,ss-start),"end":min(end-start,se-start),"text":str(s.get("text",""))})
        burn_subtitles(raw,clip_path,local); raw.unlink(missing_ok=True); thumbnail(clip_path,thumb); transcript=" ".join(s["text"] for s in local).strip(); mean,peak=audio_metrics(clip_path); a_score,a_reasons=audio_score(mean,peak); t_score,t_reasons=transcript_score(transcript); s_score=scene_score(clip_path); base=combine_scores(a_score,t_score,s_score); base.total=round(min(100.0,base.total*.60+context_score*.40),2); description,hashtags=suggest_metadata(transcript)
        reasons=a_reasons+t_reasons+[f"história {context_score:.0f}/100",f"cena {s_score:.0f}/100",f"duração {end-start:.1f}s","final natural confirmado","legendado"]; breakdown={"audio":round(a_score,2),"transcript":round(t_score,2),"scene":round(s_score,2),"context":round(context_score,2)}
        clip=Clip(id=clip_id,title=source_title,source_title=source_title,score=base,score_breakdown=breakdown,duration=round(end-start,2),source_url=url,asset_url="",thumbnail_url="",transcript=transcript,reasons=reasons,description=description,hashtags=hashtags,created_at=datetime.now(UTC).isoformat()); upsert_clip(RANKING_PATH,clip); clips.append(clip)
    clips.sort(key=lambda c:c.score.total,reverse=True); return clips[:3]

def main()->None:
    parser=argparse.ArgumentParser(); parser.add_argument("--url",required=True); parser.add_argument("--capture-seconds",type=int,default=DEFAULT_CAPTURE_SECONDS); parser.add_argument("--workdir",default="work"); args=parser.parse_args(); clips=process(args.url,Path(args.workdir),args.capture_seconds); Path(args.workdir,"result.json").write_text(json.dumps({"clips":[c.to_dict() for c in clips]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__": main()
