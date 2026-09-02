import os
from pathlib import Path


def transcribe(path: Path, model_size: str | None = None) -> tuple[str, list[dict]]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "", []

    model_size = model_size or os.getenv("WHISPER_MODEL", "small").strip() or "small"
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    language = os.getenv("WHISPER_LANGUAGE", "pt").strip() or None

    segments, _ = model.transcribe(
        str(path),
        language=language,
        beam_size=5,
        best_of=5,
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": 250,
            "speech_pad_ms": 120,
        },
        condition_on_previous_text=True,
        temperature=0.0,
        word_timestamps=True,
    )

    rows = []
    for segment in segments:
        words = [word for word in (segment.words or []) if word.word.strip()]
        if not words:
            text = segment.text.strip()
            if text:
                rows.append({"start": float(segment.start), "end": float(segment.end), "text": text})
            continue

        # Monte blocos curtos a partir dos timestamps reais das palavras.
        # Isso evita mostrar uma frase inteira antes de ela ter sido falada.
        current = []
        block_start = None
        block_end = None
        for word in words:
            word_start = float(word.start)
            word_end = float(word.end)
            token = word.word.strip()
            if block_start is None:
                block_start = word_start

            current.append(token)
            block_end = word_end
            text = " ".join(current)
            duration = block_end - block_start
            sentence_end = token.endswith((".", "!", "?", ",", ";", ":"))

            # Legendas de 2-3 segundos / poucas palavras soam mais naturais.
            if len(current) >= 7 or duration >= 2.8 or (sentence_end and duration >= 1.0):
                rows.append({"start": block_start, "end": block_end, "text": text})
                current = []
                block_start = None
                block_end = None

        if current and block_start is not None and block_end is not None:
            rows.append({"start": block_start, "end": block_end, "text": " ".join(current)})

    return " ".join(row["text"] for row in rows).strip(), rows
