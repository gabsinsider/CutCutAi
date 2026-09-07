"""Captura contínua em blocos independentes e atômicos para lives longas."""
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
def _input(url):return ["-thread_queue_size","4096","-fflags","+genpts+discardcorrupt","-http_persistent","0","-http_multiple","0","-reconnect","1","-reconnect_streamed","1","-reconnect_delay_max","5","-i",url]
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
def _block_command(video,audio,target,seconds):
    cmd=["ffmpeg","-hide_banner","-nostdin","-loglevel","warning"]+_input(video)
    if audio:cmd += _input(audio)+["-map","0:v:0","-map","1:a:0"]
    else:cmd += ["-map","0:v?","-map","0:a?"]
    # Cada bloco possui seu próprio processo/arquivo. Não dependemos do muxer segment,
    # de GOPs ou dos timestamps HLS para fechar e publicar um segmento.
    return cmd+["-t",str(seconds),"-c","copy","-max_interleave_delta","0","-avoid_negative_ts","make_zero","-f","matroska",str(target)]
def capture(url,output_dir,segment_seconds=30):
    validate_source_url(url)
    if segment_seconds<10 or segment_seconds>120:raise ValueError("segment_seconds deve ficar entre 10 e 120")
    output_dir.mkdir(parents=True,exist_ok=True)
    for p in output_dir.glob("*.part"):
        try:p.unlink()
        except OSError:pass
    _disk_log(output_dir,"início da captura")
    if _low_disk(output_dir):print("[stream-capture] armazenamento abaixo da reserva segura; aguardando limpeza",flush=True);return 77
    print(f"[stream-capture] resolvendo transmissão; proxy={'configurada' if _proxy() else 'não configurada'}",flush=True)
    try:video,audio,mode=_media_urls(url)
    except Exception as exc:print(f"[stream-capture] falha temporária ao resolver live: {type(exc).__name__}: {exc}",flush=True);return 75
    number=next_segment_number(output_dir);stopping=False;process=None
    print(f"[stream-capture] modo={mode}; captura atômica a partir de {number:08d}",flush=True)
    def stop(*_):
        nonlocal stopping,process
        stopping=True
        if process and process.poll() is None:process.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while not stopping:
        if _low_disk(output_dir):_disk_log(output_dir,"reserva atingida");return 77
        part=output_dir/f"segment-{number:08d}.mkv.part";final=output_dir/f"segment-{number:08d}.mkv"
        started=time.monotonic();process=subprocess.Popen(_block_command(video,audio,part,segment_seconds))
        try:code=process.wait(timeout=segment_seconds+90)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
            code=76
        if stopping:
            part.unlink(missing_ok=True);break
        elapsed=time.monotonic()-started
        if code==0 and part.exists() and _valid_segment(part,max(5,segment_seconds*0.55)):
            part.replace(final);print(f"[stream-capture] segmento {number:08d} publicado ({elapsed:.1f}s)",flush=True);number+=1;continue
        part.unlink(missing_ok=True)
        print(f"[stream-capture] bloco falhou/expirou (exit={code}, {elapsed:.1f}s); renovando URLs",flush=True)
        return 76
    return 0
def ready_segments(output_dir,settle_seconds=1.0):
    now=time.time();files=sorted(output_dir.glob("segment-*.mkv"),key=segment_number);return [p for p in files if segment_number(p)>=0 and now-p.stat().st_mtime>=settle_seconds]
def main():
    p=argparse.ArgumentParser(description="Captura contínua atômica de uma live");p.add_argument("--url",required=True);p.add_argument("--output-dir",type=Path,default=Path("work/stream"));p.add_argument("--segment-seconds",type=int,default=30);a=p.parse_args();raise SystemExit(capture(a.url,a.output_dir,a.segment_seconds))
if __name__=="__main__":main()
