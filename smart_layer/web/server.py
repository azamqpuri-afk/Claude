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
from urllib.parse import unquote_plus as _unquote

from .. import store
from . import data

PAGE = Path(__file__).with_name("dashboard.html")
MAX_SESSIONS = 50

ENTITY_LABELS = {
    "items": "Store items", "stock_balances": "Stock balances", "store_receipts": "Store receipts (GRN)",
    "store_issues": "Store issues", "parties": "Parties", "accounts": "Accounts", "ledger_entries": "Ledger entries",
    "account_balances": "Account balances", "looms": "Looms", "loom_production": "Loom production",
    "contracts": "Contracts", "yarn_movements": "Yarn movements", "fabric_dispatches": "Fabric dispatches",
    "beams": "Beams", "beam_movements": "Beam movements", "loom_monitor": "Loom monitor",
    "employees": "Employees", "payroll": "Payroll", "employee_loans": "Employee loans", "gate_passes": "Gate passes",
    "yarn_stock": "Yarn stock (count-wise)", "fabric_stock": "Fabric stock", "attendance": "Attendance (last present)",
}


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

    def entities(self):
        """Every data set in the local copy, with a friendly label and its row count, for the Browse tab."""
        from .. import schema
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            out = []
            for e in schema.ENTITIES:
                try:
                    n = con.execute(f"SELECT COUNT(*) FROM {e.name}").fetchone()[0]
                except Exception:
                    n = 0
                out.append({"name": e.name, "label": ENTITY_LABELS.get(e.name, e.name.replace("_", " ").title()),
                            "rows": n, "columns": e.column_names})
            return out
        finally:
            con.close()

    def browse(self, entity, q="", sort="", direction="desc", limit=200, offset=0):
        """Rows of one canonical data set, filtered by a search term across its text columns. Read-only."""
        from .. import schema
        if entity not in schema.BY_NAME:
            return None
        e = schema.BY_NAME[entity]
        cols = e.column_names
        where, params = "", []
        if q:
            text_cols = [c.name for c in e.columns if c.type in ("TEXT", "DATE", "DATETIME")] or cols
            where = "WHERE " + " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in text_cols)
            params = [f"%{q}%"] * len(text_cols)
        order = ""
        if sort in cols:
            order = f"ORDER BY {sort} {'ASC' if str(direction).lower() == 'asc' else 'DESC'}"
        elif e.date_column:
            order = f"ORDER BY {e.date_column} DESC"
        limit = max(1, min(int(limit), 1000))
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            total = con.execute(f"SELECT COUNT(*) FROM {entity} {where}", params).fetchone()[0]
            rows = con.execute(f"SELECT {', '.join(cols)} FROM {entity} {where} {order} LIMIT ? OFFSET ?",
                               [*params, limit, int(offset)]).fetchall()
            return {"entity": entity, "columns": cols, "rows": [list(r) for r in rows],
                    "total": total, "limit": limit, "offset": int(offset)}
        finally:
            con.close()

    def ledger(self, account):
        from . import finance
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return finance.ledger(con, account)
        finally:
            con.close()

    def fin_parties(self, chip, q):
        from . import finance
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return {"parties": finance.parties(con, chip or "all", q or "")}
        finally:
            con.close()

    def fin_daily(self, day):
        from . import finance
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return finance.daily(con, day or date.today())
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
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path == "/api/licence":
                from .. import licence
                st = licence.status(app.cfg)
                return self._send(200, {"state": st.state, "message": st.message, "allowed": st.allowed,
                                        "warning": st.warning, "machine": licence.machine_id()})
            if path == "/api/reports":
                from .. import report_registry
                return self._send(200, {"reports": [{"name": n, "label": label} for n, label in report_registry.menu()]})
            if path in ("/api/data", "/api/report", "/api/entities", "/api/browse",
                        "/api/ledger", "/api/finance/parties", "/api/finance/daily"):
                from .. import licence
                st = licence.status(app.cfg)
                if not st.allowed:
                    return self._send(403, {"licence": st.message, "machine": licence.machine_id()})
                params = {k: _unquote(v) for p in query.split("&") if "=" in p for k, v in [p.split("=", 1)]}
                if path == "/api/entities":
                    return self._send(200, {"entities": app.entities()})
                if path == "/api/browse":
                    data = app.browse(params.get("entity", ""), params.get("q", ""), params.get("sort", ""),
                                      params.get("dir", "desc"), params.get("limit", 200), params.get("offset", 0))
                    return self._send(200, data) if data else self._send(404, {"error": "unknown data set"})
                if path == "/api/ledger":
                    led = app.ledger(params.get("account", ""))
                    return self._send(200, led) if led else self._send(404, {"error": "unknown account"})
                if path == "/api/finance/parties":
                    return self._send(200, app.fin_parties(params.get("group", "all"), params.get("q", "")))
                try:
                    day = date.fromisoformat(params["date"]) if params.get("date") else None
                except ValueError:
                    return self._send(400, {"error": "date must be YYYY-MM-DD"})
                if path == "/api/finance/daily":
                    return self._send(200, app.fin_daily(day))
                if path == "/api/data":
                    return self._send(200, app.data(day))
                return self._report(app, params.get("name", ""), params.get("fmt", "json"), day)
            self._send(404, {"error": "not found"})

        def _static(self, name):
            """Serve a bundled static file (Chart.js etc.) from web/static - offline, no CDN."""
            name = name.replace("\\", "/").lstrip("/")
            if "/" in name or ".." in name:
                return self._send(404, {"error": "not found"})
            p = Path(__file__).with_name("static") / name
            if not p.is_file():
                return self._send(404, {"error": "not found"})
            ctype = {"js": "application/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                     "woff2": "font/woff2", "svg": "image/svg+xml"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            body = p.read_bytes()
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

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
