import os
from pathlib import Path


def transcribe(path: Path, model_size: str = "tiny") -> tuple[str, list[dict]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", []
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    language = os.getenv("WHISPER_LANGUAGE", "").strip() or None
    segments, _ = model.transcribe(str(path), beam_size=1, vad_filter=True, language=language)
    rows = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
    return " ".join(row["text"] for row in rows).strip(), rows
