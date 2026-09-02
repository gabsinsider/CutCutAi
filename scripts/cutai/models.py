from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class SegmentScore:
    audio: float
    transcript: float
    scene: float
    total: float
    reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Clip:
    id: str
    title: str
    source_url: str
    source_title: str
    created_at: str
    duration: float
    score: float
    score_breakdown: dict[str, float]
    transcript: str
    description: str
    hashtags: list[str]
    asset_url: str | None = None
    thumbnail_url: str | None = None
    release_tag: str | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

