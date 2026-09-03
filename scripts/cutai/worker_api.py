"""API HTTP persistente que recebe lives, controla o supervisor e serve os cortes."""
from __future__ import annotations

import json
import mimetypes
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(os.getenv("CUTAI_DATA_ROOT", "/data/cutcutai"))
PORT = int(os.getenv("PORT", "8080"))
_lock = threading.Lock()
_process: subprocess.Popen | None = None
_current_url: str | None = None


def _state() -> dict:
    global _process, _current_url
    running = bool(_process and _process.poll() is None)
    supervisor = ROOT / "continuous-live" / "supervisor.json"
    detail = {}
    try:
        detail = json.loads(supervisor.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"ok": True, "running": running, "url": _current_url if running else None, "supervisor": detail}


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except ValueError:
        return False


def _stop() -> None:
    global _process, _current_url
    if _process and _process.poll() is None:
        _process.send_signal(signal.SIGTERM)
        try:
            _process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _process.kill(); _process.wait()
    _process = None; _current_url = None


def _start(url: str) -> None:
    global _process, _current_url
    _stop()
    root = ROOT / "continuous-live"; root.mkdir(parents=True, exist_ok=True)
    _process = subprocess.Popen([sys.executable, "-m", "cutai.live_supervisor", "--url", url,
        "--root", str(root), "--segment-seconds", "30", "--window-seconds", "600",
        "--overlap-seconds", "90", "--capture-restarts", "12"])
    _current_url = url


def _clip_files() -> dict[str, dict[str, Path]]:
    result: dict[str, dict[str, Path]] = {}
    analysis_root = ROOT / "continuous-live" / "analysis"
    if not analysis_root.exists(): return result
    for path in analysis_root.rglob("*"):
        if not path.is_file(): continue
        suffix = path.suffix.lower()
        if suffix not in {".mp4", ".jpg", ".json"}: continue
        name = path.name
        if name.endswith(".captions.json"):
            clip_id = name[:-14]; kind = "captions"
        elif suffix == ".mp4": clip_id = path.stem; kind = "asset"
        elif suffix == ".jpg": clip_id = path.stem; kind = "thumbnail"
        else: continue
        result.setdefault(clip_id, {})[kind] = path
    return result


def _ranking() -> dict:
    files = _clip_files(); ranking_path = Path("data/ranking.json")
    try: rows = json.loads(ranking_path.read_text(encoding="utf-8")).get("clips", [])
    except (OSError, ValueError, TypeError): rows = []
    clips = []
    base = os.getenv("CUTAI_PUBLIC_BASE_URL", "").rstrip("/")
    for row in rows:
        clip_id = str(row.get("id", "")); found = files.get(clip_id)
        if not found or "asset" not in found: continue
        item = dict(row)
        prefix = f"{base}/media" if base else "/media"
        item["asset_url"] = f"{prefix}/{clip_id}.mp4"
        if "thumbnail" in found: item["thumbnail_url"] = f"{prefix}/{clip_id}.jpg"
        if "captions" in found: item["captions_url"] = f"{prefix}/{clip_id}.captions.json"
        clips.append(item)
    clips.sort(key=lambda c: str(c.get("created_at", "")), reverse=True)
    return {"clips": clips}


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", os.getenv("CUTAI_ALLOWED_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(body))); self._cors(); self.end_headers(); self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file(): self._send(404, {"ok": False, "error": "not_found"}); return
        size = path.stat().st_size; self.send_response(200); self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"); self.send_header("Content-Length", str(size)); self.send_header("Accept-Ranges", "bytes"); self._cors(); self.end_headers()
        with path.open("rb") as fh:
            while chunk := fh.read(1024 * 1024): self.wfile.write(chunk)

    def do_OPTIONS(self): self._send(204, {})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/health", "/status"}: self._send(200, _state()); return
        if parsed.path == "/ranking": self._send(200, _ranking()); return
        if parsed.path.startswith("/media/"):
            filename = Path(unquote(parsed.path[len("/media/"):])).name
            clip_id = filename.split(".", 1)[0]; found = _clip_files().get(clip_id, {})
            if filename.endswith(".captions.json"): path = found.get("captions")
            elif filename.endswith(".mp4"): path = found.get("asset")
            elif filename.endswith(".jpg"): path = found.get("thumbnail")
            else: path = None
            if path: self._send_file(path)
            else: self._send(404, {"ok": False, "error": "not_found"})
            return
        self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        token = os.getenv("CUTAI_API_TOKEN", "")
        if token and self.headers.get("Authorization") != f"Bearer {token}": self._send(401, {"ok": False, "error": "unauthorized"}); return
        if self.path == "/live/start":
            try:
                size = min(int(self.headers.get("Content-Length", "0")), 16384); data = json.loads(self.rfile.read(size) or b"{}"); url = str(data.get("url", "")).strip()
            except (ValueError, TypeError, json.JSONDecodeError): self._send(400, {"ok": False, "error": "invalid_json"}); return
            if not _valid_url(url): self._send(400, {"ok": False, "error": "invalid_url"}); return
            with _lock: _start(url)
            self._send(202, _state())
        elif self.path == "/live/stop":
            with _lock: _stop()
            self._send(200, _state())
        else: self._send(404, {"ok": False, "error": "not_found"})

    def log_message(self, fmt, *args): print(f"[worker-api] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True); server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler); print(f"CutCutAi worker API ouvindo em 0.0.0.0:{PORT}", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        with _lock: _stop()
        server.server_close()


if __name__ == "__main__": main()
