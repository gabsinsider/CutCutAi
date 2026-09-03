"""Supervisor resiliente do modo de live longa do CutCutAi."""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(path: Path, **data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _terminate(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _capture_cmd(url: str, stream_dir: Path, segment: int) -> list[str]:
    return [sys.executable, "-m", "cutai.stream_capture", "--url", url,
            "--output-dir", str(stream_dir), "--segment-seconds", str(segment)]


def _analyzer_cmd(url: str, stream_dir: Path, analysis_dir: Path, segment: int,
                  window: int, overlap: int, stop: Path) -> list[str]:
    return [sys.executable, "-m", "cutai.stream_analyzer", "--url", url,
            "--stream-dir", str(stream_dir), "--workdir", str(analysis_dir),
            "--segment-seconds", str(segment), "--window-seconds", str(window),
            "--overlap-seconds", str(overlap), "--stop-file", str(stop)]


def run(url: str, root: Path, segment: int = 30, window: int = 600,
        overlap: int = 90, restarts: int = 12) -> int:
    stream = root / "stream"
    analysis = root / "analysis"
    stop = root / "capture-ended"
    state = root / "supervisor.json"
    stream.mkdir(parents=True, exist_ok=True)
    stop.unlink(missing_ok=True)
    shutdown = False
    started_at = _now()
    disconnects = 0
    consecutive_failures = 0
    capture: subprocess.Popen | None = None
    analyzer: subprocess.Popen | None = None

    def request(*_: object) -> None:
        nonlocal shutdown
        shutdown = True

    signal.signal(signal.SIGINT, request)
    signal.signal(signal.SIGTERM, request)

    analyzer = subprocess.Popen(_analyzer_cmd(url, stream, analysis, segment, window, overlap, stop))
    _write(state, status="starting", url=url, started_at=started_at,
           capture_restarts=0, analyzer_pid=analyzer.pid)

    try:
        while not shutdown:
            # O analisador também é um processo de longa duração. Se cair enquanto a
            # transmissão segue ativa, reiniciamos sem interromper a captura.
            if analyzer.poll() is not None:
                analyzer = subprocess.Popen(_analyzer_cmd(url, stream, analysis, segment, window, overlap, stop))

            capture_started = time.monotonic()
            capture = subprocess.Popen(_capture_cmd(url, stream, segment))
            _write(state, status="capturing", url=url, started_at=started_at,
                   capture_restarts=disconnects, capture_pid=capture.pid,
                   analyzer_pid=analyzer.pid, connected_at=_now())
            code = capture.wait()
            lived_for = time.monotonic() - capture_started
            if shutdown:
                break

            disconnects += 1
            # Uma conexão que ficou viva por algum tempo é uma queda transitória, não
            # uma sequência de falhas de inicialização. Ela zera o contador de falhas.
            if lived_for >= max(30, segment):
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            _write(state, status="reconnecting", url=url, started_at=started_at,
                   last_disconnect=_now(), capture_exit=code,
                   last_connection_seconds=round(lived_for, 1),
                   capture_restarts=disconnects,
                   consecutive_failures=consecutive_failures,
                   analyzer_pid=analyzer.pid if analyzer.poll() is None else None)

            # Não encerramos uma live longa só porque a mídia caiu. O limite serve
            # apenas para falhas consecutivas muito rápidas (URL inválida/live encerrada).
            if consecutive_failures > restarts:
                _write(state, status="draining", url=url, started_at=started_at,
                       ended_at=_now(), capture_exit=code,
                       capture_restarts=disconnects,
                       consecutive_failures=consecutive_failures)
                break

            delay = min(20, 2 + consecutive_failures * 3)
            time.sleep(delay)

        stop.touch()
        if analyzer and analyzer.poll() is None:
            try:
                analyzer.wait(timeout=max(180, window * 2))
            except subprocess.TimeoutExpired:
                _terminate(analyzer)
        final = "stopped" if shutdown else "finished"
        analyzer_exit = analyzer.poll() if analyzer else None
        _write(state, status=final, url=url, started_at=started_at, ended_at=_now(),
               capture_restarts=disconnects, analyzer_exit=analyzer_exit)
        return 0 if analyzer_exit in (0, None) else int(analyzer_exit)
    finally:
        _terminate(capture)
        stop.touch()
        _terminate(analyzer)


def main() -> None:
    parser = argparse.ArgumentParser(description="Supervisor persistente de live")
    parser.add_argument("--url", required=True)
    parser.add_argument("--root", type=Path, default=Path("work/continuous-live"))
    parser.add_argument("--segment-seconds", type=int, default=30)
    parser.add_argument("--window-seconds", type=int, default=600)
    parser.add_argument("--overlap-seconds", type=int, default=90)
    parser.add_argument("--capture-restarts", type=int, default=12)
    args = parser.parse_args()
    raise SystemExit(run(args.url, args.root, args.segment_seconds,
                         args.window_seconds, args.overlap_seconds,
                         args.capture_restarts))


if __name__ == "__main__":
    main()
