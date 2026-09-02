import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .media import audio_metrics, duration, make_clip_range, scene_score, thumbnail
from .metadata import suggest_metadata
from .models import Clip
from .proxy import normalize_proxy_url
from .ranking import upsert_clip
from .scoring import audio_score, combine_scores, transcript_score
from .transcription import transcribe
from .validation import validate_source_url

MIN_CLIP_SECONDS = 60.0
MAX_CLIP_SECONDS = 90.0


def download(url: str, output: Path, seconds: int) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = ["yt-dlp", "--no-playlist", "--no-progress", "--no-simulate", "--impersonate", "chrome",
               "--extractor-retries", "3", "--js-runtimes", "node",
               "--extractor-args", "youtube:player_client=default,android;formats=missing_pot",
               "--downloader", "ffmpeg", "--downloader-args", f"ffmpeg_i:-t {seconds}",
               "--merge-output-format", "mkv",
               "-f", "bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080][fps<=30]/bestvideo[height<=720][fps<=30]+bestaudio/best[height<=720]/best",
               "-o", str(output), "--print", "title"]
    proxy_url = normalize_proxy_url(os.getenv("CUTAI_PROXY_URL", ""))
    if proxy_url:
        command.extend(["--proxy", proxy_url])
    command.append(url)
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode:
        detail = result.stderr.strip()[-3000:] or result.stdout.strip()[-3000:]
        if proxy_url:
            detail = detail.replace(proxy_url, "[proxy protegido]")
        raise RuntimeError(f"yt-dlp não conseguiu acessar esta transmissão:\n{detail}")
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "Live"


def _segment_excitement(text: str) -> float:
    normalized = text.lower()
    keywords = {"gol", "goal", "golaço", "incrível", "absurdo", "caramba", "nossa", "wow", "olha", "atenção", "agora", "mano", "meu deus", "não acredito", "que isso", "risada", "haha", "kkkk", "vitória", "ganhou", "perdeu", "recorde", "melhor"}
    score = sum(1.0 for word in keywords if word in normalized)
    score += min(2.0, text.count("!") * 0.5 + text.count("?") * 0.25)
    return score if len(text.split()) >= 4 else score * 0.5


def choose_top_centers(segments: list[dict], source_duration: float, clip_length: int = 60, limit: int = 3) -> list[tuple[float, float]]:
    if source_duration <= clip_length:
        return [(50.0, source_duration / 2)]
    half = clip_length / 2
    candidates = []
    center = half
    last = max(half, source_duration - half)
    while center <= last + 0.01:
        start, end = center - half, center + half
        inside = [s for s in segments if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end]
        speech_seconds = sum(max(0.0, min(end, float(s.get("end", 0))) - max(start, float(s.get("start", 0)))) for s in inside)
        words = sum(len(str(s.get("text", "")).split()) for s in inside)
        excitement = sum(_segment_excitement(str(s.get("text", ""))) for s in inside)
        score = min(30.0, speech_seconds * 0.5) + min(25.0, words * 0.12) + min(35.0, excitement * 5.0)
        candidates.append((score, center))
        center += 10.0
    candidates.sort(reverse=True)
    selected = []
    for candidate in candidates:
        if all(abs(candidate[1] - chosen[1]) >= clip_length * 0.75 for chosen in selected):
            selected.append(candidate)
            if len(selected) == limit:
                break
    if len(selected) < limit:
        for candidate in candidates:
            if candidate not in selected and all(abs(candidate[1] - chosen[1]) >= clip_length * 0.5 for chosen in selected):
                selected.append(candidate)
                if len(selected) == limit:
                    break
    return selected or [(0.0, source_duration / 2)]


