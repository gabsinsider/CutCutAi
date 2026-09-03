"""Consumidor do buffer contínuo com suporte a drenagem final."""
from __future__ import annotations
import argparse,json,subprocess,time
from pathlib import Path
from .pipeline import process_source
from .stream_capture import ready_segments

def _concat(segments:list[Path],output:Path)->None:
    output.parent.mkdir(parents=True,exist_ok=True); manifest=output.with_suffix('.txt'); manifest.write_text(''.join(f"file '{p.resolve().as_posix()}'\n" for p in segments),encoding='utf-8'); result=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(manifest),'-c','copy',str(output)]); manifest.unlink(missing_ok=True)
    if result.returncode!=0: raise RuntimeError('Falha ao montar janela contínua')

def _load_cursor(path:Path)->int:
    try:return max(0,int(json.loads(path.read_text()).get('cursor',0)))
    except (ValueError,OSError,TypeError):return 0

def _save(path:Path,cursor:int,last:str,status:str)->None:path.write_text(json.dumps({'cursor':cursor,'last_segment':last,'status':status},indent=2)+'\n',encoding='utf-8')

def analyze_once(url:str,stream_dir:Path,workdir:Path,cursor:int,needed:int,overlap:int,final:bool=False)->tuple[int,bool]:
    segments=ready_segments(stream_dir,0.5 if final else 2.0); available=len(segments)-cursor
    take=needed if available>=needed else (available if final and available>=2 else 0)
    if not take:return cursor,False
    selected=segments[cursor:cursor+take]; first,last=selected[0].stem,selected[-1].stem; window=workdir/f'window-{first}-{last}.mkv'; _concat(selected,window); analysis_dir=workdir/f'analysis-{first}-{last}'
    try:process_source(window,url,analysis_dir,'Live contínua')
    finally:window.unlink(missing_ok=True)
    advance=take if final and take<needed else max(1,take-overlap); cursor+=advance; _save(workdir/'stream-analyzer.json',cursor,last,'drained' if final else 'watching')
    return cursor,True

def run(url:str,stream_dir:Path,workdir:Path,segment_seconds:int=30,window_seconds:int=600,overlap_seconds:int=90,poll_seconds:int=5,stop_file:Path|None=None)->None:
    needed=max(2,window_seconds//segment_seconds); overlap=max(1,overlap_seconds//segment_seconds); workdir.mkdir(parents=True,exist_ok=True); state=workdir/'stream-analyzer.json'; cursor=_load_cursor(state)
    while True:
        final=bool(stop_file and stop_file.exists()); cursor,worked=analyze_once(url,stream_dir,workdir,cursor,needed,overlap,final)
        if final and not worked:_save(state,cursor,'','finished');return
        if not worked:time.sleep(poll_seconds)

def main()->None:
    p=argparse.ArgumentParser();p.add_argument('--url',required=True);p.add_argument('--stream-dir',type=Path,default=Path('work/stream'));p.add_argument('--workdir',type=Path,default=Path('work/live-analysis'));p.add_argument('--segment-seconds',type=int,default=30);p.add_argument('--window-seconds',type=int,default=600);p.add_argument('--overlap-seconds',type=int,default=90);p.add_argument('--poll-seconds',type=int,default=5);p.add_argument('--stop-file',type=Path,default=None);a=p.parse_args();run(a.url,a.stream_dir,a.workdir,a.segment_seconds,a.window_seconds,a.overlap_seconds,a.poll_seconds,a.stop_file)
if __name__=='__main__':main()
