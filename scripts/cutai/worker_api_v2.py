"""Extensão do worker: Releases permanentes, diagnóstico e exportação direta."""
from __future__ import annotations
import json,re,subprocess,sys,threading,time,urllib.error,urllib.request
from datetime import UTC,datetime
from pathlib import Path
from urllib.parse import urlparse
from . import worker_api as base
ROOT=base.ROOT;ARCHIVED=ROOT/"archived-ranking.json";EXPORT_ROOT=ROOT/"exports";EXPORT_STATE=ROOT/"export-jobs.json";REPO="gabsinsider/CutCutAi"
_original_ranking=base._ranking;_original_cleanup=base._cleanup_storage;_original_do_get=base.Handler.do_GET;_original_do_post=base.Handler.do_POST
_export_lock=threading.Lock()
def _load_export_jobs():
    try:
        rows=json.loads(EXPORT_STATE.read_text(encoding="utf-8")).get("jobs",[])
        return {str(x.get("id")):dict(x) for x in rows if x.get("id")}
    except (OSError,ValueError,TypeError):return {}
_export_jobs=_load_export_jobs()
def _save_export_jobs():
    with _export_lock:
        rows=list(_export_jobs.values())[-100:]
        base._write_json(EXPORT_STATE,{"jobs":rows})
def _update_job(jid,values):
    with _export_lock:_export_jobs[jid].update(values)
    _save_export_jobs()
def _recover_export_jobs():
    changed=False
    for jid,job in list(_export_jobs.items()):
        if job.get("status")!='processing':continue
        path=EXPORT_ROOT/f"{jid}.mp4"
        if path.exists() and path.stat().st_size>1024:job.update({"status":"ready","url":f"/exports/{jid}.mp4","recovered_at":datetime.now(UTC).isoformat()})
        else:job.update({"status":"failed","error":"A exportação foi interrompida por uma reinicialização do worker. Tente novamente.","finished_at":datetime.now(UTC).isoformat()})
        changed=True
    if changed:_save_export_jobs()
def _exports_for_archive():
    rows=[]
    for job in _export_jobs.values():
        jid=str(job.get("id","")).strip();cid=str(job.get("clip_id","")).strip();status=str(job.get("status","")).strip();url=str(job.get("url","")).strip()
        if jid and cid and status=="ready" and url==f"/exports/{jid}.mp4" and (EXPORT_ROOT/f"{jid}.mp4").exists():rows.append({"id":jid,"clip_id":cid,"status":status,"url":url,"created_at":job.get("created_at"),"finished_at":job.get("finished_at")})
    rows.sort(key=lambda x:str(x.get("created_at",'')))
    return {"exports":rows,"count":len(rows)}
def _load_archived():
    try:return json.loads(ARCHIVED.read_text(encoding="utf-8")).get("clips",[])
    except (OSError,ValueError,TypeError):return []
def _save_archived(rows):base._write_json(ARCHIVED,{"clips":rows})
def _release_tag(tag):
    req=urllib.request.Request(f"https://api.github.com/repos/{REPO}/releases/tags/{tag}",headers={"Accept":"application/vnd.github+json","User-Agent":"CutCutAi-worker"})
    try:
        with urllib.request.urlopen(req,timeout=12) as r:return json.load(r)
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,ValueError):return None
def _archive_reap():
    local=_original_ranking(None).get("clips",[]);archived={str(x.get("id")):dict(x) for x in _load_archived()};files=base._clip_files();removed=0;freed=0
    for row in local:
        cid=str(row.get("id","")).strip();found=files.get(cid,{})
        if not cid or "asset" not in found:continue
        rel=_release_tag(f"liveclip-{cid}")
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
def _export_reap():
    removed=0;freed=0;changed=False
    for jid,job in list(_export_jobs.items()):
        if job.get("status")!="ready":continue
        path=EXPORT_ROOT/f"{jid}.mp4";rel=_release_tag(f"export-{jid}")
        if not rel:continue
        asset=next((a for a in rel.get("assets",[]) if a.get("name")==f"{jid}.mp4" and a.get("browser_download_url")),None)
        if not asset:continue
        job.update({"status":"archived","url":str(asset["browser_download_url"]),"archived_at":datetime.now(UTC).isoformat(),"archive_tag":f"export-{jid}"});changed=True
        if path.exists():
            try:freed+=path.stat().st_size;path.unlink();removed+=1
            except OSError:pass
    if changed:_save_export_jobs()
    if removed:print(f"[export-reaper] {removed} exportação(ões) removida(s); {freed/1048576:.1f} MiB liberados",flush=True)
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
            try:_archive_reap();_export_reap()
            except Exception as exc:print(f"[archive-reaper] falha recuperável: {type(exc).__name__}: {exc}",flush=True)
