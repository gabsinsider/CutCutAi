"""API HTTP persistente que recebe lives e controla o supervisor CutCutAi."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

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
            _process.kill()
            _process.wait()
    _process = None
    _current_url = None


def _start(url: str) -> None:
    global _process, _current_url
    _stop()
    root = ROOT / "continuous-live"
    root.mkdir(parents=True, exist_ok=True)
    _process = subprocess.Popen([
        sys.executable, "-m", "cutai.live_supervisor",
        "--url", url,
        "--root", str(root),
        "--segment-seconds", "30",
        "--window-seconds", "600",
        "--overlap-seconds", "90",
        "--capture-restarts", "12",
    ])
    _current_url = url


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", os.getenv("CUTAI_ALLOWED_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        if self.path in {"/", "/health", "/status"}:
            self._send(200, _state())
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def do_POST(self):
        token = os.getenv("CUTAI_API_TOKEN", "")
        if token and self.headers.get("Authorization") != f"Bearer {token}":
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path == "/live/start":
            try:
                size = min(int(self.headers.get("Content-Length", "0")), 16384)
                data = json.loads(self.rfile.read(size) or b"{}")
                url = str(data.get("url", "")).strip()
            except (ValueError, TypeError, json.JSONDecodeError):
                self._send(400, {"ok": False, "error": "invalid_json"})
                return
            if not _valid_url(url):
                self._send(400, {"ok": False, "error": "invalid_url"})
                return
            with _lock:
                _start(url)
            self._send(202, _state())
        elif self.path == "/live/stop":
            with _lock:
                _stop()
            self._send(200, _state())
        else:
            self._send(404, {"ok": False, "error": "not_found"})

    def log_message(self, fmt, *args):
        print(f"[worker-api] {self.address_string()} {fmt % args}", flush=True)


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"CutCutAi worker API ouvindo em 0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with _lock:
            _stop()
        server.server_close()


if __name__ == "__main__":
    main()
