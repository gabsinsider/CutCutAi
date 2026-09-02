import math
import re

from .models import SegmentScore

EMOTIONAL_TERMS = {
    "absurdo", "agora", "atenção", "bomba", "caramba", "emocionante",
    "gol", "histórico", "impressionante", "inacreditável", "incrível",
    "melhor", "ninguém", "olha", "polêmica", "segredo", "surpreendente",
}


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def transcript_score(text: str) -> tuple[float, list[str]]:
    words = re.findall(r"[\wÀ-ÿ]+", text.lower())
    hits = sorted(set(words) & EMOTIONAL_TERMS)
    punctuation = min(3, text.count("!") + text.count("?"))
    density = len(hits) / max(1, len(words))
    score = clamp(18 + density * 650 + punctuation * 7 + min(len(words), 120) / 6)
    reasons = []
    if hits:
        reasons.append("Linguagem emocional: " + ", ".join(hits[:4]))
    if punctuation:
        reasons.append("Fala com ênfase")
    return score, reasons


def audio_score(rms_mean: float, rms_peak: float) -> tuple[float, list[str]]:
    # RMS inputs are expected in the 0..1 range.
    dynamic = max(0.0, rms_peak - rms_mean)
    score = clamp(15 + math.sqrt(max(rms_mean, 0)) * 55 + math.sqrt(dynamic) * 55)
    reasons = ["Pico de energia no áudio"] if dynamic > 0.12 else []
    return score, reasons


def combine_scores(audio: float, transcript: float, scene: float, reasons: list[str] | None = None) -> SegmentScore:
    total = clamp(audio * 0.35 + transcript * 0.45 + scene * 0.20)
    return SegmentScore(audio=clamp(audio), transcript=clamp(transcript), scene=clamp(scene), total=total, reasons=reasons or [])

