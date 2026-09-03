FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CUTAI_DATA_ROOT=/data/cutcutai

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY scripts ./scripts
RUN pip install --upgrade pip \
    && pip install ".[ai]" \
    && pip install -U "yt-dlp[default]"

RUN mkdir -p /data/cutcutai

CMD ["python", "-m", "cutai.worker_api"]
