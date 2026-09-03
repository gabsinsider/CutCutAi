"""Captura contínua de lives com autenticação opcional.

Para YouTube público, o caminho padrão é anônimo. Cookies de conta ficam apenas
como fallback explícito para conteúdo que realmente exige login.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import time
from pathlib import Path

from .proxy import normalize_proxy_url
from .validation import validate_source_url


def build_command(url: str, output_dir: Path, segment_seconds: int) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / "segment-%08d.mkv"

    # Deixe o yt-dlp resolver a URL da mídia e entregue o fluxo ao ffmpeg.
    # A segmentação pertence ao ffmpeg; usar o template segment-%d diretamente
    # como saída do yt-dlp faz o downloader acrescentar `.part` e quebra o muxer.
    resolver = [
        "yt-dlp", "--no-playlist", "--no-progress",
        "--extractor-retries", "5", "--fragment-retries", "10",
        "--retry-sleep", "extractor:2",
        "-f", "best[height<=1080][fps<=30]/best",
        "-g",
    ]

    cookie_file = os.getenv("CUTAI_YOUTUBE_COOKIES_FILE", "").strip()
    use_cookies = os.getenv("CUTAI_USE_YOUTUBE_COOKIES", "").strip().lower() in {"1", "true", "yes"}
    user_agent = os.getenv("CUTAI_YOUTUBE_USER_AGENT", "").strip()
    proxy_url = normalize_proxy_url(os.getenv("CUTAI_PROXY_URL", ""))
    if use_cookies and cookie_file and Path(cookie_file).exists():
        resolver += ["--cookies", cookie_file]
    if user_agent:
        resolver += ["--user-agent", user_agent]
    if proxy_url:
        resolver += ["--proxy", proxy_url]
    resolver += ["--extractor-args", "youtube:player_client=web_safari,mweb;formats=missing_pot", url]

    media_url = subprocess.check_output(resolver, text=True).strip().splitlines()[0]
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-i", media_url,
        "-map", "0:v?", "-map", "0:a?", "-c", "copy",
        "-f", "segment", "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1", str(pattern),
    ]


def capture(url: str, output_dir: Path, segment_seconds: int = 30) -> int:
    validate_source_url(url)
    if segment_seconds < 10 or segment_seconds > 120:
        raise ValueError("segment_seconds deve ficar entre 10 e 120")
    process = subprocess.Popen(build_command(url, output_dir, segment_seconds))
    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        if stopping:
            return
        stopping = True
        process.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def ready_segments(output_dir: Path, settle_seconds: float = 2.0) -> list[Path]:
    """Retorna somente segmentos que pararam de crescer; o último pode estar aberto."""
    now = time.time()
    files = sorted(output_dir.glob("segment-*.mkv"))
    return [p for p in files if now - p.stat().st_mtime >= settle_seconds]


def main() -> None:
    parser = argparse.ArgumentParser(description="Captura contínua segmentada de uma live")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("work/stream"))
    parser.add_argument("--segment-seconds", type=int, default=30)
    args = parser.parse_args()
    raise SystemExit(capture(args.url, args.output_dir, args.segment_seconds))


if __name__ == "__main__":
    main()
