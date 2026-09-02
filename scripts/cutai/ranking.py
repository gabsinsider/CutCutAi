import json
from datetime import UTC, datetime
from pathlib import Path

from .models import Clip


def load_ranking(path: Path) -> dict:
    if not path.exists():
        return {"generated_at": None, "clips": []}
    return json.loads(path.read_text(encoding="utf-8"))


def upsert_clip(path: Path, clip: Clip) -> dict:
    ranking = load_ranking(path)
    clips = [item for item in ranking.get("clips", []) if item.get("id") != clip.id]
    clips.append(clip.to_dict())
    clips.sort(key=lambda item: item.get("score", 0), reverse=True)
    ranking = {"generated_at": datetime.now(UTC).isoformat(), "clips": clips}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ranking

