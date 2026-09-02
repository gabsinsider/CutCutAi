import math
import re

from .models import SegmentScore

EMOTIONAL_TERMS = {
    "absurdo", "atenção", "bomba", "caramba", "emocionante", "golaço", "gol",
    "histórico", "impressionante", "inacreditável", "incrível", "loucura", "melhor",
    "ninguém", "polêmica", "segredo", "sensacional", "surpreendente", "urgente",
}
HOOK_TERMS = {
    "atenção", "descobriu", "entenda", "explicar", "ninguém", "olha", "problema",
    "segredo", "seguinte", "verdade", "você", "vocês",
}
SURPRISE_TERMS = {
    "absurdo", "caramba", "impressionante", "inacreditável", "incrível", "loucura",
    "nunca", "ninguém", "surpreendente",
}
CONFLICT_TERMS = {
    "acusou", "briga", "criticou", "discussão", "erro", "mentira", "polêmica",
    "problema", "reclamou", "roubo", "treta",
}
PAYOFF_TERMS = {
    "acabou", "conseguiu", "conclusão", "enfim", "finalmente", "ganhou", "perdeu",
    "pronto", "resultado", "resolveu", "terminou",
}


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _words(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.lower())


def viral_text_score(text: str) -> tuple[float, list[str], dict[str, float]]:
    words = _words(text)
    if not words:
        return 0.0, [], {"hook": 0.0, "emotion": 0.0, "surprise": 0.0, "conflict": 0.0, "payoff": 0.0, "clarity": 0.0}

    normalized = " ".join(words)
    opening = words[:28]
    ending = words[-35:]
    unique_ratio = len(set(words)) / max(1, len(words))

    hook_hits = sum(1 for term in HOOK_TERMS if term in opening)
    emotion_hits = sum(1 for term in EMOTIONAL_TERMS if term in words)
    surprise_hits = sum(1 for term in SURPRISE_TERMS if term in words)
    conflict_hits = sum(1 for term in CONFLICT_TERMS if term in words)
    payoff_hits = sum(1 for term in PAYOFF_TERMS if term in ending)

    hook = clamp(22 + hook_hits * 17 + min(20, text[:220].count("?") * 10 + text[:220].count("!") * 7))
    emotion = clamp(15 + emotion_hits * 14 + min(18, text.count("!") * 4))
    surprise = clamp(10 + surprise_hits * 18)
    conflict = clamp(8 + conflict_hits * 18)
    payoff = clamp(20 + payoff_hits * 18)
    clarity = clamp(30 + min(35, len(words) * .25) + min(25, unique_ratio * 30))

    total = clamp(hook * .24 + emotion * .20 + surprise * .16 + conflict * .14 + payoff * .16 + clarity * .10)
    reasons = []
    if hook >= 55: reasons.append("Gancho forte no início")
    if emotion >= 55: reasons.append("Alta carga emocional")
    if surprise >= 50: reasons.append("Elemento de surpresa")
    if conflict >= 50: reasons.append("Conflito ou tensão")
    if payoff >= 50: reasons.append("Entrega clara no final")

    return total, reasons, {
        "hook": hook,
        "emotion": emotion,
        "surprise": surprise,
        "conflict": conflict,
        "payoff": payoff,
        "clarity": clarity,
    }


def transcript_score(text: str) -> tuple[float, list[str]]:
    total, reasons, _ = viral_text_score(text)
    return total, reasons


def audio_score(rms_mean: float, rms_peak: float) -> tuple[float, list[str]]:
    dynamic = max(0.0, rms_peak - rms_mean)
    score = clamp(15 + math.sqrt(max(rms_mean, 0)) * 55 + math.sqrt(dynamic) * 55)
    reasons = ["Pico de energia no áudio"] if dynamic > 0.12 else []
    return score, reasons


def combine_scores(audio: float, transcript: float, scene: float, reasons: list[str] | None = None) -> SegmentScore:
    total = clamp(audio * 0.25 + transcript * 0.55 + scene * 0.20)
    return SegmentScore(audio=clamp(audio), transcript=clamp(transcript), scene=clamp(scene), total=total, reasons=reasons or [])
