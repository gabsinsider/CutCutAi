"""Captura contínua segmentada, resiliente a reconexões e com vídeo/áudio adaptativos."""
from __future__ import annotations
import argparse,csv,os,re,shutil,signal,subprocess,time
from pathlib import Path
from .proxy import normalize_proxy_url
from .validation import validate_source_url
SEGMENT_RE=re.compile(r"^segment-(\d+)\.mkv$")
def segment_number(path):
    m=SEGMENT_RE.match(path.name);return int(m.group(1)) if m else -1
def next_segment_number(output_dir):
    nums=[segment_number(p) for p in output_dir.glob("segment-*.mkv")];nums += [int(m.group(1)) for p in output_dir.glob("segment-*.mkv.part") if (m:=re.match(r"^segment-(\d+)\.mkv\.part$",p.name))];return max([n for n in nums if n>=0],default=-1)+1
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
    raise RuntimeError(f"yt-dlp não conseguiu resolver a transmissão após múltiplos clientes/tentativas: {last}")
def _media_urls(url):
    video_fmt="bestvideo[height<=1080][fps<=30][vcodec^=avc1]/bestvideo[height<=1080][fps<=30]";audio_fmt="bestaudio[acodec^=mp4a]/bestaudio"
    try:return _resolve(url,video_fmt)[0],_resolve(url,audio_fmt)[0],"adaptive"
    except (RuntimeError,IndexError):return _resolve(url,"best[height<=1080][fps<=30]/best")[0],None,"muxed"
def _input(url):return ["-thread_queue_size","4096","-http_persistent","0","-http_multiple","0","-reconnect","1","-reconnect_streamed","1","-reconnect_delay_max","5","-i",url]
def _remove_incomplete(output_dir):
    removed=0
    for p in output_dir.glob("*.part"):
        try:p.unlink();removed+=1
        except OSError:pass
    (output_dir/"completed.csv").unlink(missing_ok=True)
    if removed:print(f"[stream-capture] removidos {removed} segmento(s) incompleto(s)",flush=True)
def _disk(output_dir):
    try:return shutil.disk_usage(output_dir)
    except OSError:return None
def _disk_log(output_dir,label="armazenamento"):
    usage=_disk(output_dir)
    if not usage:return
    print(f"[stream-capture] {label}: livre={usage.free/1024/1024:.1f} MiB usado={(usage.used/usage.total*100 if usage.total else 0):.1f}%",flush=True)
def _low_disk(output_dir):
    usage=_disk(output_dir)
    if not usage:return False
    reserve_mb=max(32,int(os.getenv("CUTAI_MIN_FREE_MB","96")))
    reserve_pct=max(1,min(50,int(os.getenv("CUTAI_MIN_FREE_PERCENT","8"))))
    return usage.free < reserve_mb*1024*1024 or (usage.total and usage.free/usage.total*100 < reserve_pct)
def build_command(url,output_dir,segment_seconds):
    output_dir.mkdir(parents=True,exist_ok=True);pattern=output_dir/"segment-%08d.mkv.part";completed=output_dir/"completed.csv";start=next_segment_number(output_dir);print(f"[stream-capture] resolvendo transmissão; proxy={'configurada' if _proxy() else 'não configurada'}",flush=True);video,audio,mode=_media_urls(url);print(f"[stream-capture] modo={mode}; iniciando no segmento {start:08d}",flush=True);cmd=["ffmpeg","-hide_banner","-nostdin","-loglevel","warning"]+_input(video)
    if audio:cmd += _input(audio)+["-map","0:v:0","-map","1:a:0"]
    else:cmd += ["-map","0:v?","-map","0:a?"]
    cmd += ["-c","copy","-max_interleave_delta","0","-f","segment","-segment_format","matroska","-segment_time",str(segment_seconds),"-segment_start_number",str(start),"-segment_list",str(completed),"-segment_list_type","csv","-reset_timestamps","1",str(pattern)];return cmd
