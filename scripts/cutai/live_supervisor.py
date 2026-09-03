"""Supervisor resiliente do modo de live longa do CutCutAi."""
from __future__ import annotations
import argparse,json,signal,subprocess,sys,time
from datetime import UTC,datetime
from pathlib import Path

def _now():return datetime.now(UTC).isoformat()
def _write(path,**data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def _terminate(p):
    if not p or p.poll() is not None:return
    p.terminate()
    try:p.wait(timeout=15)
    except subprocess.TimeoutExpired:p.kill();p.wait()
def _capture_cmd(url,stream,segment):return [sys.executable,"-m","cutai.stream_capture","--url",url,"--output-dir",str(stream),"--segment-seconds",str(segment)]
def _analyzer_cmd(url,stream,analysis,segment,window,overlap,stop):return [sys.executable,"-m","cutai.stream_analyzer","--url",url,"--stream-dir",str(stream),"--workdir",str(analysis),"--segment-seconds",str(segment),"--window-seconds",str(window),"--overlap-seconds",str(overlap),"--stop-file",str(stop)]
def _probe_live(url,timeout=45):
    """Retorna live, ended ou unknown sem depender do processo de captura."""
    cmd=["yt-dlp","--no-playlist","--no-progress","--skip-download","--print","%(live_status)s",url]
    try:
        r=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout);out=(r.stdout+"\n"+r.stderr).lower()
    except (subprocess.TimeoutExpired,OSError):return "unknown"
    if r.returncode==0:
        statuses=[x.strip() for x in r.stdout.lower().splitlines() if x.strip()]
        if any(x=="is_live" for x in statuses):return "live"
        if any(x in {"was_live","post_live","not_live"} for x in statuses):return "ended"
    # Mensagens explícitas de encerramento são fortes; erros de rede/bot/429 não são.
    ended_terms=("live event has ended","livestream has ended","this live stream recording is not available","premiere has ended")
    if any(t in out for t in ended_terms):return "ended"
    return "unknown"

def run(url,root,segment=30,window=600,overlap=90,restarts=12):
    stream=root/"stream";analysis=root/"analysis";stop=root/"capture-ended";state=root/"supervisor.json";stream.mkdir(parents=True,exist_ok=True);stop.unlink(missing_ok=True)
    shutdown=False;started_at=_now();disconnects=0;consecutive_failures=0;ended_confirmations=0;capture=None;analyzer=None
    def request(*_):
        nonlocal shutdown;shutdown=True
    signal.signal(signal.SIGINT,request);signal.signal(signal.SIGTERM,request)
    analyzer=subprocess.Popen(_analyzer_cmd(url,stream,analysis,segment,window,overlap,stop));_write(state,status="starting",url=url,started_at=started_at,capture_restarts=0,analyzer_pid=analyzer.pid)
    try:
        while not shutdown:
            if analyzer.poll() is not None:analyzer=subprocess.Popen(_analyzer_cmd(url,stream,analysis,segment,window,overlap,stop))
            capture_started=time.monotonic();capture=subprocess.Popen(_capture_cmd(url,stream,segment));_write(state,status="capturing",url=url,started_at=started_at,capture_restarts=disconnects,capture_pid=capture.pid,analyzer_pid=analyzer.pid,connected_at=_now(),end_confirmations=ended_confirmations)
            code=capture.wait();lived_for=time.monotonic()-capture_started
            if shutdown:break
            disconnects+=1;consecutive_failures=0 if lived_for>=max(30,segment) else consecutive_failures+1
            probe=_probe_live(url)
            if probe=="live":ended_confirmations=0;consecutive_failures=0
            elif probe=="ended":ended_confirmations+=1
            else:ended_confirmations=0
            _write(state,status="checking_end" if probe=="ended" else "reconnecting",url=url,started_at=started_at,last_disconnect=_now(),capture_exit=code,last_connection_seconds=round(lived_for,1),capture_restarts=disconnects,consecutive_failures=consecutive_failures,live_probe=probe,end_confirmations=ended_confirmations,analyzer_pid=analyzer.pid if analyzer.poll() is None else None)
            # Duas confirmações explícitas evitam confundir um erro isolado com fim real.
            if ended_confirmations>=2:
                _write(state,status="draining",reason="live_ended",url=url,started_at=started_at,ended_at=_now(),capture_exit=code,capture_restarts=disconnects,end_confirmations=ended_confirmations);break
            # Muitas falhas rápidas com estado desconhecido continuam protegidas pelo
            # limite antigo, mas são marcadas como inacessíveis em vez de "live terminou".
            if consecutive_failures>restarts:
                _write(state,status="draining",reason="source_unreachable",url=url,started_at=started_at,ended_at=_now(),capture_exit=code,capture_restarts=disconnects,consecutive_failures=consecutive_failures);break
            delay=8 if probe=="ended" else min(20,2+consecutive_failures*3);time.sleep(delay)
        stop.touch()
        if analyzer and analyzer.poll() is None:
            try:analyzer.wait(timeout=max(180,window*2))
            except subprocess.TimeoutExpired:_terminate(analyzer)
        final="stopped" if shutdown else "finished";analyzer_exit=analyzer.poll() if analyzer else None;_write(state,status=final,reason="user_stop" if shutdown else ("live_ended" if ended_confirmations>=2 else "source_unreachable"),url=url,started_at=started_at,ended_at=_now(),capture_restarts=disconnects,analyzer_exit=analyzer_exit);return 0 if analyzer_exit in (0,None) else int(analyzer_exit)
    finally:_terminate(capture);stop.touch();_terminate(analyzer)
def main():
    p=argparse.ArgumentParser(description="Supervisor persistente de live");p.add_argument("--url",required=True);p.add_argument("--root",type=Path,default=Path("work/continuous-live"));p.add_argument("--segment-seconds",type=int,default=30);p.add_argument("--window-seconds",type=int,default=600);p.add_argument("--overlap-seconds",type=int,default=90);p.add_argument("--capture-restarts",type=int,default=12);a=p.parse_args();raise SystemExit(run(a.url,a.root,a.segment_seconds,a.window_seconds,a.overlap_seconds,a.capture_restarts))
if __name__=="__main__":main()
