import os
from pathlib import Path


def transcribe(path: Path, model_size: str | None = None) -> tuple[str, list[dict]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", []

    # tiny era rápido, mas insuficiente para legendas confiáveis em lives.
    # small oferece um salto grande de precisão ainda sendo viável em CPU.
    model_size = model_size or os.getenv("WHISPER_MODEL", "small").strip() or "small"
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    # As lives usadas no projeto são majoritariamente em português.
    # Permite sobrescrever por variável de ambiente quando necessário.
    language = os.getenv("WHISPER_LANGUAGE", "pt").strip() or None

    segments, _ = model.transcribe(
        str(path),
        language=language,
        beam_size=5,
        best_of=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=True,
        temperature=0.0,
    )

    rows = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            rows.append({
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
            })

    return " ".join(row["text"] for row in rows).strip(), rows
