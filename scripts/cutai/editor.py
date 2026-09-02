import argparse
import json
import subprocess
from pathlib import Path


FILTERS = {
    "none": "null",
    "vivid": "eq=saturation=1.28:contrast=1.08",
    "cinematic": "eq=saturation=.85:contrast=1.15:brightness=-.03",
    "mono": "hue=s=0",
}


def render(source: Path, output: Path, resolution: int, filter_name: str) -> None:
    height = resolution if resolution in {720, 1080, 2160} else 1080
    vf = f"{FILTERS.get(filter_name, 'null')},scale=-2:{height}"
    preset = "ultrafast" if height == 2160 else "veryfast"
    subprocess.run(["ffmpeg", "-y", "-i", str(source), "-vf", vf, "-c:v", "libx264",
                    "-preset", preset, "-crf", "22", "-c:a", "aac", "-movflags", "+faststart", str(output)], check=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolution", type=int, default=1080)
    parser.add_argument("--filter", default="none")
    args = parser.parse_args()
    render(args.source, args.output, args.resolution, args.filter)
    print(json.dumps({"output": str(args.output), "resolution": args.resolution, "filter": args.filter}))


if __name__ == "__main__":
    main()

