"""Consumidor resiliente do buffer contínuo com limpeza e deduplicação."""
from __future__ import annotations
import argparse,json,os,re,subprocess,time
from pathlib import Path
from .pipeline import process_source
from .stream_capture import ready_segments,segment_number

def _log(m):print(f"[stream-analyzer] {m}",flush=True)
def _concat(segments,output):
    output.parent.mkdir(parents=True,exist_ok=True);manifest=output.with_suffix(".txt");manifest.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments),encoding="utf-8");r=subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y","-f","concat","-safe","0","-i",str(manifest),"-c","copy",str(output)]);manifest.unlink(missing_ok=True)
    if r.returncode!=0:raise RuntimeError("Falha ao montar janela contínua")
def _load_cursor(path):
    try:
        data=json.loads(path.read_text());return max(0,int(data.get("next_segment",data.get("cursor",0))))
    except (ValueError,OSError,TypeError,json.JSONDecodeError):return 0
def _save(path,**data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def _available(stream_dir,next_segment):return [p for p in ready_segments(stream_dir) if segment_number(p)>=next_segment]
def _cleanup(stream_dir,keep_from):
    removed=0
    for p in stream_dir.glob("segment-*.mkv"):
        n=segment_number(p)
        if 0<=n<keep_from:
            try:p.unlink();removed+=1
            except OSError:pass
    if removed:_log(f"buffer limpo: {removed} segmento(s) antigo(s) removido(s)")

def _tokens(text):return {w for w in re.findall(r"[\wÀ-ÿ]+",(text or "").lower()) if len(w)>=4}
def _similar(a,b):
    aa,bb=_tokens(a),_tokens(b)
    if not aa or not bb:return 0.0
    return len(aa&bb)/max(1,min(len(aa),len(bb)))
def _load_dedup(path):
    try:return json.loads(path.read_text(encoding="utf-8")).get("clips",[])
    except (OSError,ValueError,TypeError):return []
def _remove_from_ranking(ids):
    ranking=Path(os.getenv("CUTAI_RANKING_PATH",str(Path(os.getenv("CUTAI_DATA_ROOT","/data/cutcutai"))/"ranking.json")))
    try:data=json.loads(ranking.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError):return
    data["clips"]=[c for c in data.get("clips",[]) if c.get("id") not in ids];ranking.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def _deduplicate(clips,analysis_dir,workdir):
    """Remove apenas duplicatas da MESMA sessão, causadas pelo overlap das janelas."""
    state=workdir/"dedup.json";history=_load_dedup(state);kept=[];removed=[]
    for clip in clips:
        transcript=getattr(clip,"transcript","") or ""
        duplicate=next((old for old in history if _similar(transcript,old.get("transcript",""))>=0.72),None)
        if duplicate:
            removed.append(clip.id)
            for suffix in (".mp4",".jpg",".captions.json"):(analysis_dir/f"{clip.id}{suffix}").unlink(missing_ok=True)
            _log(f"duplicata de overlap descartada: {clip.id} ≈ {duplicate.get('id')}")
        else:
            kept.append(clip);history.append({"id":clip.id,"transcript":transcript,"created_at":getattr(clip,"created_at","")})
    # Só precisamos de um histórico recente da sessão; evita crescimento indefinido.
    _save(state,clips=history[-120:])
    if removed:_remove_from_ranking(set(removed))
    return kept

def analyze_once(url,stream_dir,workdir,next_segment,needed,overlap,final=False):
    segments=_available(stream_dir,next_segment);available=len(segments);state=workdir/"stream-analyzer.json";_save(state,next_segment=next_segment,status="waiting" if available<needed else "ready",available_segments=available,needed_segments=needed,final=final)
    take=needed if available>=needed else (available if final and available>=2 else 0)
    if not take:return next_segment,False
    selected=segments[:take];first,last=selected[0].stem,selected[-1].stem;window=workdir/f"window-{first}-{last}.mkv";analysis_dir=workdir/f"analysis-{first}-{last}";_log(f"montando janela {first}..{last} com {take} segmentos");_concat(selected,window);_save(state,next_segment=next_segment,status="analyzing",available_segments=available,needed_segments=needed,first_segment=first,last_segment=last)
    try:
        clips=process_source(window,url,analysis_dir,"Live contínua");clips=_deduplicate(clips,analysis_dir,workdir);_log(f"janela analisada: {len(clips)} corte(s) novo(s) gerado(s)")
    except Exception as exc:
        _log(f"erro na janela {first}..{last}: {type(exc).__name__}: {exc}");_save(state,next_segment=next_segment,status="analysis_error",error=str(exc),first_segment=first,last_segment=last)
    finally:window.unlink(missing_ok=True)
    advance=take if final and take<needed else max(1,take-overlap);new_next=segment_number(selected[advance]) if advance<len(selected) else segment_number(selected[-1])+1;_cleanup(stream_dir,new_next);_save(state,next_segment=new_next,last_segment=last,status="drained" if final else "watching",needed_segments=needed);return new_next,True

def run(url,stream_dir,workdir,segment_seconds=30,window_seconds=600,overlap_seconds=90,poll_seconds=5,stop_file=None):
    needed=max(2,window_seconds//segment_seconds);overlap=max(1,overlap_seconds//segment_seconds);workdir.mkdir(parents=True,exist_ok=True);state=workdir/"stream-analyzer.json";next_segment=_load_cursor(state);last_report=0.0;_log(f"iniciado: precisa de {needed} segmentos, overlap={overlap}, próximo={next_segment}")
    existing=ready_segments(stream_dir)
    if existing and next_segment>segment_number(existing[-1])+1:next_segment=segment_number(existing[0])
    while True:
        final=bool(stop_file and stop_file.exists())
        try:next_segment,worked=analyze_once(url,stream_dir,workdir,next_segment,needed,overlap,final)
        except Exception as exc:_log(f"erro recuperável no consumidor: {type(exc).__name__}: {exc}");_save(state,next_segment=next_segment,status="consumer_error",error=str(exc),final=final);worked=False
        if final and not worked:_save(state,next_segment=next_segment,status="finished");_log("buffer drenado; finalizando");return
        now=time.monotonic()
        if not worked and now-last_report>=30:
            available=len(_available(stream_dir,next_segment));_log(f"aguardando buffer: {available}/{needed} segmentos disponíveis (próximo={next_segment})");last_report=now
        if not worked:time.sleep(poll_seconds)
def main():
    p=argparse.ArgumentParser();p.add_argument("--url",required=True);p.add_argument("--stream-dir",type=Path,default=Path("work/stream"));p.add_argument("--workdir",type=Path,default=Path("work/live-analysis"));p.add_argument("--segment-seconds",type=int,default=30);p.add_argument("--window-seconds",type=int,default=600);p.add_argument("--overlap-seconds",type=int,default=90);p.add_argument("--poll-seconds",type=int,default=5);p.add_argument("--stop-file",type=Path,default=None);a=p.parse_args();run(a.url,a.stream_dir,a.workdir,a.segment_seconds,a.window_seconds,a.overlap_seconds,a.poll_seconds,a.stop_file)
if __name__=="__main__":main()
