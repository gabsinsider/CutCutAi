"""Consumidor resiliente e observável do buffer contínuo."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from .pipeline import process_source
from .stream_capture import ready_segments


def _log(message: str) -> None:
    print(f"[stream-analyzer] {message}", flush=True)


def _concat(segments: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(".txt")
    manifest.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    result = subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(output)
    ])
    manifest.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError("Falha ao montar janela contínua")


def _load_cursor(path: Path) -> int:
    try:
        return max(0, int(json.loads(path.read_text()).get("cursor", 0)))
    except (ValueError, OSError, TypeError, json.JSONDecodeError):
        return 0


def _save(path: Path, **data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def analyze_once(url: str, stream_dir: Path, workdir: Path, cursor: int,
                 needed: int, overlap: int, final: bool = False) -> tuple[int, bool]:
    segments = ready_segments(stream_dir, 0.5 if final else 2.0)
    # O volume é persistente entre deploys. Se um cursor antigo apontar além dos
    # segmentos existentes (por troca/reinício da captura), recuperamos do início.
    if cursor > len(segments):
        _log(f"cursor antigo {cursor} > {len(segments)} segmentos; reiniciando cursor")
        cursor = 0

    available = len(segments) - cursor
    state = workdir / "stream-analyzer.json"
    _save(state, cursor=cursor, status="waiting" if available < needed else "ready",
          total_segments=len(segments), available_segments=available,
          needed_segments=needed, final=final)

    take = needed if available >= needed else (available if final and available >= 2 else 0)
    if not take:
        return cursor, False

    selected = segments[cursor:cursor + take]
    first, last = selected[0].stem, selected[-1].stem
    window = workdir / f"window-{first}-{last}.mkv"
    analysis_dir = workdir / f"analysis-{first}-{last}"
    _log(f"montando janela {first}..{last} com {take} segmentos")
    _concat(selected, window)
    _save(state, cursor=cursor, status="analyzing", total_segments=len(segments),
          available_segments=available, needed_segments=needed,
          first_segment=first, last_segment=last)
    try:
        clips = process_source(window, url, analysis_dir, "Live contínua")
        _log(f"janela analisada: {len(clips)} corte(s) gerado(s)")
    except Exception as exc:
        # Uma janela ruim não pode matar o consumidor de uma live de muitas horas.
        _log(f"erro na janela {first}..{last}: {type(exc).__name__}: {exc}")
        _save(state, cursor=cursor, status="analysis_error", error=str(exc),
              first_segment=first, last_segment=last)
        advance = max(1, take - overlap)
        return cursor + advance, True
    finally:
        window.unlink(missing_ok=True)

    advance = take if final and take < needed else max(1, take - overlap)
    cursor += advance
    _save(state, cursor=cursor, last_segment=last,
          status="drained" if final else "watching",
          total_segments=len(segments), needed_segments=needed)
    return cursor, True


def run(url: str, stream_dir: Path, workdir: Path, segment_seconds: int = 30,
        window_seconds: int = 600, overlap_seconds: int = 90,
        poll_seconds: int = 5, stop_file: Path | None = None) -> None:
    needed = max(2, window_seconds // segment_seconds)
    overlap = max(1, overlap_seconds // segment_seconds)
    workdir.mkdir(parents=True, exist_ok=True)
    state = workdir / "stream-analyzer.json"
    cursor = _load_cursor(state)
    last_report = 0.0
    _log(f"iniciado: precisa de {needed} segmentos, overlap={overlap}, cursor={cursor}")

    while True:
        final = bool(stop_file and stop_file.exists())
        try:
            cursor, worked = analyze_once(url, stream_dir, workdir, cursor, needed, overlap, final)
        except Exception as exc:
            _log(f"erro recuperável no consumidor: {type(exc).__name__}: {exc}")
            _save(state, cursor=cursor, status="consumer_error", error=str(exc), final=final)
            worked = False

        if final and not worked:
            _save(state, cursor=cursor, status="finished")
            _log("buffer drenado; finalizando")
            return

        now = time.monotonic()
        if not worked and now - last_report >= 30:
            segments = ready_segments(stream_dir)
            available = max(0, len(segments) - cursor)
            _log(f"aguardando buffer: {available}/{needed} segmentos disponíveis (total={len(segments)}, cursor={cursor})")
            last_report = now
        if not worked:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--stream-dir", type=Path, default=Path("work/stream"))
    parser.add_argument("--workdir", type=Path, default=Path("work/live-analysis"))
    parser.add_argument("--segment-seconds", type=int, default=30)
    parser.add_argument("--window-seconds", type=int, default=600)
    parser.add_argument("--overlap-seconds", type=int, default=90)
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--stop-file", type=Path, default=None)
    args = parser.parse_args()
    run(args.url, args.stream_dir, args.workdir, args.segment_seconds,
        args.window_seconds, args.overlap_seconds, args.poll_seconds, args.stop_file)


if __name__ == "__main__":
    main()
