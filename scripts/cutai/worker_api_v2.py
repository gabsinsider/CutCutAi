"""Extensão do worker: GitHub Releases como armazenamento permanente dos cortes."""
from __future__ import annotations
import json,time,urllib.error,urllib.request
from pathlib import Path
from . import worker_api as base

ROOT=base.ROOT
ARCHIVED=ROOT/"archived-ranking.json"
REPO="gabsinsider/CutCutAi"
_original_ranking=base._ranking
_original_cleanup=base._cleanup_storage

def _load_archived():
    try:return json.loads(ARCHIVED.read_text(encoding="utf-8")).get("clips",[])
    except (OSError,ValueError,TypeError):return []

def _save_archived(rows):
    base._write_json(ARCHIVED,{"clips":rows})

def _release(cid):
    url=f"https://api.github.com/repos/{REPO}/releases/tags/liveclip-{cid}"
    req=urllib.request.Request(url,headers={"Accept":"application/vnd.github+json","User-Agent":"CutCutAi-worker"})
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
        assets={str(a.get("name","")):str(a.get("browser_download_url","")) for a in rel.get("assets",[]) if a.get("browser_download_url")}
        mp4=assets.get(f"{cid}.mp4")
        if not mp4:continue
        remote=dict(row);remote["asset_url"]=mp4
        if assets.get(f"{cid}.jpg"):remote["thumbnail_url"]=assets[f"{cid}.jpg"]
        if assets.get(f"{cid}.captions.json"):remote["captions_url"]=assets[f"{cid}.captions.json"]
        remote["archived"]=True;remote["archive_tag"]=f"liveclip-{cid}";archived[cid]=remote
        # Só removemos o local depois de confirmar que o MP4 existe no Release.
        for path in found.values():
            try:freed+=path.stat().st_size;path.unlink();removed+=1
            except OSError:pass
    if removed:
        _save_archived(list(archived.values()));print(f"[archive-reaper] {removed} arquivo(s) locais removidos após confirmação no GitHub; {freed/1048576:.1f} MiB liberados",flush=True)
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
        except Exception as exc:print(f"[worker-api] manutenção de armazenamento falhou: {type(exc).__name__}: {exc}",flush=True)
        archive_tick+=interval
        if archive_tick>=60:
            archive_tick=0
            try:_archive_reap()
            except Exception as exc:print(f"[archive-reaper] falha recuperável: {type(exc).__name__}: {exc}",flush=True)

base._ranking=_ranking
base._maintenance_loop=_maintenance_loop

if __name__=="__main__":
    base.main()
