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
MAX_CLIP_SECONDS = 120.0
RANKING_PATH = Path("data/ranking.json")


def download(url: str, output: Path, seconds: int) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    base = [
        "yt-dlp", "--no-playlist", "--no-progress", "--no-simulate",
        "--impersonate", "chrome", "--extractor-retries", "5", "--fragment-retries", "5",
        "--retry-sleep", "extractor:2", "--js-runtimes", "node",
        "--downloader", "ffmpeg", "--downloader-args", f"ffmpeg_i:-t {seconds}",
        "--merge-output-format", "mkv",
        "-f", "bestvideo[height=1080][fps<=30]+bestaudio/bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080][fps<=30]/best",
        "-o", str(output), "--print", "title",
    ]
    proxy_url = normalize_proxy_url(os.getenv("CUTAI_PROXY_URL", ""))
    cookie_file = os.getenv("CUTAI_YOUTUBE_COOKIES_FILE", "").strip()
    user_agent = os.getenv("CUTAI_YOUTUBE_USER_AGENT", "").strip()
    attempts = [
        "youtube:player_client=tv,web_safari;formats=missing_pot",
        "youtube:player_client=web_safari,android;formats=missing_pot",
        "youtube:player_client=default,android;formats=missing_pot",
    ]
    errors: list[str] = []
    for extractor_args in attempts:
        command = base + ["--extractor-args", extractor_args]
        if cookie_file and Path(cookie_file).exists():
            command += ["--cookies", cookie_file]
        if user_agent:
            command += ["--user-agent", user_agent]
        if proxy_url:
            command += ["--proxy", proxy_url]
        command.append(url)
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "Live"
        detail = result.stderr.strip()[-1600:] or result.stdout.strip()[-1600:]
        if proxy_url:
            detail = detail.replace(proxy_url, "[proxy protegido]")
        errors.append(detail)
        output.unlink(missing_ok=True)
    raise RuntimeError(
        "yt-dlp não conseguiu acessar esta transmissão após tentar múltiplos clientes do YouTube:\n"
        + "\n--- tentativa seguinte ---\n".join(errors)
    )


REACTION_PHRASES = {
    "meu deus", "não acredito", "que isso", "que loucura", "que absurdo",
    "olha isso", "olha só", "caramba", "nossa", "incrível", "absurdo",
    "sensacional", "impressionante", "histórico", "golaço", "gol",
}
STORY_TERMS = {
    "porque", "então", "mas", "porém", "aconteceu", "depois", "antes",
    "agora", "resultado", "problema", "verdade", "segredo", "explicar",
    "entender", "motivo", "finalmente", "conseguiu", "ganhou", "perdeu",
}
CONTINUATION_ENDINGS = {
    "e", "mas", "porque", "pois", "que", "quando", "então", "porém", "só",
    "se", "como", "para", "pra", "de", "do", "da", "dos", "das", "com",
}


