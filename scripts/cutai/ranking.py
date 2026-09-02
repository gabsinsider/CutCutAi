import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Clip


def load_ranking(path: Path) -> dict:
    if not path.exists():
        return {"generated_at": None, "clips": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _numeric_score(item: dict[str, Any]) -> float:
    """Return a stable numeric score even for legacy ranking records."""
    value = item.get("score", 0)
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("total", "score", "combined", "value"):
            candidate = value.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def upsert_clip(path: Path, clip: Clip) -> dict:
    ranking = load_ranking(path)
    clips = [item for item in ranking.get("clips", []) if isinstance(item, dict) and item.get("id") != clip.id]
    clips.append(clip.to_dict())
    clips.sort(key=_numeric_score, reverse=True)
    ranking = {"generated_at": datetime.now(UTC).isoformat(), "clips": clips}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ranking, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ranking