def _completed_names(output_dir):
    path=output_dir/"completed.csv"
    try:
        with path.open(newline="",encoding="utf-8") as fh:return {Path(row[0]).name for row in csv.reader(fh) if row}
    except OSError:return set()
def _promote_finished(output_dir):
    promoted=0
    for name in _completed_names(output_dir):
        if not name.endswith(".mkv.part"):continue
        p=output_dir/name
        if not p.is_file():continue
        target=Path(str(p)[:-5])
        try:
            r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(p)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            if r.returncode==0:p.replace(target);promoted+=1
        except (OSError,subprocess.TimeoutExpired):pass
    if promoted:print(f"[stream-capture] {promoted} segmento(s) finalizado(s) publicados",flush=True)
    return promoted
def _capture_activity(output_dir):
    """Assinatura barata do progresso real do FFmpeg, inclusive do segmento .part em escrita."""
    newest_mtime=0;part_size=0;completed_size=0
    try:
        for p in output_dir.glob("segment-*.mkv.part"):
            st=p.stat();newest_mtime=max(newest_mtime,st.st_mtime_ns);part_size=max(part_size,st.st_size)
        completed=output_dir/"completed.csv"
        if completed.exists():
            st=completed.stat();newest_mtime=max(newest_mtime,st.st_mtime_ns);completed_size=st.st_size
    except OSError:pass
    return newest_mtime,part_size,completed_size
def capture(url,output_dir,segment_seconds=30):
    validate_source_url(url)
    if segment_seconds<10 or segment_seconds>120:raise ValueError("segment_seconds deve ficar entre 10 e 120")
    output_dir.mkdir(parents=True,exist_ok=True);_remove_incomplete(output_dir);_disk_log(output_dir,"início da captura")
    if _low_disk(output_dir):
        print("[stream-capture] armazenamento abaixo da reserva segura; aguardando limpeza antes de capturar",flush=True);return 77
    try:command=build_command(url,output_dir,segment_seconds)
    except Exception as exc:
        print(f"[stream-capture] falha temporária ao resolver live: {type(exc).__name__}: {exc}",flush=True);return 75
    process=subprocess.Popen(command);stopping=False;last_progress=time.monotonic();last_disk_check=0.0;stall_seconds=max(240,segment_seconds*8);activity=_capture_activity(output_dir)
    def stop(*_):
        nonlocal stopping
        if stopping:return
        stopping=True;process.terminate()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        while process.poll() is None:
            promoted=_promote_finished(output_dir);current_activity=_capture_activity(output_dir)
            if promoted or current_activity!=activity:last_progress=time.monotonic();activity=current_activity
            now=time.monotonic()
            if not stopping and now-last_disk_check>=10:
                last_disk_check=now
                if _low_disk(output_dir):
                    _disk_log(output_dir,"reserva atingida")
                    print("[stream-capture] pausando captura antes de encher o volume",flush=True);process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                    return 77
            if not stopping and now-last_progress>=stall_seconds:
                print(f"[stream-capture] watchdog: nenhuma atividade de captura por {stall_seconds}s; renovando URLs da live",flush=True)
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                return 76
            time.sleep(1)
        _promote_finished(output_dir);return process.returncode
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill()
        _promote_finished(output_dir)
        for p in output_dir.glob("*.part"):
            try:p.unlink()
            except OSError:pass
def ready_segments(output_dir,settle_seconds=1.0):
    now=time.time();files=sorted(output_dir.glob("segment-*.mkv"),key=segment_number);return [p for p in files if segment_number(p)>=0 and now-p.stat().st_mtime>=settle_seconds]
def main():
    p=argparse.ArgumentParser(description="Captura contínua segmentada de uma live");p.add_argument("--url",required=True);p.add_argument("--output-dir",type=Path,default=Path("work/stream"));p.add_argument("--segment-seconds",type=int,default=30);a=p.parse_args();raise SystemExit(capture(a.url,a.output_dir,a.segment_seconds))
if __name__=="__main__":main()
