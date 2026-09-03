import os
import time
from pathlib import Path

_MODEL_CACHE: dict[tuple[str, str, str], object] = {}


def _log(message: str) -> None:
    print(f"[transcription] {message}", flush=True)


def _model(model_size: str, device: str, compute_type: str):
    key = (model_size, device, compute_type)
    if key not in _MODEL_CACHE:
        from faster_whisper import WhisperModel
        _log(f"carregando modelo {model_size} ({device}/{compute_type})")
        started = time.monotonic()
        _MODEL_CACHE[key] = WhisperModel(model_size, device=device, compute_type=compute_type)
        _log(f"modelo carregado em {time.monotonic() - started:.1f}s")
    return _MODEL_CACHE[key]


def transcribe(path: Path, model_size: str | None = None) -> tuple[str, list[dict]]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return "", []

    # small continua disponível via WHISPER_MODEL, mas o padrão do worker contínuo
    # precisa ser leve o bastante para CPU compartilhada. O modelo base preserva
    # qualidade suficiente para seleção textual em português e reduz muito latência.
    model_size = model_size or os.getenv("WHISPER_MODEL", "base").strip() or "base"
    device = os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu"
    compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
    model = _model(model_size, device, compute_type)
    language = os.getenv("WHISPER_LANGUAGE", "pt").strip() or None

    _log(f"iniciando transcrição de {path.name} com modelo {model_size}")
    started = time.monotonic()
    segments, _ = model.transcribe(
        str(path),
        language=language,
        beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "1")),
        best_of=int(os.getenv("WHISPER_BEST_OF", "1")),
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
            if len(current) >= 7 or duration >= 2.8 or (sentence_end and duration >= 1.0):
                rows.append({"start": block_start, "end": block_end, "text": text})
                current = []
                block_start = None
                block_end = None

        if current and block_start is not None and block_end is not None:
            rows.append({"start": block_start, "end": block_end, "text": " ".join(current)})

    _log(f"transcrição concluída em {time.monotonic() - started:.1f}s: {len(rows)} blocos")
    return " ".join(row["text"] for row in rows).strip(), rows