def _diagnostics():
    state=base._state();root=base._session_root();stream=root/"stream" if root else None;ready=[];parts=[]
    if stream and stream.exists():ready=sorted(stream.glob("segment-*.mkv"));parts=sorted(stream.glob("segment-*.mkv.part"))
    def info(p):
        try:return {"name":p.name,"size_mb":round(p.stat().st_size/1048576,2),"age_seconds":round(max(0,time.time()-p.stat().st_mtime),1)}
        except OSError:return {"name":p.name}
    return {"ok":True,"checked_at":datetime.now(UTC).isoformat(),"deploy_commit":base.os.getenv("RAILWAY_GIT_COMMIT_SHA") or base.os.getenv("GIT_COMMIT_SHA"),"state":state,"capture":{"ready_segments":len(ready),"partial_segments":len(parts),"latest_ready":info(ready[-1]) if ready else None,"latest_partial":info(parts[-1]) if parts else None},"clips":{"local":len(base._clip_files()),"archived":len(_load_archived())},"exports":list(_export_jobs.values())[-10:]}
def _download(url,path):
    req=urllib.request.Request(url,headers={"User-Agent":"CutCutAi-worker"})
    with urllib.request.urlopen(req,timeout=90) as r,path.open("wb") as f:
        while True:
            chunk=r.read(1048576)
            if not chunk:break
            f.write(chunk)
def _clip_row(cid):return next((x for x in _ranking(None).get("clips",[]) if str(x.get("id"))==cid),None)
def _run_export(job_id,cid,opts):
    tmp=EXPORT_ROOT/f".{job_id}";tmp.mkdir(parents=True,exist_ok=True);out=EXPORT_ROOT/f"{job_id}.mp4"
    try:
        row=_clip_row(cid);local=base._clip_files().get(cid,{})
        if not row and "asset" not in local:raise RuntimeError("corte não encontrado")
        source=tmp/f"{cid}.mp4";captions=tmp/f"{cid}.captions.json"
        if "asset" in local:base.shutil.copy2(local["asset"],source)
        elif row and str(row.get("asset_url","")).startswith(("http://","https://")):_download(str(row["asset_url"]),source)
        else:raise RuntimeError("fonte do corte indisponível")
        if "captions" in local:base.shutil.copy2(local["captions"],captions)
        elif row and str(row.get("captions_url","")).startswith(("http://","https://")):
            try:_download(str(row["captions_url"]),captions)
            except Exception:pass
        cmd=[sys.executable,"-m","cutai.editor","--source",str(source),"--output",str(out),"--filter",opts["filter"],"--resolution",str(opts["resolution"]),"--caption-style",opts["caption_style"],"--caption-color",opts["caption_color"],"--highlight-color",opts["highlight_color"],"--caption-position",opts["position"],"--caption-size",str(opts["size"]),"--auto-emphasis","yes" if opts["emphasis"] else "no"]
        if captions.exists():cmd += ["--captions",str(captions)]
        proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=1800)
        if proc.returncode!=0:
            detail=(proc.stderr or proc.stdout or "renderização falhou").strip();detail=" | ".join(detail.splitlines()[-8:]);raise RuntimeError(f"FFmpeg/editor: {detail[-1200:]}")
        _update_job(job_id,{"status":"ready","finished_at":datetime.now(UTC).isoformat(),"url":f"/exports/{job_id}.mp4"})
    except Exception as exc:out.unlink(missing_ok=True);_update_job(job_id,{"status":"failed","error":str(exc)[:1400],"finished_at":datetime.now(UTC).isoformat()});print(f"[export] {job_id} falhou: {exc}",flush=True)
    finally:base.shutil.rmtree(tmp,ignore_errors=True)
