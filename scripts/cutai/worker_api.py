"""API HTTP persistente que recebe lives, controla o supervisor e serve os cortes."""
from __future__ import annotations
import json, mimetypes, os, signal, subprocess, sys, threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse
ROOT=Path(os.getenv("CUTAI_DATA_ROOT","/data/cutcutai")); PORT=int(os.getenv("PORT","8080")); _lock=threading.Lock(); _process=None; _current_url=None

def _state():
    running=bool(_process and _process.poll() is None); supervisor=ROOT/"continuous-live"/"supervisor.json"; detail={}
    try: detail=json.loads(supervisor.read_text(encoding="utf-8"))
    except (OSError,ValueError): pass
    return {"ok":True,"running":running,"url":_current_url if running else None,"supervisor":detail}
def _valid_url(value):
    try: p=urlparse(value); return p.scheme in {"http","https"} and bool(p.netloc)
    except ValueError: return False
def _stop():
    global _process,_current_url
    if _process and _process.poll() is None:
        _process.send_signal(signal.SIGTERM)
        try:_process.wait(timeout=30)
        except subprocess.TimeoutExpired:_process.kill();_process.wait()
    _process=None;_current_url=None
def _start(url):
    global _process,_current_url; _stop(); root=ROOT/"continuous-live"; root.mkdir(parents=True,exist_ok=True)
    _process=subprocess.Popen([sys.executable,"-m","cutai.live_supervisor","--url",url,"--root",str(root),"--segment-seconds","30","--window-seconds","600","--overlap-seconds","90","--capture-restarts","12"]); _current_url=url

def _clip_files():
    result={}; root=ROOT/"continuous-live"/"analysis"
    if not root.exists(): return result
    for path in root.rglob("*"):
        if not path.is_file(): continue
        name=path.name
        if name.endswith(".captions.json"): cid=name[:-14]; kind="captions"
        elif path.suffix.lower()==".mp4": cid=path.stem; kind="asset"
        elif path.suffix.lower()==".jpg": cid=path.stem; kind="thumbnail"
        else: continue
        result.setdefault(cid,{})[kind]=path
    return result

def _ranking():
    files=_clip_files(); rows=[]; ranking_path=Path(os.getenv("CUTAI_RANKING_PATH",str(ROOT/"ranking.json")))
    # Compatibilidade com cortes criados antes da migração do ranking para o volume.
    for candidate in (ranking_path,Path("data/ranking.json")):
        try:
            loaded=json.loads(candidate.read_text(encoding="utf-8")).get("clips",[])
            known={str(r.get("id","")) for r in rows}; rows.extend(r for r in loaded if str(r.get("id","")) not in known)
        except (OSError,ValueError,TypeError): pass
    by_id={str(r.get("id","")):r for r in rows}; clips=[]; base=os.getenv("CUTAI_PUBLIC_BASE_URL","").rstrip("/"); prefix=f"{base}/media" if base else "/media"
    for cid,found in files.items():
        if "asset" not in found: continue
        row=dict(by_id.get(cid,{"id":cid,"title":"Corte da live","source_title":"Live contínua","created_at":datetime.fromtimestamp(found["asset"].stat().st_mtime,UTC).isoformat(),"duration":0,"score":0,"score_breakdown":{},"transcript":"","description":"Corte recuperado do processamento contínuo.","hashtags":[],"reasons":[]}))
        row["asset_url"]=f"{prefix}/{cid}.mp4"
        if "thumbnail" in found: row["thumbnail_url"]=f"{prefix}/{cid}.jpg"
        if "captions" in found: row["captions_url"]=f"{prefix}/{cid}.captions.json"
        clips.append(row)
    clips.sort(key=lambda c:str(c.get("created_at","")),reverse=True); return {"clips":clips,"count":len(clips)}

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",os.getenv("CUTAI_ALLOWED_ORIGIN","*"));self.send_header("Access-Control-Allow-Headers","Content-Type, Authorization");self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
    def _send(self,code,payload):
        body=json.dumps(payload,ensure_ascii=False).encode();self.send_response(code);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Content-Length",str(len(body)));self._cors();self.end_headers();self.wfile.write(body)
    def _send_file(self,path):
        if not path.is_file():self._send(404,{"ok":False,"error":"not_found"});return
        self.send_response(200);self.send_header("Content-Type",mimetypes.guess_type(path.name)[0] or "application/octet-stream");self.send_header("Content-Length",str(path.stat().st_size));self.send_header("Accept-Ranges","bytes");self._cors();self.end_headers()
        with path.open("rb") as fh:
            while True:
                chunk=fh.read(1024*1024)
                if not chunk:break
                self.wfile.write(chunk)
    def do_OPTIONS(self):self._send(204,{})
    def do_GET(self):
        p=urlparse(self.path).path
        if p in {"/","/health","/status"}:self._send(200,_state());return
        if p=="/ranking":self._send(200,_ranking());return
        if p.startswith("/media/"):
            filename=Path(unquote(p[len("/media/"):])).name;cid=filename.split(".",1)[0];found=_clip_files().get(cid,{})
            path=found.get("captions") if filename.endswith(".captions.json") else found.get("asset") if filename.endswith(".mp4") else found.get("thumbnail") if filename.endswith(".jpg") else None
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
    ROOT.mkdir(parents=True,exist_ok=True);server=ThreadingHTTPServer(("0.0.0.0",PORT),Handler);print(f"CutCutAi worker API ouvindo em 0.0.0.0:{PORT}",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        with _lock:_stop()
        server.server_close()
if __name__=="__main__":main()
