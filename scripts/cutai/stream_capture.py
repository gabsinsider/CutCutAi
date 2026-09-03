"""Captura contínua segmentada, resiliente a reconexões."""
from __future__ import annotations
import argparse, os, re, signal, subprocess, time
from pathlib import Path
from .proxy import normalize_proxy_url
from .validation import validate_source_url

SEGMENT_RE=re.compile(r"^segment-(\d+)\.mkv$")

def segment_number(path:Path)->int:
    m=SEGMENT_RE.match(path.name); return int(m.group(1)) if m else -1

def next_segment_number(output_dir:Path)->int:
    numbers=[segment_number(p) for p in output_dir.glob("segment-*.mkv")]
    numbers=[n for n in numbers if n>=0]
    return max(numbers,default=-1)+1

def build_command(url:str,output_dir:Path,segment_seconds:int)->list[str]:
    output_dir.mkdir(parents=True,exist_ok=True); pattern=output_dir/"segment-%08d.mkv"; start=next_segment_number(output_dir)
    resolver=["yt-dlp","--no-playlist","--no-progress","--extractor-retries","5","--fragment-retries","10","--retry-sleep","extractor:2","-f","best[height<=1080][fps<=30]/best","-g"]
    cookie_file=os.getenv("CUTAI_YOUTUBE_COOKIES_FILE","").strip(); use_cookies=os.getenv("CUTAI_USE_YOUTUBE_COOKIES","").strip().lower() in {"1","true","yes"}; user_agent=os.getenv("CUTAI_YOUTUBE_USER_AGENT","").strip(); proxy_url=normalize_proxy_url(os.getenv("CUTAI_PROXY_URL",""))
    if use_cookies and cookie_file and Path(cookie_file).exists():resolver += ["--cookies",cookie_file]
    if user_agent:resolver += ["--user-agent",user_agent]
    if proxy_url:resolver += ["--proxy",proxy_url]
    resolver += ["--extractor-args","youtube:player_client=web_safari,mweb;formats=missing_pot",url]
    media_url=subprocess.check_output(resolver,text=True).strip().splitlines()[0]
    print(f"[stream-capture] iniciando no segmento {start:08d}",flush=True)
    return ["ffmpeg","-hide_banner","-nostdin","-loglevel","warning","-i",media_url,"-map","0:v?","-map","0:a?","-c","copy","-f","segment","-segment_time",str(segment_seconds),"-segment_start_number",str(start),"-reset_timestamps","1",str(pattern)]

def capture(url: str, output_dir: Path, segment_seconds: int = 30) -> int:
    validate_source_url(url)
    if segment_seconds<10 or segment_seconds>120:raise ValueError("segment_seconds deve ficar entre 10 e 120")
    process=subprocess.Popen(build_command(url,output_dir,segment_seconds)); stopping=False
    def stop(*_:object)->None:
        nonlocal stopping
        if stopping:return
        stopping=True;process.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:return process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill()

def ready_segments(output_dir:Path,settle_seconds:float=2.0)->list[Path]:
    now=time.time(); files=sorted(output_dir.glob("segment-*.mkv"),key=segment_number)
    return [p for p in files if segment_number(p)>=0 and now-p.stat().st_mtime>=settle_seconds]

def main()->None:
    p=argparse.ArgumentParser(description="Captura contínua segmentada de uma live");p.add_argument("--url",required=True);p.add_argument("--output-dir",type=Path,default=Path("work/stream"));p.add_argument("--segment-seconds",type=int,default=30);a=p.parse_args();raise SystemExit(capture(a.url,a.output_dir,a.segment_seconds))
if __name__=="__main__":main()
