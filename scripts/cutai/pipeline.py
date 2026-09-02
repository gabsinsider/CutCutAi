import argparse
import hashlib
import json
import os
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
MAX_CLIP_SECONDS = 90.0
RANKING_PATH = Path("data/ranking.json")


def download(url: str, output: Path, seconds: int) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)

    # Prefer adaptive 1080p video + best audio. yt-dlp hands both URLs to one ffmpeg
    # process, preserving their original timestamps while clipping the same live window.
    # Fall back to a muxed stream when adaptive formats are unavailable.
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


def _segment_excitement(text: str) -> float:
    normalized = text.lower()
    keywords = {"gol", "goal", "golaço", "incrível", "absurdo", "caramba", "nossa", "wow", "olha", "atenção", "agora", "mano", "meu deus", "não acredito", "que isso", "risada", "haha", "kkkk", "vitória", "ganhou", "perdeu", "recorde", "melhor"}
    score = sum(1.0 for word in keywords if word in normalized)
    score += min(2.0, text.count("!") * 0.5 + text.count("?") * 0.25)
    return score if len(text.split()) >= 4 else score * 0.5


def choose_top_centers(segments: list[dict], source_duration: float, clip_length: int = 60, limit: int = 3) -> list[tuple[float, float]]:
    if source_duration <= clip_length:
        return [(50.0, source_duration / 2)]
    half = clip_length / 2
    candidates = []
    center = half
    last = max(half, source_duration - half)
    while center <= last + 0.01:
        start, end = center - half, center + half
        inside = [s for s in segments if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end]
        speech_seconds = sum(max(0.0, min(end, float(s.get("end", 0))) - max(start, float(s.get("start", 0)))) for s in inside)
        words = sum(len(str(s.get("text", "")).split()) for s in inside)
        excitement = sum(_segment_excitement(str(s.get("text", ""))) for s in inside)
        score = min(30.0, speech_seconds * 0.5) + min(25.0, words * 0.12) + min(35.0, excitement * 5.0)
        candidates.append((score, center))
        center += 10.0
    candidates.sort(reverse=True)
    selected = []
    for candidate in candidates:
        if all(abs(candidate[1] - chosen[1]) >= clip_length * 0.75 for chosen in selected):
            selected.append(candidate)
            if len(selected) == limit:
                break
    if len(selected) < limit:
        for candidate in candidates:
            if candidate not in selected and all(abs(candidate[1] - chosen[1]) >= clip_length * 0.5 for chosen in selected):
                selected.append(candidate)
                if len(selected) == limit:
                    break
    return selected or [(0.0, source_duration / 2)]


def choose_smart_range(segments: list[dict], center: float, source_duration: float) -> tuple[float, float]:
    core_start = max(0.0, center - MIN_CLIP_SECONDS / 2)
    core_end = min(source_duration, core_start + MIN_CLIP_SECONDS)
    core_start = max(0.0, core_end - MIN_CLIP_SECONDS)
    if not segments:
        return core_start, core_end
    start, end = core_start, core_end
    before = [s for s in segments if float(s.get("start", 0)) <= core_start and core_start - float(s.get("start", 0)) <= 12.0]
    if before:
        start = float(before[-1].get("start", core_start))
    after = [s for s in segments if float(s.get("end", 0)) >= core_end and float(s.get("end", 0)) - core_end <= 18.0]
    if after:
        end = float(after[0].get("end", core_end))
    if end - start < MIN_CLIP_SECONDS:
        missing = MIN_CLIP_SECONDS - (end - start)
        grow_after = min(missing, source_duration - end)
        end += grow_after
        start = max(0.0, start - (missing - grow_after))
    if end - start > MAX_CLIP_SECONDS:
        end = start + MAX_CLIP_SECONDS
        if end > source_duration:
            end = source_duration
            start = max(0.0, end - MAX_CLIP_SECONDS)
    return start, end


def process(url: str, workdir: Path, capture_seconds: int = 180) -> list[Clip]:
    validate_source_url(url)
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "capture.mkv"
    source_title = download(url, source, max(60, min(capture_seconds, 900)))
    source_duration = duration(source)
    if source_duration < MIN_CLIP_SECONDS:
        raise RuntimeError(f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. São necessários pelo menos 60s para publicar um corte.")
    _full_transcript, segments = transcribe(source)
    centers = choose_top_centers(segments, source_duration, clip_length=60, limit=3)
    clips = []
    for rank, (_, center) in enumerate(centers, start=1):
        start, end = choose_smart_range(segments, center, source_duration)
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
        score = combine_scores(a_score, t_score, s_score)
        description, hashtags = suggest_metadata(transcript)
        reasons = a_reasons + t_reasons + [f"cena {s_score:.0f}/100", f"duração {end-start:.1f}s", "legendado"]
        score_breakdown = {"audio": round(a_score, 2), "transcript": round(t_score, 2), "scene": round(s_score, 2)}
        clip = Clip(id=clip_id, title=source_title, source_title=source_title, score=score, score_breakdown=score_breakdown, duration=round(end-start, 2), source_url=url, asset_url="", thumbnail_url="", transcript=transcript, reasons=reasons, description=description, hashtags=hashtags, created_at=datetime.now(UTC).isoformat())
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