def _looks_complete(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    last_word = re.findall(r"[\wÀ-ÿ]+", text.lower())
    if not last_word or last_word[-1] in CONTINUATION_ENDINGS:
        return False
    return text.endswith((".", "!", "?"))


def _topic_blocks(segments: list[dict], source_duration: float) -> list[dict]:
    """Agrupa legendas curtas em unidades de conversa antes de escolher cortes."""
    if not segments:
        return []
    blocks: list[dict] = []
    current: list[dict] = []
    for segment in segments:
        if current:
            gap = float(segment.get("start", 0)) - float(current[-1].get("end", 0))
            elapsed = float(current[-1].get("end", 0)) - float(current[0].get("start", 0))
            # Pausa longa ou bloco já substancial indica mudança provável de assunto.
            if gap >= 1.6 or (elapsed >= 35.0 and gap >= 0.75):
                blocks.append(_make_block(current))
                current = []
        current.append(segment)
    if current:
        blocks.append(_make_block(current))

    # Blocos minúsculos isolados não têm contexto suficiente: una ao vizinho.
    merged: list[dict] = []
    for block in blocks:
        if merged and block["end"] - block["start"] < 12.0 and block["start"] - merged[-1]["end"] < 3.0:
            merged[-1]["end"] = block["end"]
            merged[-1]["segments"].extend(block["segments"])
            merged[-1]["text"] += " " + block["text"]
        else:
            merged.append(block)
    return merged


def _make_block(items: list[dict]) -> dict:
    return {
        "start": float(items[0].get("start", 0)),
        "end": float(items[-1].get("end", 0)),
        "segments": list(items),
        "text": " ".join(str(s.get("text", "")) for s in items).strip(),
    }


def _block_score(block: dict) -> float:
    text = block["text"]
    normalized = text.lower()
    words = re.findall(r"[\wÀ-ÿ]+", normalized)
    duration_s = max(1.0, block["end"] - block["start"])
    reaction_hits = sum(1 for phrase in REACTION_PHRASES if phrase in normalized)
    story_hits = sum(1 for term in STORY_TERMS if term in words)
    score = min(30.0, len(words) * 0.13)
    score += min(25.0, reaction_hits * 7.0 + text.count("!") * 2.0)
    score += min(25.0, story_hits * 2.8 + text.count("?") * 2.5)
    score += min(15.0, duration_s * 0.25)
    if _looks_complete(text):
        score += 5.0
    return min(100.0, score)


def _context_range(blocks: list[dict], index: int, source_duration: float) -> tuple[float, float]:
    """Inclui preparação + assunto principal + desfecho, sem começar no meio da ideia."""
    main = blocks[index]
    start = main["start"]
    end = main["end"]

    # Inclua o bloco anterior quando o assunto principal sozinho começa curto demais.
    if index > 0 and (end - start < 45.0 or start - blocks[index - 1]["end"] < 1.0):
        previous = blocks[index - 1]
        if end - previous["start"] <= MAX_CLIP_SECONDS:
            start = previous["start"]

    # Continue pelos blocos seguintes até haver pelo menos 60 s e uma conclusão.
    cursor = index + 1
    while cursor < len(blocks) and end - start < MIN_CLIP_SECONDS:
        nxt = blocks[cursor]
        if nxt["end"] - start > MAX_CLIP_SECONDS:
            break
        end = nxt["end"]
        cursor += 1

    # Mesmo após 60 s, não pare se o bloco final aparenta continuação imediata.
    while cursor < len(blocks) and end - start < MAX_CLIP_SECONDS:
        current_text = blocks[cursor - 1]["text"] if cursor > 0 else main["text"]
        gap = blocks[cursor]["start"] - end
        if _looks_complete(current_text) and gap >= 0.75:
            break
        nxt = blocks[cursor]
        if nxt["end"] - start > MAX_CLIP_SECONDS:
            break
        end = nxt["end"]
        cursor += 1

    if end - start < MIN_CLIP_SECONDS:
        pad = MIN_CLIP_SECONDS - (end - start)
        start = max(0.0, start - min(pad, 12.0))
        end = min(source_duration, max(end, start + MIN_CLIP_SECONDS))
    return max(0.0, start), min(source_duration, min(end, start + MAX_CLIP_SECONDS))


def choose_top_ranges(segments: list[dict], source_duration: float, limit: int = 3) -> list[tuple[float, float, float]]:
    blocks = _topic_blocks(segments, source_duration)
    if not blocks:
        return [(50.0, 0.0, min(source_duration, max(MIN_CLIP_SECONDS, source_duration)))]
    candidates = []
    for index, block in enumerate(blocks):
        start, end = _context_range(blocks, index, source_duration)
        score = _block_score(block)
        candidates.append((score, start, end))
    candidates.sort(key=lambda item: item[0], reverse=True)

    selected: list[tuple[float, float, float]] = []
    for candidate in candidates:
        _, start, end = candidate
        overlap = False
        for _, chosen_start, chosen_end in selected:
            intersection = max(0.0, min(end, chosen_end) - max(start, chosen_start))
            smaller = max(1.0, min(end - start, chosen_end - chosen_start))
            if intersection / smaller > 0.25:
                overlap = True
                break
        if not overlap:
            selected.append(candidate)
            if len(selected) == limit:
                break
    return selected or [candidates[0]]


def process(url: str, workdir: Path, capture_seconds: int = 180) -> list[Clip]:
    validate_source_url(url)
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "capture.mkv"
    source_title = download(url, source, max(60, min(capture_seconds, 900)))
    source_duration = duration(source)
    if source_duration < MIN_CLIP_SECONDS:
        raise RuntimeError(f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. São necessários pelo menos 60s para publicar um corte.")
    _full_transcript, segments = transcribe(source)
    ranges = choose_top_ranges(segments, source_duration, limit=3)
    clips = []
    for rank, (context_score, start, end) in enumerate(ranges, start=1):
        clip_id = hashlib.sha1(f"{url}:{datetime.now(UTC).isoformat()}:{rank}".encode()).hexdigest()[:12]
        raw_clip_path = workdir / f"{clip_id}.raw.mp4"
        clip_path = workdir / f"{clip_id}.mp4"
        thumb_path = workdir / f"{clip_id}.jpg"
        make_clip_range(source, raw_clip_path, start, end)
        local_segments = []
        for segment in segments:
            seg_start = float(segment.get("start", 0))
            seg_end = float(segment.get("end", 0))
            if seg_end > start and seg_start < end:
                local_segments.append({"start": max(0.0, seg_start - start), "end": min(end - start, seg_end - start), "text": str(segment.get("text", ""))})
        burn_subtitles(raw_clip_path, clip_path, local_segments)
        raw_clip_path.unlink(missing_ok=True)
        thumbnail(clip_path, thumb_path)
        transcript = " ".join(s["text"] for s in local_segments).strip()
        mean, peak = audio_metrics(clip_path)
        a_score, a_reasons = audio_score(mean, peak)
        t_score, t_reasons = transcript_score(transcript)
        s_score = scene_score(clip_path)
        base_score = combine_scores(a_score, t_score, s_score)
        base_score.total = round(min(100.0, base_score.total * 0.65 + context_score * 0.35), 2)
        description, hashtags = suggest_metadata(transcript)
        reasons = a_reasons + t_reasons + [f"contexto {context_score:.0f}/100", f"cena {s_score:.0f}/100", f"duração {end-start:.1f}s", "bloco completo de assunto", "legendado"]
        score_breakdown = {"audio": round(a_score, 2), "transcript": round(t_score, 2), "scene": round(s_score, 2), "context": round(context_score, 2)}
        clip = Clip(id=clip_id, title=source_title, source_title=source_title, score=base_score, score_breakdown=score_breakdown, duration=round(end-start, 2), source_url=url, asset_url="", thumbnail_url="", transcript=transcript, reasons=reasons, description=description, hashtags=hashtags, created_at=datetime.now(UTC).isoformat())
        upsert_clip(RANKING_PATH, clip)
        clips.append(clip)
    clips.sort(key=lambda c: c.score.total, reverse=True)
    return clips[:3]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--capture-seconds", type=int, default=180)
    parser.add_argument("--workdir", default="work")
    args = parser.parse_args()
    clips = process(args.url, Path(args.workdir), args.capture_seconds)
    Path(args.workdir, "result.json").write_text(json.dumps({"clips": [clip.to_dict() for clip in clips]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
