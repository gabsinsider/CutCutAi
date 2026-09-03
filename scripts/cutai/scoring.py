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
DEVELOPMENT_TERMS = {"porque","então","depois","antes","quando","mas","porém","aconteceu","começou","resultado","motivo","problema","decidiu","conseguiu"}


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _words(text: str) -> list[str]:
    return re.findall(r"[\wÀ-ÿ]+", text.lower())


def viral_text_score(text: str) -> tuple[float, list[str], dict[str, float]]:
    words = _words(text)
    if not words:
        empty = {k: 0.0 for k in ("hook","retention","emotion","surprise","conflict","payoff","clarity","story_arc","viral_strength")}
        return 0.0, [], empty

    opening = words[:32]
    first_12 = words[:12]
    middle_start = max(0, len(words)//3)
    middle_end = max(middle_start+1, (len(words)*2)//3)
    middle = words[middle_start:middle_end]
    ending = words[-35:]
    unique_ratio = len(set(words)) / max(1, len(words))
    filler_ratio = sum(1 for w in opening if w in FILLERS) / max(1, len(opening))

    hook_hits = sum(1 for term in HOOK_TERMS if term in opening)
    curiosity_hits = sum(1 for term in CURIOSITY_TERMS if term in opening)
    emotion_hits = sum(1 for term in EMOTIONAL_TERMS if term in words)
    surprise_hits = sum(1 for term in SURPRISE_TERMS if term in words)
    conflict_hits = sum(1 for term in CONFLICT_TERMS if term in words)
    payoff_hits = sum(1 for term in PAYOFF_TERMS if term in ending)
    development_hits = sum(1 for term in DEVELOPMENT_TERMS if term in middle)

    direct_open = bool(set(first_12) & (HOOK_TERMS | SURPRISE_TERMS | CONFLICT_TERMS))
    question_open = "?" in text[:260]
    hook = clamp(20 + hook_hits*16 + (15 if direct_open else 0) + (12 if question_open else 0))
    retention = clamp(28 + curiosity_hits*13 + hook_hits*9 + (12 if direct_open else 0) + (10 if question_open else 0) - filler_ratio*70)
    emotion = clamp(15 + emotion_hits*14 + min(18, text.count("!")*4))
    surprise = clamp(10 + surprise_hits*18)
    conflict = clamp(8 + conflict_hits*18)
    payoff = clamp(20 + payoff_hits*18)
    clarity = clamp(34 + min(30, len(words)*.22) + min(24, unique_ratio*28) - filler_ratio*35)

    development = clamp(25 + development_hits*11 + min(20, len(middle)*.18))
    opening_strength = (hook + retention) / 2
    body_strength = (development + emotion + surprise + conflict + clarity) / 5
    ending_strength = payoff
    weakest_phase = min(opening_strength, body_strength, ending_strength)

    # O arco precisa refletir o vídeo inteiro sem inflar artificialmente a nota.
    # Os pesos somam 1.0 e a fase mais fraca continua tendo influência explícita.
    story_arc = clamp(opening_strength*.28 + body_strength*.33 + ending_strength*.26 + weakest_phase*.13)

    raw_viral = hook*.15 + retention*.18 + emotion*.12 + surprise*.11 + conflict*.09 + payoff*.14 + clarity*.07 + story_arc*.14
    balance_penalty = max(0.0, 42.0-weakest_phase) * .22
    viral_strength = clamp(raw_viral-balance_penalty)
    total = viral_strength

    reasons = []
    if hook >= 55: reasons.append("Gancho forte no início")
    if retention >= 55: reasons.append("Abertura com potencial de retenção")
    if emotion >= 55: reasons.append("Alta carga emocional")
    if surprise >= 50: reasons.append("Elemento de surpresa")
    if conflict >= 50: reasons.append("Conflito ou tensão")
    if payoff >= 50: reasons.append("Entrega clara no final")
    if story_arc >= 55: reasons.append("História mantém força do início ao desfecho")

    return total, reasons, {"hook":hook,"retention":retention,"emotion":emotion,"surprise":surprise,"conflict":conflict,"payoff":payoff,"clarity":clarity,"story_arc":story_arc,"viral_strength":viral_strength}


def transcript_score(text: str) -> tuple[float, list[str]]:
    total, reasons, _ = viral_text_score(text)
    return total, reasons


def audio_score(rms_mean: float, rms_peak: float) -> tuple[float, list[str]]:
    dynamic = max(0.0, rms_peak-rms_mean)
    score = clamp(15 + math.sqrt(max(rms_mean,0))*55 + math.sqrt(dynamic)*55)
    return score, ["Pico de energia no áudio"] if dynamic > .12 else []


def combine_scores(audio: float, transcript: float, scene: float, reasons: list[str] | None=None) -> SegmentScore:
    total = clamp(audio*.20 + transcript*.64 + scene*.16)
    return SegmentScore(audio=clamp(audio), transcript=clamp(transcript), scene=clamp(scene), total=total, reasons=reasons or [])
