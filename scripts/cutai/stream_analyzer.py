"""Consumidor do buffer produzido por stream_capture.

Captura e análise são processos independentes: enquanto novos segmentos chegam,
este processo monta janelas somente com segmentos já finalizados e entrega o
arquivo local ao pipeline, sem reconectar à transmissão.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

from .pipeline import process_source
from .stream_capture import ready_segments


def _concat(segments: list[Path], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = output.with_suffix(".txt")
    manifest.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    result = subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","concat","-safe","0","-i",str(manifest),"-c","copy",str(output)])
    manifest.unlink(missing_ok=True)
    if result.returncode != 0: raise RuntimeError("Falha ao montar janela contínua")


def run(url: str, stream_dir: Path, workdir: Path, segment_seconds: int=30,
        window_seconds: int=600, overlap_seconds: int=90, poll_seconds: int=5) -> None:
    needed=max(2,window_seconds//segment_seconds); overlap=max(1,overlap_seconds//segment_seconds); cursor=0
    state_path=workdir/"stream-analyzer.json"; workdir.mkdir(parents=True,exist_ok=True)
    if state_path.exists():
        try: cursor=max(0,int(json.loads(state_path.read_text()).get("cursor",0)))
        except (ValueError,OSError,TypeError): cursor=0
    while True:
        segments=ready_segments(stream_dir)
        if len(segments)-cursor < needed:
            time.sleep(poll_seconds); continue
        selected=segments[cursor:cursor+needed]; first=selected[0].stem; last=selected[-1].stem; window=workdir/f"window-{first}-{last}.mkv"; _concat(selected,window)
        analysis_dir=workdir/f"analysis-{first}-{last}"
        try:
            process_source(window,url,analysis_dir,"Live contínua")
        finally:
            window.unlink(missing_ok=True)
        cursor += max(1,needed-overlap)
        state_path.write_text(json.dumps({"cursor":cursor,"last_segment":last},indent=2)+"\n",encoding="utf-8")
        # Segmentos muito antigos deixam de ser necessários após a margem de contexto.
        keep_from=max(0,cursor-overlap)
        for old in segments[:keep_from]: old.unlink(missing_ok=True)


def main() -> None:
    p=argparse.ArgumentParser(description="Analisa continuamente os segmentos de uma live")
    p.add_argument("--url",required=True); p.add_argument("--stream-dir",type=Path,default=Path("work/stream")); p.add_argument("--workdir",type=Path,default=Path("work/live-analysis")); p.add_argument("--segment-seconds",type=int,default=30); p.add_argument("--window-seconds",type=int,default=600); p.add_argument("--overlap-seconds",type=int,default=90); p.add_argument("--poll-seconds",type=int,default=5); a=p.parse_args()
    run(a.url,a.stream_dir,a.workdir,a.segment_seconds,a.window_seconds,a.overlap_seconds,a.poll_seconds)

if __name__=="__main__": main()
