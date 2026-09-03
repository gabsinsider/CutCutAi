"""Worker contínuo para acompanhar lives longas em janelas sucessivas.

A inteligência de seleção permanece em ``cutai.pipeline``. Este módulo cuida do
ciclo de vida: processa uma janela, preserva estado e continua enquanto a fonte
estiver disponível. Em produção, o processo deve rodar em um serviço/worker
persistente, não em GitHub Actions.
"""
from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class WorkerState:
    url: str
    status: str = "starting"
    windows_processed: int = 0
    started_at: str = ""
    last_window_at: str | None = None
    last_error: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_state(path: Path, state: WorkerState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _pipeline_command(url: str, capture_seconds: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "cutai.pipeline",
        "--url",
        url,
        "--capture-seconds",
        str(capture_seconds),
    ]


def run(url: str, capture_seconds: int, interval_seconds: int, state_path: Path,
        max_failures: int = 6) -> int:
    if capture_seconds < 180:
        raise ValueError("capture_seconds deve ser >= 180 para análise contextual")
    if interval_seconds < 0:
        raise ValueError("interval_seconds deve ser >= 0")

    state = WorkerState(url=url, started_at=_now())
    stop = False

    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    failures = 0

    while not stop:
        state.status = "processing"
        state.last_window_at = _now()
        _write_state(state_path, state)
        result = subprocess.run(_pipeline_command(url, capture_seconds))

        if result.returncode == 0:
            failures = 0
            state.windows_processed += 1
            state.last_error = None
            state.status = "watching"
            _write_state(state_path, state)
        else:
            failures += 1
            state.last_error = f"pipeline exit code {result.returncode}"
            state.status = "retrying" if failures < max_failures else "ended"
            _write_state(state_path, state)
            if failures >= max_failures:
                return result.returncode

        if not stop and interval_seconds:
            deadline = time.monotonic() + interval_seconds
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1.0, deadline - time.monotonic()))

    state.status = "stopped"
    _write_state(state_path, state)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Acompanha uma live continuamente e processa janelas sucessivas")
    parser.add_argument("--url", required=True)
    parser.add_argument("--capture-seconds", type=int, default=600,
                        help="duração de cada janela; padrão 10 minutos")
    parser.add_argument("--interval-seconds", type=int, default=2,
                        help="espera entre janelas")
    parser.add_argument("--max-failures", type=int, default=6,
                        help="falhas consecutivas antes de considerar a live encerrada")
    parser.add_argument("--state", type=Path, default=Path("work/live-worker.json"))
    args = parser.parse_args()
    raise SystemExit(run(args.url, args.capture_seconds, args.interval_seconds,
                         args.state, args.max_failures))


if __name__ == "__main__":
    main()
