import json
import sys
from pathlib import Path


def main() -> None:
    result_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    data = json.loads(result_path.read_text(encoding="utf-8"))
    clips = data.get("clips", [])
    with output_path.open("a", encoding="utf-8") as output:
        for index in range(3):
            clip_id = clips[index].get("id", "") if index < len(clips) else ""
            output.write(f"clip{index + 1}={clip_id}\n")
        output.write(f"count={len(clips)}\n")


if __name__ == "__main__":
    main()
