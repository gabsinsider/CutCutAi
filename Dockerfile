FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY scripts ./scripts
RUN pip install --upgrade pip \
    && pip install ".[ai]" \
    && pip install "yt-dlp>=2025.1.1"

RUN mkdir -p /data/cutcutai
VOLUME ["/data/cutcutai"]

ENTRYPOINT ["python", "-m", "cutai.live_supervisor"]
CMD ["--help"]
