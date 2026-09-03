"""Orquestrador persistente para lives longas.

O worker acompanha a transmissão em janelas sucessivas com uma pequena área de
sobreposição contextual. O pipeline aprovado continua responsável pela seleção
e pelos cortes; este módulo mantém cursor, estado e deduplicação entre janelas.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import signal
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
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
    timeline_seconds: float = 0.0
    overlap_seconds: int = 45
    emitted_clip_ids: list[str] = field(default_factory=list)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _source_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _write_state(path: Path, state: WorkerState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_state(path: Path, url: str, overlap_seconds: int) -> WorkerState:
    if not path.exists():
        return WorkerState(url=url, started_at=_now(), overlap_seconds=overlap_seconds)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("url") != url:
            return WorkerState(url=url, started_at=_now(), overlap_seconds=overlap_seconds)
        allowed = {f.name for f in WorkerState.__dataclass_fields__.values()}
        state = WorkerState(**{k: v for k, v in data.items() if k in allowed})
        state.overlap_seconds = overlap_seconds
        return state
    except (OSError, ValueError, TypeError):
        return WorkerState(url=url, started_at=_now(), overlap_seconds=overlap_seconds)


def _pipeline_command(url: str, capture_seconds: int) -> list[str]:
    return [sys.executable, "-m", "cutai.pipeline", "--url", url,
            "--capture-seconds", str(capture_seconds)]


def _ranking_ids(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(c.get("id")) for c in data.get("clips", []) if c.get("id")}
    except (OSError, ValueError, TypeError):
        return set()


def run(url: str, capture_seconds: int, overlap_seconds: int, interval_seconds: int,
        state_path: Path, ranking_path: Path, max_failures: int = 6) -> int:
    if capture_seconds < 180:
        raise ValueError("capture_seconds deve ser >= 180 para análise contextual")
    if overlap_seconds < 0 or overlap_seconds >= capture_seconds:
        raise ValueError("overlap_seconds deve estar entre 0 e capture_seconds-1")
    if interval_seconds < 0:
        raise ValueError("interval_seconds deve ser >= 0")

    state = _load_state(state_path, url, overlap_seconds)
    stop = False

    def request_stop(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    failures = 0
    step_seconds = capture_seconds - overlap_seconds

    while not stop:
        before = _ranking_ids(ranking_path)
        state.status = "processing"
        state.last_window_at = _now()
        _write_state(state_path, state)

        result = subprocess.run(_pipeline_command(url, capture_seconds))
        if result.returncode == 0:
            after = _ranking_ids(ranking_path)
            new_ids = sorted(after - before - set(state.emitted_clip_ids))
            state.emitted_clip_ids = (state.emitted_clip_ids + new_ids)[-1000:]
            failures = 0
            state.windows_processed += 1
            state.timeline_seconds += step_seconds
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

        # O intervalo deve ser mínimo: a própria captura consome aproximadamente
        # capture_seconds. A sobreposição existe para não cortar uma história que
        # começou no final da janela anterior. IDs já emitidos ficam registrados.
        if not stop and interval_seconds:
            deadline = time.monotonic() + interval_seconds
            while not stop and time.monotonic() < deadline:
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))

    state.status = "stopped"
    _write_state(state_path, state)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Acompanha uma live continuamente")
    parser.add_argument("--url", required=True)
    parser.add_argument("--capture-seconds", type=int, default=600,
                        help="janela de análise; padrão 10 minutos")
    parser.add_argument("--overlap-seconds", type=int, default=45,
                        help="contexto lógico preservado entre janelas")
    parser.add_argument("--interval-seconds", type=int, default=1,
                        help="pequena espera antes da próxima captura")
    parser.add_argument("--max-failures", type=int, default=6)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--ranking", type=Path, default=Path("data/ranking.json"))
    args = parser.parse_args()
    state = args.state or Path("work") / f"live-{_source_key(args.url)}.json"
    raise SystemExit(run(args.url, args.capture_seconds, args.overlap_seconds,
                         args.interval_seconds, state, args.ranking, args.max_failures))


if __name__ == "__main__":
    main()
