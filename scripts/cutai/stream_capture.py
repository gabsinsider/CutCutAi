"""Captura contínua segmentada para lives longas, preservando continuidade A/V."""
from __future__ import annotations
import argparse,os,re,shutil,signal,subprocess,time
from pathlib import Path
from .proxy import normalize_proxy_url
from .validation import validate_source_url

SEGMENT_RE=re.compile(r"^segment-(\d+)\.mkv$")
def segment_number(path):
    m=SEGMENT_RE.match(path.name);return int(m.group(1)) if m else -1
def next_segment_number(output_dir):
    nums=[segment_number(p) for p in output_dir.glob("segment-*.mkv")];return max([n for n in nums if n>=0],default=-1)+1
def _proxy():return normalize_proxy_url(os.getenv("CUTAI_PROXY_URL",""))
def _resolver_base(client_args):
    cmd=["yt-dlp","--no-playlist","--no-progress","--extractor-retries","5","--fragment-retries","10","--retry-sleep","extractor:2"]
    cookie=os.getenv("CUTAI_YOUTUBE_COOKIES_FILE","").strip();use=os.getenv("CUTAI_USE_YOUTUBE_COOKIES","").strip().lower() in {"1","true","yes"};ua=os.getenv("CUTAI_YOUTUBE_USER_AGENT","").strip();proxy=_proxy()
    if use and cookie and Path(cookie).exists():cmd += ["--cookies",cookie]
    if ua:cmd += ["--user-agent",ua]
    if proxy:cmd += ["--proxy",proxy]
    cmd += ["--extractor-args",client_args];return cmd
def _resolve(url,fmt):
    clients=["youtube:player_client=web_safari,mweb;formats=missing_pot","youtube:player_client=tv,web_safari;formats=missing_pot","youtube:player_client=default,android;formats=missing_pot"]
    last=None
    for client in clients:
        for attempt in range(3):
            try:
                out=subprocess.check_output(_resolver_base(client)+["-f",fmt,"-g",url],text=True,stderr=subprocess.STDOUT,timeout=45).strip().splitlines();urls=[x.strip() for x in out if x.strip().startswith(("http://","https://"))]
                if urls:return urls
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:last=exc
            time.sleep(2*(attempt+1))
    raise RuntimeError(f"yt-dlp não conseguiu resolver a transmissão: {last}")
def _media_urls(url):
    video_fmt="bestvideo[height<=1080][fps<=30][vcodec^=avc1]/bestvideo[height<=1080][fps<=30]";audio_fmt="bestaudio[acodec^=mp4a]/bestaudio"
    try:return _resolve(url,video_fmt)[0],_resolve(url,audio_fmt)[0],"adaptive"
    except (RuntimeError,IndexError):return _resolve(url,"best[height<=1080][fps<=30]/best")[0],None,"muxed"
def _input(url):
    args=["-thread_queue_size","4096","-fflags","+genpts+discardcorrupt","-http_persistent","0","-http_multiple","0","-reconnect","1","-reconnect_streamed","1","-reconnect_delay_max","5"]
    proxy=_proxy()
    if proxy:args += ["-http_proxy",proxy]
    return args+["-i",url]
def _disk(output_dir):
    try:return shutil.disk_usage(output_dir)
    except OSError:return None
def _low_disk(output_dir):
    u=_disk(output_dir)
    if not u:return False
    mb=max(32,int(os.getenv("CUTAI_MIN_FREE_MB","96")));pct=max(1,min(50,int(os.getenv("CUTAI_MIN_FREE_PERCENT","8"))))
    return u.free<mb*1048576 or (u.total and u.free/u.total*100<pct)
def _disk_log(output_dir,label):
    u=_disk(output_dir)
    if u:print(f"[stream-capture] {label}: livre={u.free/1048576:.1f} MiB usado={(u.used/u.total*100 if u.total else 0):.1f}%",flush=True)
def _valid_segment(path,min_seconds):
    try:
        r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],text=True,capture_output=True,timeout=12)
        return r.returncode==0 and float((r.stdout or "0").strip())>=min_seconds
    except (OSError,ValueError,subprocess.TimeoutExpired):return False
def _capture_command(video,audio,target,segment_seconds,start):
    cmd=["ffmpeg","-hide_banner","-nostdin","-loglevel","warning"]+_input(video)
    if audio:cmd += _input(audio)+["-map","0:v:0","-map","1:a:0"]
    else:cmd += ["-map","0:v?","-map","0:a?"]
    pattern=target/"segment-%08d.mkv"
    return cmd+["-c","copy","-max_interleave_delta","0","-avoid_negative_ts","make_zero","-f","segment","-segment_format","matroska","-segment_time",str(segment_seconds),"-break_non_keyframes","1","-segment_start_number",str(start),"-reset_timestamps","1",str(pattern)]