def _new_export(data):
    cid=str(data.get("clip_id","")).strip()
    if not re.fullmatch(r"[a-f0-9]{12}",cid):raise ValueError("clip_id inválido")
    opts={"filter":str(data.get("filter","none")),"resolution":int(data.get("resolution",1080)),"caption_style":str(data.get("caption_style","none")),"caption_color":str(data.get("caption_color","#FFFFFF")),"highlight_color":str(data.get("highlight_color","#FFFF00")),"position":str(data.get("position","bottom")),"size":int(data.get("size",62)),"emphasis":bool(data.get("emphasis",True))}
    if opts["filter"] not in {"none","vivid","cinematic","mono"}:opts["filter"]="none"
    if opts["resolution"] not in {720,1080,2160}:opts["resolution"]=1080
    if opts["caption_style"] not in {"none","viral","clean"}:opts["caption_style"]="none"
    if opts["position"] not in {"top","center","bottom"}:opts["position"]="bottom"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}",opts["caption_color"]):opts["caption_color"]="#FFFFFF"
    if not re.fullmatch(r"#[0-9a-fA-F]{6}",opts["highlight_color"]):opts["highlight_color"]="#FFFF00"
    opts["size"]=max(40,min(90,opts["size"]));job_id=f"{cid}-{int(time.time())}";job={"id":job_id,"clip_id":cid,"status":"processing","created_at":datetime.now(UTC).isoformat()}
    with _export_lock:_export_jobs[job_id]=job
    _save_export_jobs();threading.Thread(target=_run_export,args=(job_id,cid,opts),daemon=True,name=f"export-{cid}").start();return job
def _do_get(self):
    p=urlparse(self.path).path
    if p=="/diagnostics":self._send(200,_diagnostics());return
    if p=="/archive/exports":self._send(200,_exports_for_archive());return
    if p.startswith("/edit/status/"):
        jid=Path(p).name;job=_export_jobs.get(jid);self._send(200,job) if job else self._send(404,{"ok":False,"error":"not_found"});return
    if p.startswith("/exports/") and p.endswith(".mp4"):
        name=Path(p).name
        if not re.fullmatch(r"[a-f0-9]{12}-\d+\.mp4",name):self._send(404,{"ok":False,"error":"not_found"});return
        self._send_file(EXPORT_ROOT/name);return
    return _original_do_get(self)
def _do_post(self):
    if urlparse(self.path).path!="/edit/export":return _original_do_post(self)
    token=base.os.getenv("CUTAI_API_TOKEN","")
    if token and self.headers.get("Authorization")!=f"Bearer {token}":self._send(401,{"ok":False,"error":"unauthorized"});return
    try:size=min(int(self.headers.get("Content-Length","0")),16384);data=json.loads(self.rfile.read(size) or b"{}");self._send(202,_new_export(data))
    except (ValueError,TypeError,json.JSONDecodeError) as exc:self._send(400,{"ok":False,"error":str(exc)});return
    except Exception as exc:self._send(500,{"ok":False,"error":str(exc)[:300]});return
EXPORT_ROOT.mkdir(parents=True,exist_ok=True);_recover_export_jobs();base._ranking=_ranking;base._maintenance_loop=_maintenance_loop;base.Handler.do_GET=_do_get;base.Handler.do_POST=_do_post
if __name__=="__main__":base.main()
