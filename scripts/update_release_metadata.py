import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--ranking", type=Path, required=True)
parser.add_argument("--clip", required=True)
parser.add_argument("--asset-url", required=True)
parser.add_argument("--thumbnail-url", required=True)
parser.add_argument("--captions-url", required=False)
parser.add_argument("--tag", required=True)
args = parser.parse_args()
data = json.loads(args.ranking.read_text(encoding="utf-8"))
for clip in data.get("clips", []):
    if clip.get("id") == args.clip:
        metadata = {
            "asset_url": args.asset_url,
            "thumbnail_url": args.thumbnail_url,
            "release_tag": args.tag,
        }
        if args.captions_url:
            metadata["captions_url"] = args.captions_url
        clip.update(**metadata)
args.ranking.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
