"""
Owner dashboard server (Python standard library only).

    python -m smart_layer serve            then open http://<server>:8080 on a phone or PC

  GET  /             the dashboard page
  GET  /api/data     dashboard data (?date=YYYY-MM-DD, default yesterday)
  POST /api/ask      {"question": "...", "session": "..."} -> {"answer": "...", "queries": [...]}

Access needs the password from config [serve] (HTTP basic auth, any user name).
The server refuses to listen beyond this computer without a password.
The Claude API key stays on the server; browsers never see it.
"""

import base64
import hmac
import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import store
from . import data

PAGE = Path(__file__).with_name("dashboard.html")
MAX_SESSIONS = 50


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.password = str(cfg["serve"].get("password") or "")
        self.sessions = {}
        self.lock = threading.Lock()

    def data(self, day):
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return data.build(con, self.cfg, day)
        finally:
            con.close()

    def report(self, name, day):
        """A built report plus the day it is actually for (so the file name and on-screen title agree)."""
        from .. import report_registry
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return report_registry.build(con, self.cfg, name, day), report_registry.report_day(name, day)
        finally:
            con.close()

    def ask(self, session, question):
        import os
        try:
            from ..ask import Assistant
        except ImportError:
            return {"answer": "Chat is not set up yet: on the server run  python -m pip install anthropic  and restart.",
                    "queries": []}
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return {"answer": "Chat is not set up yet: add ANTHROPIC_API_KEY to the .env file on the server and restart.",
                    "queries": []}
        with self.lock:
            bot = self.sessions.get(session)
            if bot is None:
                if len(self.sessions) >= MAX_SESSIONS:
                    self.sessions.pop(next(iter(self.sessions)))
                bot = self.sessions[session] = Assistant(self.cfg)
        answer, queries = bot.ask(question)
        return {"answer": answer, "queries": queries}


def handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SmartLayer"

        def log_message(self, fmt, *args):            # keep the console quiet except errors
            pass

        def _authorised(self):
            if not app.password:
                return True
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    _, pw = base64.b64decode(h[6:]).decode("utf-8").split(":", 1)
                    return hmac.compare_digest(pw, app.password)
                except (ValueError, UnicodeDecodeError):
                    return False
            return False

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

        def do_GET(self):
            if not self._authorised():
                return self._deny()
            path, _, query = self.path.partition("?")
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/licence":
                from .. import licence
                st = licence.status(app.cfg)
                return self._send(200, {"state": st.state, "message": st.message, "allowed": st.allowed,
                                        "warning": st.warning, "machine": licence.machine_id()})
            if path == "/api/reports":
                from .. import report_registry
                return self._send(200, {"reports": [{"name": n, "label": label} for n, label in report_registry.menu()]})
            if path in ("/api/data", "/api/report"):
                from .. import licence
                st = licence.status(app.cfg)
                if not st.allowed:
                    return self._send(403, {"licence": st.message, "machine": licence.machine_id()})
                params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
                try:
                    day = date.fromisoformat(params["date"]) if params.get("date") else None
                except ValueError:
                    return self._send(400, {"error": "date must be YYYY-MM-DD"})
                if path == "/api/data":
                    return self._send(200, app.data(day))
                return self._report(app, params.get("name", ""), params.get("fmt", "json"), day)
            self._send(404, {"error": "not found"})

        def _report(self, app, name, fmt, day):
            from .. import PRODUCT, report_registry
            from ..reports import fmt as cell
            if name not in report_registry.REPORTS:
                return self._send(404, {"error": "unknown report"})
            rep, on = app.report(name, day)
            tag = on.isoformat()
            if fmt == "csv":
                import csv
                import io
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(rep.columns)
                w.writerows(rep.rows)
                body = buf.getvalue().encode("utf-8-sig")       # BOM so Excel opens Urdu text correctly
                return self._download(body, f"{name}_{tag}.csv", "text/csv; charset=utf-8")
            if fmt == "pdf":
                import tempfile
                from pathlib import Path
                with tempfile.TemporaryDirectory() as tmp:
                    p = rep.write_pdf(tmp, f"{name}_{tag}.pdf", app.cfg["company"]["name"], PRODUCT)
                    body = Path(p).read_bytes()
                return self._download(body, f"{name}_{tag}.pdf", "application/pdf")
            return self._send(200, {"title": rep.title, "columns": rep.columns, "summary": rep.summary,
                                    "rows": [[cell(v) for v in r] for r in rep.rows], "date": tag})

        def _download(self, body, filename, ctype):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self._authorised():
                return self._deny()
            if self.path == "/api/licence":                 # install a new licence (dashboard password required)
                from .. import licence
                n = min(int(self.headers.get("Content-Length", 0)), 20000)
                text = str(json.loads(self.rfile.read(n) or b"{}").get("text", ""))
                st = licence.install(app.cfg, text)
                return self._send(200 if st.allowed else 400, {"state": st.state, "message": st.message, "allowed": st.allowed})
            if self.path != "/api/ask":
                return self._send(404, {"error": "not found"})
            from .. import licence
            if not licence.status(app.cfg).allowed:
                return self._send(403, {"error": "Licence: " + licence.status(app.cfg).message})
            try:
                n = min(int(self.headers.get("Content-Length", 0)), 20000)
                body = json.loads(self.rfile.read(n) or b"{}")
                q = str(body.get("question", "")).strip()[:2000]
                if not q:
                    return self._send(400, {"error": "empty question"})
                return self._send(200, app.ask(str(body.get("session", "default"))[:64], q))
            except Exception as e:                     # report to the page instead of dropping the connection
                return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})

    return Handler


def serve(cfg):
    host = cfg["serve"].get("host", "127.0.0.1")
    port = int(cfg["serve"].get("port", 8080))
    app = App(cfg)
    if host not in ("127.0.0.1", "localhost") and not app.password:
        raise SystemExit("Set [serve] password before opening the dashboard to the network (host is not 127.0.0.1).")
    httpd = ThreadingHTTPServer((host, port), handler(app))
    print(f"Dashboard on http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
