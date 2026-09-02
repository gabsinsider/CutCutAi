import json
import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def duration(path: Path) -> float:
    result = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(result.stdout)["format"]["duration"])


def audio_metrics(path: Path) -> tuple[float, float]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        text=True, capture_output=True, check=False,
    )
    text = result.stderr
    mean_db = _db_value(text, "mean_volume", -35.0)
    peak_db = _db_value(text, "max_volume", -12.0)
    return 10 ** (mean_db / 20), 10 ** (peak_db / 20)


def _db_value(text: str, label: str, fallback: float) -> float:
    for line in text.splitlines():
        if label in line:
            try:
                return float(line.split(label + ":", 1)[1].split(" dB", 1)[0].strip())
            except (ValueError, IndexError):
                pass
    return fallback


def scene_score(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path), "-filter:v", "select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
        text=True, capture_output=True, check=False,
    )
    changes = result.stderr.count("showinfo")
    return min(100.0, 20 + changes * 5.0)


def make_clip_range(source: Path, output: Path, start: float, end: float) -> None:
    start = max(0.0, start)
    length = max(1.0, end - start)
    output.parent.mkdir(parents=True, exist_ok=True)
    run([
        "ffmpeg", "-y", "-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
        "-ss", str(start), "-i", str(source), "-t", str(length),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "setpts=PTS-STARTPTS,fps=30",
        "-af", "asetpts=PTS-STARTPTS,aresample=async=1:min_hard_comp=0.100:first_pts=0",
        "-fps_mode:v", "cfr",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", "-shortest", str(output),
    ])


def make_clip(source: Path, output: Path, center: float, length: int = 60) -> None:
    start = max(0.0, center - length / 2)
    make_clip_range(source, output, start, start + length)


def thumbnail(source: Path, output: Path) -> None:
    run(["ffmpeg", "-y", "-ss", "2", "-i", str(source), "-frames:v", "1", "-vf", "scale=640:-2", str(output)])