def _ytdlp_capture_command(source,target,segment_seconds,start):
    """Mantém resolução e download no mesmo processo/proxy do yt-dlp.

    Evita entregar ao FFmpeg URLs googlevideo assinadas que podem ser rejeitadas
    quando o provedor de proxy troca o IP de saída entre conexões.
    """
    pattern=target/"segment-%08d.mkv";fmt="bestvideo[height<=1080][fps<=30][vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[height<=1080][fps<=30]/best"
    cmd=_resolver_base("youtube:player_client=web_safari,mweb;formats=missing_pot")
    cmd += ["--retries","infinite","--fragment-retries","infinite","--retry-sleep","fragment:2","-f",fmt,"--downloader","ffmpeg","--downloader-args",f"ffmpeg_i:-thread_queue_size 4096 -fflags +genpts+discardcorrupt -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5", "--downloader-args",f"ffmpeg_o:-c copy -max_interleave_delta 0 -avoid_negative_ts make_zero -f segment -segment_format matroska -segment_time {segment_seconds} -break_non_keyframes 1 -segment_start_number {start} -reset_timestamps 1", "-o",str(pattern),source]
    return cmd
def capture(url,output_dir,segment_seconds=30):
    validate_source_url(url)
    if segment_seconds<10 or segment_seconds>120:raise ValueError("segment_seconds deve ficar entre 10 e 120")
    output_dir.mkdir(parents=True,exist_ok=True)
    for p in output_dir.glob("*.part"):p.unlink(missing_ok=True)
    _disk_log(output_dir,"início da captura")
    if _low_disk(output_dir):print("[stream-capture] armazenamento abaixo da reserva segura; aguardando limpeza",flush=True);return 77
    proxy=_proxy();print(f"[stream-capture] iniciando captura; proxy={'configurada' if proxy else 'não configurada'}",flush=True)
    start=next_segment_number(output_dir)
    # Com proxy, deixamos o yt-dlp resolver e abrir a mídia na mesma execução.
    # Isso mantém a sessão de rede coerente para URLs assinadas do YouTube.
    if proxy:
        print(f"[stream-capture] modo=yt-dlp-proxy; conexão contínua a partir de {start:08d}",flush=True);cmd=_ytdlp_capture_command(url,output_dir,segment_seconds,start)
    else:
        try:video,audio,mode=_media_urls(url)
        except Exception as exc:print(f"[stream-capture] falha temporária ao resolver live: {type(exc).__name__}: {exc}",flush=True);return 75
        print(f"[stream-capture] modo={mode}; conexão contínua a partir de {start:08d}",flush=True);cmd=_capture_command(video,audio,output_dir,segment_seconds,start)
    process=subprocess.Popen(cmd);stopping=False;seen=set(p.name for p in output_dir.glob("segment-*.mkv"));last_publish=time.monotonic()
    def stop(*_):
        nonlocal stopping
        stopping=True
        if process.poll() is None:process.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while process.poll() is None:
            if _low_disk(output_dir):_disk_log(output_dir,"reserva atingida");process.terminate();return 77
            current=sorted(output_dir.glob("segment-*.mkv"),key=segment_number);closed=current[:-1] if len(current)>1 else []
            for p in closed:
                if p.name in seen:continue
                if _valid_segment(p,max(5,segment_seconds*0.45)):seen.add(p.name);last_publish=time.monotonic();print(f"[stream-capture] segmento {segment_number(p):08d} publicado",flush=True)
            if not stopping and time.monotonic()-last_publish>max(150,segment_seconds*5):print("[stream-capture] watchdog: captura sem novos blocos; renovando sessão yt-dlp",flush=True);process.terminate();return 76
            time.sleep(2)
        return process.returncode or 0
    finally:
        if process.poll() is None:process.terminate()
def ready_segments(output_dir,settle_seconds=2.0):
    now=time.time();files=sorted(output_dir.glob("segment-*.mkv"),key=segment_number);settled=[p for p in files if segment_number(p)>=0 and now-p.stat().st_mtime>=settle_seconds];return settled[:-1] if len(settled)>1 else []
def main():
    p=argparse.ArgumentParser(description="Captura contínua de uma live");p.add_argument("--url",required=True);p.add_argument("--output-dir",type=Path,default=Path("work/stream"));p.add_argument("--segment-seconds",type=int,default=30);a=p.parse_args();raise SystemExit(capture(a.url,a.output_dir,a.segment_seconds))
if __name__=="__main__":main()
