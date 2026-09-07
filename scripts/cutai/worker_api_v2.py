"""Extensão do worker: Releases permanentes + diagnóstico público de operação."""
from __future__ import annotations
import json,time,urllib.error,urllib.request
from datetime import UTC,datetime
from pathlib import Path
from urllib.parse import urlparse
from . import worker_api as base

ROOT=base.ROOT
ARCHIVED=ROOT/"archived-ranking.json"
REPO="gabsinsider/CutCutAi"
_original_ranking=base._ranking
_original_cleanup=base._cleanup_storage
_original_do_get=base.Handler.do_GET

def _load_archived():
    try:return json.loads(ARCHIVED.read_text(encoding="utf-8")).get("clips",[])
    except (OSError,ValueError,TypeError):return []
def _save_archived(rows):base._write_json(ARCHIVED,{"clips":rows})
def _release(cid):
    req=urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/tags/liveclip-{cid}",headers={"Accept":"application/vnd.github+json","User-Agent":"CutCutAi-worker"})
    try:
        with urllib.request.urlopen(req,timeout=12) as r:return json.load(r)
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError):return None
def _archive_reap():
    local=_original_ranking(None).get("clips",[]);archived={str(x.get("id")):dict(x) for x in _load_archived()};files=base._clip_files();removed=0;freed=0
    for row in local:
        cid=str(row.get("id","")).strip();found=files.get(cid,{})
        if not cid or "asset" not in found:continue
        rel=_release(cid)
        if not rel:continue
        assets={str(a.get("name","")):str(a.get("browser_download_url","")) for a in rel.get("assets",[]) if a.get("browser_download_url")};mp4=assets.get(f"{cid}.mp4")
        if not mp4:continue
        remote=dict(row);remote["asset_url"]=mp4
        if assets.get(f"{cid}.jpg"):remote["thumbnail_url"]=assets[f"{cid}.jpg"]
        if assets.get(f"{cid}.captions.json"):remote["captions_url"]=assets[f"{cid}.captions.json"]
        remote["archived"]=True;remote["archive_tag"]=f"liveclip-{cid}";archived[cid]=remote
        for path in found.values():
            try:freed+=path.stat().st_size;path.unlink();removed+=1
            except OSError:pass
    if removed:_save_archived(list(archived.values()));print(f"[archive-reaper] {removed} arquivo(s) removidos; {freed/1048576:.1f} MiB liberados",flush=True)
    return removed
def _ranking(handler=None):
    current=_original_ranking(handler).get("clips",[]);merged={str(x.get("id")):dict(x) for x in _load_archived()}
    for row in current:merged[str(row.get("id"))]=row
    clips=list(merged.values());clips.sort(key=lambda c:str(c.get("created_at","")),reverse=True);return {"clips":clips,"count":len(clips)}
def _maintenance_loop():
    interval=max(20,int(base.os.getenv("CUTAI_STORAGE_CHECK_SECONDS","30")));archive_tick=0
    while True:
        time.sleep(interval)
        try:_original_cleanup()
        except Exception as exc:print(f"[worker-api] manutenção falhou: {type(exc).__name__}: {exc}",flush=True)
        archive_tick+=interval
        if archive_tick>=60:
            archive_tick=0
            try:_archive_reap()
            except Exception as exc:print(f"[archive-reaper] falha recuperável: {type(exc).__name__}: {exc}",flush=True)
def _diagnostics():
    state=base._state();root=base._session_root();stream=root/"stream" if root else None
    ready=[];parts=[]
    if stream and stream.exists():
        ready=sorted(stream.glob("segment-*.mkv"));parts=sorted(stream.glob("segment-*.mkv.part"))
    def info(p):
        try:return {"name":p.name,"size_mb":round(p.stat().st_size/1048576,2),"age_seconds":round(max(0,time.time()-p.stat().st_mtime),1)}
        except OSError:return {"name":p.name}
    return {"ok":True,"checked_at":datetime.now(UTC).isoformat(),"deploy_commit":base.os.getenv("RAILWAY_GIT_COMMIT_SHA") or base.os.getenv("GIT_COMMIT_SHA"),"state":state,"capture":{"ready_segments":len(ready),"partial_segments":len(parts),"latest_ready":info(ready[-1]) if ready else None,"latest_partial":info(parts[-1]) if parts else None},"clips":{"local":len(base._clip_files()),"archived":len(_load_archived())}}
def _do_get(self):
    if urlparse(self.path).path=="/diagnostics":self._send(200,_diagnostics());return
    return _original_do_get(self)

base._ranking=_ranking
base._maintenance_loop=_maintenance_loop
base.Handler.do_GET=_do_get

if __name__=="__main__":base.main()