def choose_smart_range(segments: list[dict], center: float, source_duration: float) -> tuple[float, float]:
    """Expand a 60s core to natural speech boundaries, never below 60s and capped at 90s."""
    core_start = max(0.0, center - MIN_CLIP_SECONDS / 2)
    core_end = min(source_duration, core_start + MIN_CLIP_SECONDS)
    core_start = max(0.0, core_end - MIN_CLIP_SECONDS)
    if not segments:
        return core_start, core_end

    start = core_start
    end = core_end
    # Prefer beginning at the start of a spoken segment shortly before the 60s core.
    before = [s for s in segments if float(s.get("start", 0)) <= core_start and core_start - float(s.get("start", 0)) <= 12.0]
    if before:
        start = float(before[-1].get("start", core_start))
    # Prefer finishing after the current thought rather than cutting the final sentence.
    after = [s for s in segments if float(s.get("end", 0)) >= core_end and float(s.get("end", 0)) - core_end <= 18.0]
    if after:
        end = float(after[0].get("end", core_end))

    # Guarantee the user's hard minimum of 60 seconds.
    if end - start < MIN_CLIP_SECONDS:
        missing = MIN_CLIP_SECONDS - (end - start)
        grow_after = min(missing, source_duration - end)
        end += grow_after
        start = max(0.0, start - (missing - grow_after))
    # Avoid runaway clips while still allowing context beyond one minute.
    if end - start > MAX_CLIP_SECONDS:
        end = start + MAX_CLIP_SECONDS
        if end > source_duration:
            end = source_duration
            start = max(0.0, end - MAX_CLIP_SECONDS)
    return start, end


def _clip_transcript(segments: list[dict], start: float, end: float) -> str:
    return " ".join(str(s.get("text", "")).strip() for s in segments if float(s.get("end", 0)) > start and float(s.get("start", 0)) < end).strip()


def process(url: str, workdir: Path, ranking_path: Path, capture_seconds: int = 180) -> list[Clip]:
    url, _ = validate_source_url(url)
    source = workdir / "capture.mkv"
    source_title = download(url, source, min(max(capture_seconds, 60), 900))
    source_duration = duration(source)
    if source_duration < MIN_CLIP_SECONDS:
        raise RuntimeError(f"A plataforma entregou somente {source_duration:.1f}s utilizáveis. São necessários pelo menos 60s para publicar um corte.")
    transcript, segments = transcribe(source, os.getenv("WHISPER_MODEL", "tiny"))
    rms_mean, rms_peak = audio_metrics(source)
    a_score, a_reasons = audio_score(rms_mean, rms_peak)
    t_score, t_reasons = transcript_score(transcript)
    source_scene = scene_score(source)
    base_score = combine_scores(a_score, t_score, source_scene, a_reasons + t_reasons)
    candidates = choose_top_centers(segments, source_duration)
    now = datetime.now(UTC)
    clips = []
    for rank, (semantic_score, center) in enumerate(candidates, start=1):
        start, end = choose_smart_range(segments, center, source_duration)
        clip_id = hashlib.sha256(f"{url}-{now.isoformat()}-{rank}-{start}-{end}".encode()).hexdigest()[:12]
        clip_path = workdir / f"{clip_id}.mp4"
        make_clip_range(source, clip_path, start, end)
        thumb_path = workdir / f"{clip_id}.jpg"
        thumbnail(clip_path, thumb_path)
        local_transcript = _clip_transcript(segments, start, end) or transcript
        description, hashtags = suggest_metadata(local_transcript)
        local_scene = scene_score(clip_path)
        final_score = round(max(0.0, min(100.0, base_score.total * 0.70 + semantic_score * 0.20 + local_scene * 0.10)), 2)
        clip = Clip(id=clip_id, title=description[:80], source_url=url, source_title=source_title,
                    created_at=now.isoformat(), duration=duration(clip_path), score=final_score,
                    score_breakdown={"audio": base_score.audio, "transcript": base_score.transcript, "scene": local_scene},
                    transcript=local_transcript, description=description, hashtags=hashtags,
                    reasons=base_score.reasons + [f"Top {rank}; janela inteligente {start:.1f}s–{end:.1f}s ({end-start:.1f}s)"])
        upsert_clip(ranking_path, clip)
        clips.append(clip)
    payload = {"clips": [clip.to_dict() for clip in clips], "count": len(clips), "best_id": clips[0].id}
    (workdir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return clips


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--workdir", type=Path, default=Path("work"))
    parser.add_argument("--ranking", type=Path, default=Path("data/ranking.json"))
    parser.add_argument("--capture-seconds", type=int, default=180)
    args = parser.parse_args()
    clips = process(args.url, args.workdir, args.ranking, args.capture_seconds)
    print(json.dumps({"clips": [clip.to_dict() for clip in clips]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
