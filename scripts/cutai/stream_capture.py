"""Captura contínua segmentada, resiliente a reconexões e com vídeo/áudio adaptativos."""
from __future__ import annotations
import argparse,os,re,signal,subprocess,time
from pathlib import Path
from .proxy import normalize_proxy_url
from .validation import validate_source_url
SEGMENT_RE=re.compile(r"^segment-(\d+)\.mkv$")
def segment_number(path):
    m=SEGMENT_RE.match(path.name);return int(m.group(1)) if m else -1
def next_segment_number(output_dir):
    nums=[segment_number(p) for p in output_dir.glob("segment-*.mkv")];return max([n for n in nums if n>=0],default=-1)+1
def _resolver_base():
    cmd=["yt-dlp","--no-playlist","--no-progress","--extractor-retries","5","--fragment-retries","10","--retry-sleep","extractor:2"]
    cookie=os.getenv("CUTAI_YOUTUBE_COOKIES_FILE","").strip();use=os.getenv("CUTAI_USE_YOUTUBE_COOKIES","").strip().lower() in {"1","true","yes"};ua=os.getenv("CUTAI_YOUTUBE_USER_AGENT","").strip();proxy=normalize_proxy_url(os.getenv("CUTAI_PROXY_URL",""))
    if use and cookie and Path(cookie).exists():cmd += ["--cookies",cookie]
    if ua:cmd += ["--user-agent",ua]
    if proxy:cmd += ["--proxy",proxy]
    cmd += ["--extractor-args","youtube:player_client=web_safari,mweb;formats=missing_pot"]
    return cmd
def _resolve(url,fmt):
    out=subprocess.check_output(_resolver_base()+["-f",fmt,"-g",url],text=True).strip().splitlines();return [x.strip() for x in out if x.strip()]
def _media_urls(url):
    video_fmt="bestvideo[height<=1080][fps<=30][vcodec^=avc1]/bestvideo[height<=1080][fps<=30]";audio_fmt="bestaudio[acodec^=mp4a]/bestaudio"
    try:return _resolve(url,video_fmt)[0],_resolve(url,audio_fmt)[0],"adaptive"
    except (subprocess.CalledProcessError,IndexError):return _resolve(url,"best[height<=1080][fps<=30]/best")[0],None,"muxed"
def _input(url):
    # O CDN do YouTube pode trocar r16 -> rr1 (ou outro host) durante a live.
    # Desabilitar persistência/múltiplas requisições impede o demuxer HLS de tentar
    # reaproveitar uma conexão HTTP aberta para um hostname diferente.
    return ["-thread_queue_size","4096","-http_persistent","0","-http_multiple","0","-reconnect","1","-reconnect_streamed","1","-reconnect_delay_max","5","-i",url]
def build_command(url,output_dir,segment_seconds):
    output_dir.mkdir(parents=True,exist_ok=True);pattern=output_dir/"segment-%08d.mkv";start=next_segment_number(output_dir);video,audio,mode=_media_urls(url);print(f"[stream-capture] modo={mode}; iniciando no segmento {start:08d}",flush=True)
    cmd=["ffmpeg","-hide_banner","-nostdin","-loglevel","warning"]+_input(video)
    if audio:cmd += _input(audio)+["-map","0:v:0","-map","1:a:0"]
    else:cmd += ["-map","0:v?","-map","0:a?"]
    cmd += ["-c","copy","-max_interleave_delta","0","-f","segment","-segment_time",str(segment_seconds),"-segment_start_number",str(start),"-reset_timestamps","1",str(pattern)];return cmd
def capture(url,output_dir,segment_seconds=30):
    validate_source_url(url)
    if segment_seconds<10 or segment_seconds>120:raise ValueError("segment_seconds deve ficar entre 10 e 120")
    process=subprocess.Popen(build_command(url,output_dir,segment_seconds));stopping=False
    def stop(*_):
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
def ready_segments(output_dir,settle_seconds=2.0):
    now=time.time();files=sorted(output_dir.glob("segment-*.mkv"),key=segment_number);return [p for p in files if segment_number(p)>=0 and now-p.stat().st_mtime>=settle_seconds]
def main():
    p=argparse.ArgumentParser(description="Captura contínua segmentada de uma live");p.add_argument("--url",required=True);p.add_argument("--output-dir",type=Path,default=Path("work/stream"));p.add_argument("--segment-seconds",type=int,default=30);a=p.parse_args();raise SystemExit(capture(a.url,a.output_dir,a.segment_seconds))
if __name__=="__main__":main()
