import json
import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, text=True, capture_output=True)


def duration(path: Path) -> float:
    result = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    return float(json.loads(result.stdout)["format"]["duration"])


def audio_metrics(path: Path) -> tuple[float, float]:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"], text=True, capture_output=True, check=False)
    text = result.stderr
    mean_db = _db_value(text, "mean_volume", -35.0); peak_db = _db_value(text, "max_volume", -12.0)
    return 10 ** (mean_db / 20), 10 ** (peak_db / 20)


def _db_value(text: str, label: str, fallback: float) -> float:
    for line in text.splitlines():
        if label in line:
            try: return float(line.split(label + ":", 1)[1].split(" dB", 1)[0].strip())
            except (ValueError, IndexError): pass
    return fallback


def scene_score(path: Path) -> float:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-filter:v", "select='gt(scene,0.35)',showinfo", "-f", "null", "-"], text=True, capture_output=True, check=False)
    return min(100.0, 20 + result.stderr.count("showinfo") * 5.0)


def make_clip_range(source: Path, output: Path, start: float, end: float) -> None:
    start=max(0.0,start); length=max(1.0,end-start); output.parent.mkdir(parents=True,exist_ok=True)
    run(["ffmpeg","-y","-fflags","+genpts+discardcorrupt","-err_detect","ignore_err","-ss",str(start),"-i",str(source),"-t",str(length),"-map","0:v:0","-map","0:a:0?","-c:v","libx264","-preset","veryfast","-crf","18","-profile:v","high","-pix_fmt","yuv420p","-fps_mode:v","vfr","-c:a","aac","-b:a","192k","-avoid_negative_ts","make_zero","-movflags","+faststart","-shortest",str(output)])


def _ass_time(seconds: float) -> str:
    seconds=max(0.0,seconds); hours=int(seconds//3600); minutes=int((seconds%3600)//60); secs=seconds%60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_text(text: str) -> str:
    return text.replace("\\",r"\\").replace("{",r"\{").replace("}",r"\}").replace("\n",r"\N").strip()


def _caption_chunks(text: str, max_words: int = 5) -> list[str]:
    words=text.split()
    if len(words) <= max_words: return [text]
    return [" ".join(words[i:i+max_words]) for i in range(0,len(words),max_words)]


def burn_subtitles(source: Path, output: Path, segments: list[dict]) -> None:
    """Burn short social captions. The opening gets stronger typography without changing A/V timing."""
    if not segments:
        if source != output: run(["ffmpeg","-y","-i",str(source),"-map","0","-c","copy","-movflags","+faststart",str(output)])
        return

    ass_path=output.with_suffix(".ass").resolve()
    header="""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Arial,62,&H00FFFFFF,&H0000FFFF,&H00101010,&H78000000,-1,0,0,0,100,100,0,0,1,4,1,2,150,150,92,1
Style: Hook,Arial,72,&H00FFFFFF,&H0000FFFF,&H00000000,&H8A000000,-1,0,0,0,100,100,0,0,1,5,2,2,140,140,105,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    events=[]
    for segment in segments:
        start=float(segment.get("start",0)); end=max(start+.35,float(segment.get("end",start+.35))); text=str(segment.get("text","")).strip()
        chunks=_caption_chunks(text,5)
        if not chunks: continue
        span=max(.35,end-start); chunk_span=span/len(chunks)
        for idx,chunk in enumerate(chunks):
            cs=start+idx*chunk_span; ce=end if idx==len(chunks)-1 else min(end,cs+chunk_span)
            style="Hook" if cs < 8.0 else "Default"
            safe=_ass_text(chunk)
            if safe: events.append(f"Dialogue: 0,{_ass_time(cs)},{_ass_time(ce)},{style},,0,0,0,,{safe}")
    ass_path.write_text(header+"\n".join(events)+"\n",encoding="utf-8")
    subtitle_filter=f"ass=filename='{ass_path.as_posix()}'"
    run(["ffmpeg","-y","-i",str(source),"-vf",subtitle_filter,"-map","0:v:0","-map","0:a:0?","-c:v","libx264","-preset","veryfast","-crf","18","-profile:v","high","-pix_fmt","yuv420p","-fps_mode:v","vfr","-c:a","copy","-movflags","+faststart","-shortest",str(output)])
    ass_path.unlink(missing_ok=True)


def make_clip(source: Path, output: Path, center: float, length: int = 60) -> None:
    start=max(0.0,center-length/2); make_clip_range(source,output,start,start+length)


def thumbnail(source: Path, output: Path) -> None:
    run(["ffmpeg","-y","-i",str(source),"-vf","select=gte(t\\,2),scale=640:-2","-frames:v","1",str(output)])
