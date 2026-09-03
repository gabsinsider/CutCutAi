"""Supervisor do modo de live longa do CutCutAi."""
from __future__ import annotations
import argparse,json,signal,subprocess,sys,time
from datetime import UTC,datetime
from pathlib import Path

def _now():return datetime.now(UTC).isoformat()
def _write(path:Path,**data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def _terminate(p:subprocess.Popen|None):
    if not p or p.poll() is not None:return
    p.terminate()
    try:p.wait(timeout=15)
    except subprocess.TimeoutExpired:p.kill();p.wait()
def _capture_cmd(url,stream_dir,segment):return [sys.executable,'-m','cutai.stream_capture','--url',url,'--output-dir',str(stream_dir),'--segment-seconds',str(segment)]
def _analyzer_cmd(url,stream_dir,analysis_dir,segment,window,overlap,stop):return [sys.executable,'-m','cutai.stream_analyzer','--url',url,'--stream-dir',str(stream_dir),'--workdir',str(analysis_dir),'--segment-seconds',str(segment),'--window-seconds',str(window),'--overlap-seconds',str(overlap),'--stop-file',str(stop)]
def run(url:str,root:Path,segment:int=30,window:int=600,overlap:int=90,restarts:int=4)->int:
    stream=root/'stream';analysis=root/'analysis';stop=root/'capture-ended';state=root/'supervisor.json';stream.mkdir(parents=True,exist_ok=True);stop.unlink(missing_ok=True);shutdown=False
    def request(*_):
        nonlocal shutdown;shutdown=True
    signal.signal(signal.SIGINT,request);signal.signal(signal.SIGTERM,request)
    analyzer=subprocess.Popen(_analyzer_cmd(url,stream,analysis,segment,window,overlap,stop));capture=None;attempt=0
    _write(state,status='starting',url=url,started_at=_now(),capture_restarts=0)
    try:
        while not shutdown:
            capture=subprocess.Popen(_capture_cmd(url,stream,segment));_write(state,status='capturing',url=url,started_at=_now(),capture_restarts=attempt)
            code=capture.wait()
            if shutdown:break
            # Uma queda pode ser rede/plataforma. Tentamos religar algumas vezes antes de
            # considerar a transmissão encerrada. Os segmentos já gravados permanecem.
            attempt+=1
            if attempt>restarts:
                _write(state,status='draining',url=url,ended_at=_now(),capture_exit=code,capture_restarts=attempt-1);break
            _write(state,status='reconnecting',url=url,last_disconnect=_now(),capture_exit=code,capture_restarts=attempt);time.sleep(min(30,5*attempt))
        stop.touch()
        if analyzer.poll() is None:
            try:analyzer.wait(timeout=max(180,window*2))
            except subprocess.TimeoutExpired:_terminate(analyzer)
        final='stopped' if shutdown else 'finished';_write(state,status=final,url=url,ended_at=_now(),capture_restarts=attempt,analyzer_exit=analyzer.poll());return 0 if analyzer.poll() in (0,None) else int(analyzer.poll())
    finally:
        _terminate(capture);stop.touch();_terminate(analyzer)
def main():
    p=argparse.ArgumentParser(description='Supervisor persistente de live');p.add_argument('--url',required=True);p.add_argument('--root',type=Path,default=Path('work/continuous-live'));p.add_argument('--segment-seconds',type=int,default=30);p.add_argument('--window-seconds',type=int,default=600);p.add_argument('--overlap-seconds',type=int,default=90);p.add_argument('--capture-restarts',type=int,default=4);a=p.parse_args();raise SystemExit(run(a.url,a.root,a.segment_seconds,a.window_seconds,a.overlap_seconds,a.capture_restarts))
if __name__=='__main__':main()
