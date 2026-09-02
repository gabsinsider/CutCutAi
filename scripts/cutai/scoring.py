import math
import re

from .models import SegmentScore

EMOTIONAL_TERMS = {"absurdo","atenção","bomba","caramba","emocionante","golaço","gol","histórico","impressionante","inacreditável","incrível","loucura","melhor","ninguém","polêmica","segredo","sensacional","surpreendente","urgente"}
HOOK_TERMS = {"atenção","descobriu","entenda","explicar","ninguém","olha","problema","segredo","seguinte","verdade","você","vocês"}
SURPRISE_TERMS = {"absurdo","caramba","impressionante","inacreditável","incrível","loucura","nunca","ninguém","surpreendente"}
CONFLICT_TERMS = {"acusou","briga","criticou","discussão","erro","mentira","polêmica","problema","reclamou","roubo","treta"}
PAYOFF_TERMS = {"acabou","conseguiu","conclusão","enfim","finalmente","ganhou","perdeu","pronto","resultado","resolveu","terminou"}
CURIOSITY_TERMS = {"como","porquê","porque","motivo","segredo","verdade","descobriu","aconteceu","imagina","sabia","entenda"}
FILLERS = {"tipo","né","assim","entendeu","cara","mano","ahn","hum","é","então"}


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _words(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.lower())


def viral_text_score(text: str) -> tuple[float, list[str], dict[str, float]]:
    words = _words(text)
    if not words:
        empty = {k: 0.0 for k in ("hook","retention","emotion","surprise","conflict","payoff","clarity")}
        return 0.0, [], empty

    opening = words[:32]
    first_12 = words[:12]
    ending = words[-35:]
    unique_ratio = len(set(words)) / max(1, len(words))
    filler_ratio = sum(1 for w in opening if w in FILLERS) / max(1, len(opening))

    hook_hits = sum(1 for term in HOOK_TERMS if term in opening)
    curiosity_hits = sum(1 for term in CURIOSITY_TERMS if term in opening)
    emotion_hits = sum(1 for term in EMOTIONAL_TERMS if term in words)
    surprise_hits = sum(1 for term in SURPRISE_TERMS if term in words)
    conflict_hits = sum(1 for term in CONFLICT_TERMS if term in words)
    payoff_hits = sum(1 for term in PAYOFF_TERMS if term in ending)

    direct_open = bool(set(first_12) & (HOOK_TERMS | SURPRISE_TERMS | CONFLICT_TERMS))
    question_open = "?" in text[:260]
    hook = clamp(20 + hook_hits*16 + (15 if direct_open else 0) + (12 if question_open else 0))
    retention = clamp(28 + curiosity_hits*13 + hook_hits*9 + (12 if direct_open else 0) + (10 if question_open else 0) - filler_ratio*70)
    emotion = clamp(15 + emotion_hits*14 + min(18, text.count("!")*4))
    surprise = clamp(10 + surprise_hits*18)
    conflict = clamp(8 + conflict_hits*18)
    payoff = clamp(20 + payoff_hits*18)
    clarity = clamp(34 + min(30, len(words)*.22) + min(24, unique_ratio*28) - filler_ratio*35)

    total = clamp(hook*.20 + retention*.20 + emotion*.14 + surprise*.13 + conflict*.11 + payoff*.14 + clarity*.08)
    reasons = []
    if hook >= 55: reasons.append("Gancho forte no início")
    if retention >= 55: reasons.append("Abertura com potencial de retenção")
    if emotion >= 55: reasons.append("Alta carga emocional")
    if surprise >= 50: reasons.append("Elemento de surpresa")
    if conflict >= 50: reasons.append("Conflito ou tensão")
    if payoff >= 50: reasons.append("Entrega clara no final")

    return total, reasons, {"hook":hook,"retention":retention,"emotion":emotion,"surprise":surprise,"conflict":conflict,"payoff":payoff,"clarity":clarity}


def transcript_score(text: str) -> tuple[float, list[str]]:
    total, reasons, _ = viral_text_score(text)
    return total, reasons


def audio_score(rms_mean: float, rms_peak: float) -> tuple[float, list[str]]:
    dynamic = max(0.0, rms_peak-rms_mean)
    score = clamp(15 + math.sqrt(max(rms_mean,0))*55 + math.sqrt(dynamic)*55)
    return score, ["Pico de energia no áudio"] if dynamic > .12 else []


def combine_scores(audio: float, transcript: float, scene: float, reasons: list[str] | None=None) -> SegmentScore:
    total = clamp(audio*.22 + transcript*.60 + scene*.18)
    return SegmentScore(audio=clamp(audio), transcript=clamp(transcript), scene=clamp(scene), total=total, reasons=reasons or [])
