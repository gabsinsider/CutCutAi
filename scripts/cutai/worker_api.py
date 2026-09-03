"""API HTTP persistente que recebe lives, controla sessões e serve os cortes."""
from __future__ import annotations
import hashlib,json,mimetypes,os,re,signal,subprocess,sys,threading
from datetime import UTC,datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote,urlparse
ROOT=Path(os.getenv("CUTAI_DATA_ROOT","/data/cutcutai"));PORT=int(os.getenv("PORT","8080"));_lock=threading.Lock();_process=None;_current_url=None;_session_id=None

def _session(url):return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")+"-"+hashlib.sha1(url.encode()).hexdigest()[:8]
def _session_root():return ROOT/"sessions"/str(_session_id) if _session_id else None
def _read_json(path):
    try:return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError):return {}
def _write_json(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def _cleanup_storage():
    """Remove apenas artefatos regeneráveis; nunca MP4/JPG/captions finais."""
    removed=0;freed=0
    sessions=ROOT/"sessions"
    roots=[p for p in sessions.iterdir() if p.is_dir()] if sessions.exists() else []
    legacy=ROOT/"continuous-live"
    if legacy.exists():roots.append(legacy)
    for root in roots:
        # Janelas concatenadas são temporárias e podem ocupar centenas de MB cada.
        for p in root.rglob("window-segment-*.mkv"):
            try:freed+=p.stat().st_size;p.unlink();removed+=1
            except OSError:pass
        for p in root.rglob("window-segment-*.txt"):
            try:freed+=p.stat().st_size;p.unlink();removed+=1
            except OSError:pass
        # Segmentos de sessões já encerradas são descartáveis; os cortes finais
        # ficam em analysis-* e são preservados para Ranking/Assistir.
        meta=_read_json(root/"session.json")
        if meta.get("status") in {"stopped","finished"} or root==legacy:
            stream=root/"stream"
            if stream.exists():
                for p in stream.glob("segment-*.mkv"):
                    try:freed+=p.stat().st_size;p.unlink();removed+=1
                    except OSError:pass
    if removed:print(f"[worker-api] limpeza segura: {removed} temporário(s), {freed/1024/1024:.1f} MiB liberados",flush=True)
    return removed,freed
def _state():
    running=bool(_process and _process.poll() is None);root=_session_root();detail=_read_json(root/"supervisor.json") if root else {}
    return {"ok":True,"running":running,"url":_current_url if running else None,"session_id":_session_id,"supervisor":detail}
def _valid_url(v):
    try:p=urlparse(v);return p.scheme in {"http","https"} and bool(p.netloc)
    except ValueError:return False
def _launch(url,session_id):
    global _process,_current_url,_session_id;_session_id=session_id;root=ROOT/"sessions"/session_id;root.mkdir(parents=True,exist_ok=True);_process=subprocess.Popen([sys.executable,"-m","cutai.live_supervisor","--url",url,"--root",str(root),"--segment-seconds","30","--window-seconds","600","--overlap-seconds","90","--capture-restarts","12"]);_current_url=url
    meta=_read_json(root/"session.json");meta.update({"id":session_id,"url":url,"status":"active","last_started_at":datetime.now(UTC).isoformat(),"resume_count":int(meta.get("resume_count",0))});_write_json(root/"session.json",meta)
def _stop(mark_stopped=True):
    global _process,_current_url
    root=_session_root()
    if _process and _process.poll() is None:
        _process.send_signal(signal.SIGTERM)
        try:_process.wait(timeout=30)
        except subprocess.TimeoutExpired:_process.kill();_process.wait()
    if mark_stopped and root:
        meta=_read_json(root/"session.json");meta.update({"status":"stopped","stopped_at":datetime.now(UTC).isoformat()});_write_json(root/"session.json",meta)
    _process=None;_current_url=None
    if mark_stopped:_cleanup_storage()
def _start(url):
    _stop();_cleanup_storage();sid=_session(url);root=ROOT/"sessions"/sid;root.mkdir(parents=True,exist_ok=True);_write_json(root/"session.json",{"id":sid,"url":url,"status":"active","started_at":datetime.now(UTC).isoformat(),"resume_count":0});_launch(url,sid)
def _recover():
    sessions=ROOT/"sessions"
    if not sessions.exists():return False
    candidates=[]
    for root in sessions.iterdir():
        if not root.is_dir():continue
        meta=_read_json(root/"session.json")
        if meta.get("status")!="active" or not _valid_url(str(meta.get("url",""))):continue
        candidates.append((str(meta.get("last_started_at") or meta.get("started_at") or ""),root,meta))
    if not candidates:return False
    _,root,meta=max(candidates,key=lambda x:x[0]);url=str(meta["url"]);meta["resume_count"]=int(meta.get("resume_count",0))+1;meta["resumed_at"]=datetime.now(UTC).isoformat();_write_json(root/"session.json",meta);print(f"[worker-api] retomando sessão {root.name} após reinício",flush=True);_launch(url,root.name);return True

def _analysis_roots():
    roots=[];legacy=ROOT/"continuous-live"/"analysis"
    if legacy.exists():roots.append(legacy)
    sessions=ROOT/"sessions"
    if sessions.exists():roots.extend(p/"analysis" for p in sessions.iterdir() if (p/"analysis").exists())
    return roots
def _clip_files():
    result={}
    for root in _analysis_roots():
        for path in root.rglob("*"):
            if not path.is_file():continue
            name=path.name
            if name.endswith(".captions.json"):cid=name[:-14];kind="captions"
            elif path.suffix.lower()==".mp4":cid=path.stem;kind="asset"
            elif path.suffix.lower()==".jpg":cid=path.stem;kind="thumbnail"
            else:continue
            result.setdefault(cid,{})[kind]=path
    return result
def _public_base(handler=None):
    configured=os.getenv("CUTAI_PUBLIC_BASE_URL","").strip().rstrip("/")
    if configured:return configured
    if handler:
        proto=handler.headers.get("X-Forwarded-Proto","https").split(",")[0].strip();host=handler.headers.get("X-Forwarded-Host") or handler.headers.get("Host")
        if host:return f"{proto}://{host}".rstrip("/")
    return ""
def _ranking(handler=None):
    files=_clip_files();rows=[]
    for candidate in (ROOT/"ranking.json",Path("data/ranking.json")):
        try:
            loaded=json.loads(candidate.read_text(encoding="utf-8")).get("clips",[]);known={str(r.get("id","")) for r in rows};rows.extend(r for r in loaded if str(r.get("id","")) not in known)
        except (OSError,ValueError,TypeError):pass
    by_id={str(r.get("id","")):r for r in rows};base=_public_base(handler);prefix=f"{base}/media" if base else "/media";clips=[]
    for cid,found in files.items():
        if "asset" not in found:continue
        row=dict(by_id.get(cid,{"id":cid,"title":"Corte da live","source_title":"Live contínua","created_at":datetime.fromtimestamp(found["asset"].stat().st_mtime,UTC).isoformat(),"duration":0,"score":0,"score_breakdown":{},"transcript":"","description":"Corte recuperado do processamento contínuo.","hashtags":[],"reasons":[]}));row["asset_url"]=f"{prefix}/{cid}.mp4"
        if "thumbnail" in found:row["thumbnail_url"]=f"{prefix}/{cid}.jpg"
        if "captions" in found:row["captions_url"]=f"{prefix}/{cid}.captions.json"
        clips.append(row)
    clips.sort(key=lambda c:str(c.get("created_at","")),reverse=True);return {"clips":clips,"count":len(clips)}
class Handler(BaseHTTPRequestHandler):
    def _cors(self):self.send_header("Access-Control-Allow-Origin",os.getenv("CUTAI_ALLOWED_ORIGIN","*"));self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization, Range");self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS");self.send_header("Access-Control-Expose-Headers","Content-Length, Content-Range, Accept-Ranges")
    def _send(self,code,payload):body=json.dumps(payload,ensure_ascii=False).encode();self.send_response(code);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(body)));self._cors();self.end_headers();self.wfile.write(body)
    def _send_file(self,path):
        if not path.is_file():self._send(404,{"ok":False,"error":"not_found"});return
        size=path.stat().st_size;start=0;end=size-1;rh=self.headers.get("Range","")
        if rh:
            m=re.match(r"bytes=(\d*)-(\d*)",rh)
            if m:
                if m.group(1):start=int(m.group(1))
                if m.group(2):end=min(int(m.group(2)),size-1)
                if start>=size or start>end:self.send_response(416);self.send_header("Content-Range",f"bytes */{size}");self._cors();self.end_headers();return
        length=end-start+1;self.send_response(206 if rh else 200);self.send_header("Content-Type",mimetypes.guess_type(path.name)[0] or "application/octet-stream");self.send_header("Content-Length",str(length));self.send_header("Accept-Ranges","bytes")
        if rh:self.send_header("Content-Range",f"bytes {start}-{end}/{size}")
        self._cors();self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start);remaining=length
            while remaining:
                chunk=fh.read(min(1048576,remaining))
                if not chunk:break
                self.wfile.write(chunk);remaining-=len(chunk)
    def do_OPTIONS(self):self._send(204,{})
    def do_GET(self):
        p=urlparse(self.path).path
        if p in {"/","/health","/status"}:self._send(200,_state());return
        if p=="/ranking":self._send(200,_ranking(self));return
        if p.startswith("/media/"):
            filename=Path(unquote(p[7:])).name;cid=filename.split(".",1)[0];found=_clip_files().get(cid,{});path=found.get("captions") if filename.endswith(".captions.json") else found.get("asset") if filename.endswith(".mp4") else found.get("thumbnail") if filename.endswith(".jpg") else None
            if path:self._send_file(path)
            else:self._send(404,{"ok":False,"error":"not_found"})
            return
        self._send(404,{"ok":False,"error":"not_found"})
    def do_POST(self):
        token=os.getenv("CUTAI_API_TOKEN","")
        if token and self.headers.get("Authorization")!=f"Bearer {token}":self._send(401,{"ok":False,"error":"unauthorized"});return
        if self.path=="/live/start":
            try:size=min(int(self.headers.get("Content-Length","0")),16384);data=json.loads(self.rfile.read(size) or b"{}");url=str(data.get("url","")).strip()
            except (ValueError,TypeError,json.JSONDecodeError):self._send(400,{"ok":False,"error":"invalid_json"});return
            if not _valid_url(url):self._send(400,{"ok":False,"error":"invalid_url"});return
            with _lock:_start(url)
            self._send(202,_state())
        elif self.path=="/live/stop":
            with _lock:_stop()
            self._send(200,_state())
        else:self._send(404,{"ok":False,"error":"not_found"})
    def log_message(self,fmt,*args):print(f"[worker-api] {self.address_string()} {fmt % args}",flush=True)
def main():
    ROOT.mkdir(parents=True,exist_ok=True);_cleanup_storage()
    with _lock:_recover()
    server=ThreadingHTTPServer(("0.0.0.0",PORT),Handler);print(f"CutCutAi worker API ouvindo em 0.0.0.0:{PORT}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        with _lock:_stop(mark_stopped=False)
        server.server_close()
if __name__=="__main__":main()
