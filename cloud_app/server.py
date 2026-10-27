"""
The cloud server (Python standard library only, same style as the on-prem dashboard).

  GET  /                      a tiny status page
  POST /api/<mill>/upload     the mill's agent sends its data (Bearer <upload token>); body = a zip of
                              store.db (+ config.toml). Raw ERP never comes here - only the processed store.
  GET  /<mill>/              the mill's dashboard (HTTP basic auth, the mill's view password)
  GET  /api/<mill>/data      dashboard data for that mill
  POST /api/<mill>/ask       "ask the ERP" chat for that mill (uses the cloud's own Claude key)

Each mill's data lives in tenants/<mill>/ and is served only to someone with that mill's view password.
Access is controlled centrally in the registry (cloud.db); disabling a mill blocks upload and viewing at once.
"""

import base64
import io
import json
import os
import threading
import zipfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from smart_layer import config as sl_config
from smart_layer import store as sl_store
from smart_layer.web import data as sl_data
from smart_layer.web.server import App as SlApp
from smart_layer.web.server import get_response

DASHBOARD = Path(__file__).resolve().parents[1] / "smart_layer" / "web" / "dashboard.html"
MAX_UPLOAD = 300 * 1024 * 1024
MAX_SESSIONS = 200


class Cloud:
    def __init__(self, registry, tenants_dir):
        from .registry import Registry
        self.reg = registry if isinstance(registry, Registry) else Registry(registry)
        self.tenants = Path(tenants_dir)
        self.tenants.mkdir(parents=True, exist_ok=True)
        self.locks = {}
        self.sessions = {}
        self.apps = {}                                    # one smart_layer dashboard App per mill, cached
        self.guard = threading.Lock()

    def app_for(self, mill_id):
        """A cached on-prem-style App for this tenant, so the cloud serves every dashboard tab (reports, browse,
        ledger, finance) with exactly the same code as the on-prem server. Each call still reads the current store.db."""
        with self.guard:
            app = self.apps.get(mill_id)
            if app is None:
                app = self.apps[mill_id] = SlApp(self.tenant_cfg(mill_id))
            return app

    def _lock(self, mill_id):
        with self.guard:
            return self.locks.setdefault(mill_id, threading.Lock())

    def tenant_dir(self, mill_id):
        return self.tenants / mill_id

    def save_upload(self, mill_id, raw):
        with self._lock(mill_id):
            d = self.tenant_dir(mill_id)
            d.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    base = Path(name).name
                    if base in ("store.db", "config.toml", "closing_counts.local.toml") and base == name:   # no paths, no traversal
                        tmp = d / (base + ".tmp")
                        tmp.write_bytes(z.read(name))
                        tmp.replace(d / base)
        with self.guard:
            self.apps.pop(mill_id, None)                   # a new config.toml may have arrived; rebuild the App lazily
        self.reg.mark_upload(mill_id)

    def tenant_cfg(self, mill_id):
        d = self.tenant_dir(mill_id)
        cfg_path = d / "config.toml"
        if cfg_path.exists():
            cfg = sl_config.load(cfg_path, expand=False)   # untrusted tenant file: never fill ${VARS} from the cloud env
        else:
            cfg = sl_config.Config(sl_config._merge(sl_config.DEFAULTS, {}), d)
        cfg.data.setdefault("store", {})["path"] = str((d / "store.db").resolve())   # read the tenant's store
        return cfg

    def data(self, mill_id, day):
        con = sl_store.open_store(str((self.tenant_dir(mill_id) / "store.db").resolve()))
        try:
            return sl_data.build(con, self.tenant_cfg(mill_id), day)
        finally:
            con.close()

    def ask(self, mill_id, session, question):
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return {"answer": "Chat is not switched on for the cloud yet.", "queries": []}
        try:
            from smart_layer.ask import Assistant
        except ImportError:
            return {"answer": "Chat library is not installed on the cloud.", "queries": []}
        key = (mill_id, session)
        with self.guard:
            bot = self.sessions.get(key)
            if bot is None:
                if len(self.sessions) >= MAX_SESSIONS:
                    self.sessions.pop(next(iter(self.sessions)))
                bot = self.sessions[key] = Assistant(self.tenant_cfg(mill_id))
        answer, queries = bot.ask(question)
        return {"answer": answer, "queries": queries}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SmartLayerCloud"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            raw = body if isinstance(body, bytes) else json.dumps(body, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _deny(self):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Smart Layer"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _drain(self, n):                          # consume an unread request body so the client is not reset
            while n > 0:
                chunk = self.rfile.read(min(n, 65536))
                if not chunk:
                    break
                n -= len(chunk)

        def _view_ok(self, mill_id):
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    _, pw = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)
                    return app.reg.check_view(mill_id, pw)
                except (ValueError, UnicodeDecodeError):
                    return False
            return False

        def _token_ok(self, mill_id):
            h = self.headers.get("Authorization", "")
            return h.startswith("Bearer ") and app.reg.check_token(mill_id, h[7:].strip())

        def _parts(self):
            path, _, query = self.path.partition("?")
            return [p for p in path.split("/") if p != ""], query, path.endswith("/")

        def do_GET(self):
            parts, query, trailing = self._parts()
            if not parts:
                return self._send(200, b"<h1>Smart Layer Cloud</h1><p>Open your mill's address.</p>",
                                  "text/html; charset=utf-8")
            mill_id = parts[0]
            if not app.reg.get(mill_id):
                return self._send(404, {"error": "unknown mill"})
            if parts == [mill_id] and not trailing:
                self.send_response(301)
                self.send_header("Location", f"/{mill_id}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            # everything under /<mill>/ needs the mill's view password
            if not self._view_ok(mill_id):
                return self._deny()
            # the cloud is pre-authorised (no per-mill licence check here); keep the licence endpoint as a stub
            if parts[1:] == ["api", "licence"]:
                return self._send(200, {"state": "cloud", "allowed": True, "warning": False, "message": "", "machine": ""})
            subpath = "/" + "/".join(parts[1:])                      # strip the /<mill> prefix
            # data-bearing endpoints need an uploaded store; the page and static files do not
            needs_data = subpath not in ("/", "/index.html") and not subpath.startswith("/static/")
            if needs_data and not (app.tenant_dir(mill_id) / "store.db").exists():
                return self._send(503, {"error": "no data uploaded for this mill yet"})
            try:
                # hand off to the SAME routing the on-prem dashboard uses, so every tab works on the cloud
                self._emit(*get_response(app.app_for(mill_id), subpath, query, check_licence=False))
            except FileNotFoundError:
                self._send(503, {"error": "no data uploaded for this mill yet"})
            except Exception as e:
                self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})

        def _emit(self, code, body, ctype, headers):
            raw = body if isinstance(body, (bytes, bytearray)) else json.dumps(body, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            if "Cache-Control" not in (headers or {}):
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            parts, _, _ = self._parts()
            length = int(self.headers.get("Content-Length", 0) or 0)
            mill_id = parts[0] if parts else ""
            if not app.reg.get(mill_id):
                self._drain(length)
                return self._send(404, {"error": "unknown mill"})
            if parts[1:] == ["api", "upload"]:
                if not self._token_ok(mill_id):
                    self._drain(length)
                    return self._deny()
                if length <= 0 or length > MAX_UPLOAD:
                    self._drain(length)
                    return self._send(413, {"error": "bad upload size"})
                try:
                    app.save_upload(mill_id, self.rfile.read(length))
                except zipfile.BadZipFile:
                    return self._send(400, {"error": "upload must be a zip of store.db (+ config.toml)"})
                return self._send(200, {"ok": True})
            if parts[1:] == ["api", "ask"]:
                if not self._view_ok(mill_id):
                    self._drain(length)
                    return self._deny()
                try:
                    n = min(length, 20000)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    self._drain(length - n)
                    q = str(body.get("question", "")).strip()[:2000]
                    if not q:
                        return self._send(400, {"error": "empty question"})
                    return self._send(200, app.ask(mill_id, str(body.get("session", "default"))[:64], q))
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})
            if parts[1:] == ["api", "licence"]:
                self._drain(length)
                return self._send(403, {"error": "the licence cannot be changed on the cloud"})
            self._drain(length)
            return self._send(404, {"error": "not found"})

    return Handler


def serve(registry_path, tenants_dir, host="127.0.0.1", port=8090):
    app = Cloud(registry_path, tenants_dir)
    httpd = ThreadingHTTPServer((host, port), handler(app))
    print(f"Smart Layer Cloud on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
