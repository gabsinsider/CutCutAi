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
               "-f", "best[height<=1080]/best", "-o", str(output), "--print", "title"]
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


def process(url: str, workdir: Path, ranking_path: Path, capture_seconds: int = 180) -> Clip:
    url, _ = validate_source_url(url)
    # MPEG-TS tolerates timestamp discontinuities common in TikTok/HLS lives.
    source = workdir / "capture.ts"
    source_title = download(url, source, min(max(capture_seconds, 60), 900))
    source_duration = duration(source)
    if source_duration < 45:
        raise RuntimeError(
            f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. "
            "O corte não será publicado; tente uma live estável e ativa."
        )
    transcript, _ = transcribe(source, os.getenv("WHISPER_MODEL", "tiny"))
    rms_mean, rms_peak = audio_metrics(source)
    a_score, a_reasons = audio_score(rms_mean, rms_peak)
    t_score, t_reasons = transcript_score(transcript)
    s_score = scene_score(source)
    score = combine_scores(a_score, t_score, s_score, a_reasons + t_reasons)
    clip_id = hashlib.sha256(f"{url}-{datetime.now(UTC).isoformat()}".encode()).hexdigest()[:12]
    clip_path = workdir / f"{clip_id}.mp4"
    center = max(30, source_duration / 2)
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
