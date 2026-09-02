import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .media import audio_metrics, duration, make_clip, scene_score, thumbnail
from .metadata import suggest_metadata
from .models import Clip
from .proxy import normalize_proxy_url
from .ranking import upsert_clip
from .scoring import audio_score, combine_scores, transcript_score
from .transcription import transcribe
from .validation import validate_source_url


def download(url: str, output: Path, seconds: int) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["yt-dlp", "--no-playlist", "--no-progress", "--no-simulate", "--impersonate", "chrome",
               "--extractor-retries", "3", "--js-runtimes", "node",
               "--extractor-args", "youtube:player_client=default,android;formats=missing_pot",
               "--downloader", "ffmpeg", "--downloader-args", f"ffmpeg_i:-t {seconds}",
               "--merge-output-format", "mkv",
               "-f", (
                   "bestvideo[height<=1080][fps<=30]+bestaudio/"
                   "best[height<=1080][fps<=30]/"
                   "bestvideo[height<=720][fps<=30]+bestaudio/"
                   "best[height<=720]/best"
               ), "-o", str(output), "--print", "title"]
    proxy_url = normalize_proxy_url(os.getenv("CUTAI_PROXY_URL", ""))
    if proxy_url:
        command.extend(["--proxy", proxy_url])
    command.append(url)
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip()[-3000:] or result.stdout.strip()[-3000:]
        if proxy_url:
            detail = detail.replace(proxy_url, "[proxy protegido]")
        raise RuntimeError(f"yt-dlp não conseguiu acessar esta transmissão:\n{detail}")
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "Live"


def _segment_excitement(text: str) -> float:
    """Lightweight semantic signal for moments that tend to work as short clips."""
    normalized = text.lower()
    keywords = {
        "gol", "goal", "golaço", "incrível", "absurdo", "caramba", "nossa", "wow",
        "olha", "atenção", "agora", "mano", "meu deus", "não acredito", "que isso",
        "risada", "haha", "kkkk", "vitória", "ganhou", "perdeu", "recorde", "melhor",
    }
    score = sum(1.0 for word in keywords if word in normalized)
    score += min(2.0, text.count("!") * 0.5 + text.count("?") * 0.25)
    # Very short fragments are usually poor standalone clips.
    if len(text.split()) < 4:
        score *= 0.5
    return score


def choose_best_center(source: Path, segments: list[dict], source_duration: float, clip_length: int = 60) -> float:
    """Choose the strongest 60s window using speech density, excitement and scene activity."""
    if source_duration <= clip_length:
        return source_duration / 2
    if not segments:
        return max(clip_length / 2, source_duration / 2)

    half = clip_length / 2
    step = 10.0
    first = half
    last = max(first, source_duration - half)
    candidates = []
    center = first
    while center <= last + 0.01:
        start, end = center - half, center + half
        inside = [s for s in segments if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end]
        speech_seconds = sum(max(0.0, min(end, float(s.get("end", 0))) - max(start, float(s.get("start", 0)))) for s in inside)
        words = sum(len(str(s.get("text", "")).split()) for s in inside)
        excitement = sum(_segment_excitement(str(s.get("text", ""))) for s in inside)
        # Reward active, understandable speech and expressive moments; avoid silence-heavy windows.
        score = min(30.0, speech_seconds * 0.5) + min(25.0, words * 0.12) + min(35.0, excitement * 5.0)
        candidates.append((score, center))
        center += step

    # Inspect visual activity only for the best semantic candidates to keep Actions fast.
    candidates.sort(reverse=True)
    best_score, best_center = candidates[0]
    for semantic_score, candidate_center in candidates[:3]:
        probe = source.parent / f"candidate-{int(candidate_center)}.mp4"
        try:
            make_clip(source, probe, candidate_center, length=clip_length)
            visual = scene_score(probe)
            total = semantic_score + visual * 0.15
            if total > best_score:
                best_score, best_center = total, candidate_center
        finally:
            probe.unlink(missing_ok=True)
    return best_center


def process(url: str, workdir: Path, ranking_path: Path, capture_seconds: int = 180) -> Clip:
    url, _ = validate_source_url(url)
    source = workdir / "capture.mkv"
    source_title = download(url, source, min(max(capture_seconds, 60), 900))
    source_duration = duration(source)
    if source_duration < 45:
        raise RuntimeError(
            f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. "
            "O corte não será publicado; tente uma live estável e ativa."
        )
    transcript, segments = transcribe(source, os.getenv("WHISPER_MODEL", "tiny"))
    rms_mean, rms_peak = audio_metrics(source)
    a_score, a_reasons = audio_score(rms_mean, rms_peak)
    t_score, t_reasons = transcript_score(transcript)
    s_score = scene_score(source)
    score = combine_scores(a_score, t_score, s_score, a_reasons + t_reasons)
    clip_id = hashlib.sha256(f"{url}-{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:12]
    clip_path = workdir / f"{clip_id}.mp4"
    center = choose_best_center(source, segments, source_duration)
    make_clip(source, clip_path, center)
    thumb_path = workdir / f"{clip_id}.jpg"
    thumbnail(clip_path, thumb_path)
    description, hashtags = suggest_metadata(transcript)
    clip = Clip(
        id=clip_id, title=description[:80], source_url=url, source_title=source_title,
        created_at=datetime.now(UTC).isoformat(), duration=duration(clip_path), score=score.total,
        score_breakdown={"audio": score.audio, "transcript": score.transcript, "scene": score.scene},
        transcript=transcript, description=description, hashtags=hashtags, reasons=score.reasons,
    )
    upsert_clip(ranking_path, clip)
    (workdir / "result.json").write_text(json.dumps(clip.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return clip


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--workdir", type=Path, default=Path("work"))
    parser.add_argument("--ranking", type=Path, default=Path("data/ranking.json"))
    parser.add_argument("--capture-seconds", type=int, default=180)
    args = parser.parse_args()
    print(json.dumps(process(args.url, args.workdir, args.ranking, args.capture_seconds).to_dict(), ensure_ascii=False))


if __name__ == "__main__":
    main()
