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
    "empty_beam_stock": "Empty beams at sizers",
}


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.password = str(cfg["serve"].get("password") or "")
        self.sessions = {}
        self.lock = threading.Lock()
        self._syncing = False
        self._sync_info = {"running": False, "kind": None, "finished_at": None, "error": None}

    def kick_first_sync(self):
        """After a licence is installed from the dashboard, pull the first data in the background if the store is
        still empty - so the owner sees figures without waiting to run `sync` by hand. Runs at most once at a time."""
        with self.lock:
            if self._syncing:
                return False
            try:
                con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
                try:
                    synced = con.execute("SELECT COUNT(*) FROM sync_log").fetchone()[0]
                finally:
                    con.close()
            except Exception:
                synced = 0
            if synced:
                return False                              # already has data; nothing to kick
            self._syncing = True
        threading.Thread(target=self._first_sync, daemon=True).start()
        return True

    def _first_sync(self):
        try:
            from .. import erp as _erp
            mapping = _erp.Mapping(self.cfg.path(self.cfg["erp"]["mapping"]))
            e = _erp.ERP(*_erp.connect(self.cfg["erp"], self.cfg.base_dir), mapping, self.cfg["sources"], self.cfg.base_dir)
            con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
            try:
                store.sync(con, e, self.cfg["sync"]["history_days"],
                           full_history=self.cfg["sync"].get("full_history", store.FULL_HISTORY),
                           log=lambda *_: None)
            finally:
                con.close()
        except Exception:
            pass                                          # best-effort; the owner can always run `sync` by hand
        finally:
            with self.lock:
                self._syncing = False

    def data(self, day):
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return data.build(con, self.cfg, day)
        finally:
            con.close()

    def changes(self, since):
        """What changed since the client's last refresh (for the Overview animations)."""
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return data.changes(con, self.cfg, since)
        finally:
            con.close()

    def issue_history(self, item, limit=20):
        """The last issues of one store item (feature 4 drill-down), newest first."""
        from ..reports.stock import issue_history
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return issue_history(con, item, limit)
        finally:
            con.close()

    def reports_files(self):
        """Saved report files (PDF/CSV) in output_dir, for the Reports page's download list."""
        from ..reports import delivery
        return {"files": delivery.listing(self.cfg), "folder": str(self.cfg.output_dir())}

    def report_file(self, name):
        """Bytes of one saved file for download (only inside output_dir; no path traversal), or None."""
        from ..reports import delivery
        return delivery.read_file(self.cfg, name)

    def sync_now(self, full=False):
        """Run a sync now, in the background (the 'Sync now' / 'Full sync' buttons). At most one runs at a time; the
        dashboard polls /api/sync/status to know when it finished (or failed)."""
        with self.lock:
            if self._syncing:
                return {"started": False, "busy": True}
            self._syncing = True
            self._sync_info = {"running": True, "kind": "full" if full else "live", "finished_at": None, "error": None}
        threading.Thread(target=self._do_sync, args=(bool(full),), daemon=True).start()
        return {"started": True, "full": bool(full)}

    def sync_status(self):
        """Progress of a manual sync, for the dashboard to poll after the button is pressed."""
        info = dict(getattr(self, "_sync_info", {}) or {})
        info["running"] = bool(self._syncing)
        return info

    def _do_sync(self, full=False):
        from datetime import datetime
        err = None
        try:
            from .. import erp as _erp
            data_dir = self.cfg.path(self.cfg["store"]["path"]).parent
            with store.sync_lock(data_dir) as got:
                if not got:
                    err = "A scheduled sync is already running; try again in a moment."
                    return
                mapping = _erp.Mapping(self.cfg.path(self.cfg["erp"]["mapping"]))
                e = _erp.ERP(*_erp.connect(self.cfg["erp"], self.cfg.base_dir), mapping, self.cfg["sources"], self.cfg.base_dir)
                con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
                try:
                    if full:                              # the deep daily sync: all history + month-end fabric
                        res = store.sync(con, e, int(self.cfg["sync"]["history_days"]),
                                         full_history=self.cfg["sync"].get("full_history", store.FULL_HISTORY), log=lambda *_: None)
                    else:                                 # the light live sync: fast-changing entities, last few days
                        res = store.sync(con, e, int(self.cfg["sync"].get("live_days", 3)), entities=list(store.LIVE_ENTITIES),
                                         full_history=(), fabric_history=False, log=lambda *_: None, track_changes=True)
                    failed = res.get("_failed") or []
                    if failed:
                        err = "Some data sets did not sync: " + ", ".join(failed)
                finally:
                    con.close()
        except Exception as ex:
            err = f"{type(ex).__name__}: {ex}"[:200]
        finally:
            with self.lock:
                self._syncing = False
                self._sync_info = {"running": False, "kind": "full" if full else "live",
                                   "finished_at": datetime.now().strftime("%H:%M"), "error": err}

    def report(self, name, day, d0=None, d1=None):
        """A built report plus the day it is actually for (so the file name and on-screen title agree). d0/d1 are an
        optional From/To filter; the To date (or `day`) is the report's as-of."""
        from .. import report_registry
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return report_registry.build(con, self.cfg, name, day, d0, d1), report_registry.report_day(name, d1 or day)
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

    def closing(self, day, party, overrides):
        """The contract-closing report, optionally recomputed with per-yarn what-if closing counts (not saved)."""
        from ..textile import closing as cl
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return cl.contract_closing(con, self.cfg, day, party or None, overrides or None)
        finally:
            con.close()

    def closing_counts(self, day, party):
        """The warp/weft closing counts in effect for a party's closing, prefilled for the editable dashboard panel."""
        from ..textile import closing as cl
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return cl.closing_counts(con, self.cfg, day, party or None)
        finally:
            con.close()

    def save_closing_counts(self, counts, who, party, scope="yarn", clear=()):
        """Persist edited closing counts to the side file next to config.toml and apply them to this running server.
        scope 'yarn' saves the yarn default (all parties); 'party' saves for the selected party only. Only counts
        that differ from the current effective value are written (so an unchanged row never freezes config.toml)."""
        from .. import config as cfgmod
        from ..textile.method import Method
        method = Method(self.cfg["closing"])
        if scope == "party":                              # compare against the party's effective count
            pov = method.settings(party=party).get("_yarns", {})
            current = {str(c): {**method.yarn(c), **pov.get(str(c), {})} for c in set(counts or {})}
        else:                                             # compare against the yarn default
            current = {str(c): dict(method.yarn(c)) for c in set(counts or {})}
        current = {c: {k: v.get(k, v.get("closing_count")) for k in ("warp_closing_count", "weft_closing_count")}
                   for c, v in current.items()}
        res = cfgmod.save_closing_counts(self.cfg.base_dir, counts, who, party, scope=scope, clear=clear, current=current)
        if res.get("ok") and self.cfg.source_path:        # reload so the saved (and cleared) counts take effect now
            try:
                self.cfg = cfgmod.load(self.cfg.source_path)
            except Exception:
                pass
        return res

    def acknowledge(self, fid, title="", who="", undo=False):
        """Mark a Needs-attention finding read (or restore it). Kept in the local store's state by its fingerprint,
        so it stays cleared across refreshes and syncs until the same finding recurs."""
        from datetime import datetime
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            acked = store.get_state(con, "acked", {}) or {}
            if undo:
                acked.pop(fid, None)
            elif fid:
                acked[fid] = {"title": str(title)[:200], "by": str(who)[:60], "at": datetime.now().strftime("%Y-%m-%d %H:%M")}
            store.set_state(con, "acked", acked)
            return {"ok": True, "count": len(acked)}
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

    def fin_group(self, group):
        from . import finance
        con = store.open_store(self.cfg.path(self.cfg["store"]["path"]))
        try:
            return {"ledger": finance.group_ledger(con, group)}
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


def _static_response(name):
    """A bundled static file (Chart.js etc.) from web/static - offline, no CDN. Returns (code, body, ctype, headers)."""
    name = name.replace("\\", "/").lstrip("/")
    if "/" in name or ".." in name:
        return 404, {"error": "not found"}, "application/json", {}
    p = Path(__file__).with_name("static") / name
    if not p.is_file():
        return 404, {"error": "not found"}, "application/json", {}
    ctype = {"js": "application/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
             "woff2": "font/woff2", "svg": "image/svg+xml"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
    return 200, p.read_bytes(), ctype, {"Cache-Control": "max-age=86400"}


def _parse_counts(text):
    """Edited closing counts from the ?counts= query (JSON {code: {warp_closing_count, weft_closing_count}}).
    Bad or out-of-range values are dropped; an empty / broken string gives no overrides."""
    if not text:
        return {}
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return {}
    out = {}
    if isinstance(raw, dict):
        for code, vals in list(raw.items())[:300]:
            if not isinstance(vals, dict):
                continue
            keep = {}
            for k in ("warp_closing_count", "weft_closing_count"):
                v = vals.get(k)
                try:
                    if v not in (None, "", "-") and 0 < float(v) <= 500:
                        keep[k] = float(v)
                except (TypeError, ValueError):
                    pass
            if keep:
                out[str(code)] = keep
    return out


def _report_body(app, rep, name, tag, fmt, cell):
    """Turn a built Report into a (code, body, ctype, headers) response in json / csv / pdf. Shared by the report
    endpoint and the recomputed-closing endpoint so they render and download identically."""
    from .. import PRODUCT
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(rep.columns)
        w.writerows(rep.rows)
        return 200, buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}_{tag}.csv"'}
    if fmt == "pdf":
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = rep.write_pdf(tmp, f"{name}_{tag}.pdf", app.cfg["company"]["name"], PRODUCT)
            body = Path(p).read_bytes()
        return 200, body, "application/pdf", {"Content-Disposition": f'attachment; filename="{name}_{tag}.pdf"'}
    return 200, {"title": rep.title, "columns": rep.columns, "summary": rep.summary,
                 "rows": [[cell(v) for v in r] for r in rep.rows], "date": tag}, "application/json", {}


def get_response(app, path, query, check_licence=True):
    """Shared GET routing for the dashboard API, used by BOTH the on-prem server and the cloud (one App per tenant).
    The caller does its own authentication first. Returns (code, body, ctype, headers); body is bytes (page / static
    / download) or a dict (sent as JSON)."""
    from .. import licence, report_registry
    from ..reports import fmt as cell
    if path in ("/", "/index.html"):
        return 200, PAGE.read_bytes(), "text/html; charset=utf-8", {}
    if path.startswith("/static/"):
        return _static_response(path[len("/static/"):])
    if path == "/api/licence":
        st = licence.status(app.cfg)
        return 200, {"state": st.state, "message": st.message, "allowed": st.allowed,
                     "warning": st.warning, "machine": licence.machine_id()}, "application/json", {}
    if path == "/api/sync/status":                       # progress of a manual Sync now / Full sync (no data exposed)
        return 200, app.sync_status(), "application/json", {}
    if path == "/api/reports":
        return 200, {"reports": [{"name": n, "label": label, "range": rng} for n, label, rng in report_registry.menu()]}, "application/json", {}
    if path == "/api/reports/file":                      # download a saved report file (licensed below via the list block)
        params = {k: _unquote(v) for p in query.split("&") if "=" in p for k, v in [p.split("=", 1)]}
        body = app.report_file(params.get("name", ""))
        if body is None:
            return 404, {"error": "file not found"}, "application/json", {}
        name = params["name"]
        ctype = "application/pdf" if name.lower().endswith(".pdf") else "text/csv; charset=utf-8"
        return 200, body, ctype, {"Content-Disposition": f'attachment; filename="{name}"'}
    if path in ("/api/data", "/api/report", "/api/entities", "/api/browse", "/api/ledger",
                "/api/finance/parties", "/api/finance/daily", "/api/finance/group", "/api/closing", "/api/closing/counts",
                "/api/store/issues", "/api/reports/files", "/api/changes"):
        if check_licence:
            st = licence.status(app.cfg)
            if not st.allowed:
                return 403, {"licence": st.message, "machine": licence.machine_id()}, "application/json", {}
        params = {k: _unquote(v) for p in query.split("&") if "=" in p for k, v in [p.split("=", 1)]}
        if path == "/api/entities":
            return 200, {"entities": app.entities()}, "application/json", {}
        if path == "/api/browse":
            data = app.browse(params.get("entity", ""), params.get("q", ""), params.get("sort", ""),
                              params.get("dir", "desc"), params.get("limit", 200), params.get("offset", 0))
            return (200, data, "application/json", {}) if data else (404, {"error": "unknown data set"}, "application/json", {})
        if path == "/api/ledger":
            led = app.ledger(params.get("account", ""))
            return (200, led, "application/json", {}) if led else (404, {"error": "unknown account"}, "application/json", {})
        if path == "/api/finance/parties":
            return 200, app.fin_parties(params.get("group", "all"), params.get("q", "")), "application/json", {}
        if path == "/api/finance/group":                 # cash/bank transaction history for the Overview drill-down
            g = params.get("group", "cash")
            return 200, app.fin_group(g if g in ("cash", "bank") else "cash"), "application/json", {}
        if path == "/api/store/issues":                  # last issues of one item (store drill-down)
            return 200, app.issue_history(params.get("item", ""), params.get("limit", 20)), "application/json", {}
        if path == "/api/changes":                       # what changed since the client's last refresh (animations)
            return 200, app.changes(params.get("since", "")), "application/json", {}
        if path == "/api/reports/files":                 # saved report files for the Reports page
            return 200, app.reports_files(), "application/json", {}
        try:
            day = date.fromisoformat(params["date"]) if params.get("date") else None
        except ValueError:
            return 400, {"error": "date must be YYYY-MM-DD"}, "application/json", {}
        if path == "/api/finance/daily":
            return 200, app.fin_daily(day), "application/json", {}
        if path == "/api/data":
            return 200, app.data(day), "application/json", {}
        if path == "/api/closing/counts":
            return 200, app.closing_counts(day, params.get("party", "")), "application/json", {}
        if path == "/api/closing":                      # the closing report, recomputed with edited counts
            overrides = _parse_counts(params.get("counts", ""))
            rep = app.closing(day, params.get("party", ""), overrides)
            name = "closing-whatif" if overrides else "closing"   # a recomputed PDF/CSV is never named like the real one
            return _report_body(app, rep, name, (day or date.today()).isoformat(), params.get("fmt", "json"), cell)
        name, fmt = params.get("name", ""), params.get("fmt", "json")   # a report
        if name not in report_registry.REPORTS:
            return 404, {"error": "unknown report"}, "application/json", {}
        try:                                            # optional From/To filter (To, or ?date=, is the as-of)
            d0 = date.fromisoformat(params["from"]) if params.get("from") else None
            d1 = date.fromisoformat(params["to"]) if params.get("to") else None
        except ValueError:
            return 400, {"error": "from/to must be YYYY-MM-DD"}, "application/json", {}
        rep, on = app.report(name, day, d0, d1)
        return _report_body(app, rep, name, on.isoformat(), fmt, cell)
    return 404, {"error": "not found"}, "application/json", {}


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
            self._emit(*get_response(app, path, query))

        def _emit(self, code, body, ctype, headers):
            """Send a (code, body, ctype, headers) tuple from get_response: body is bytes or a JSON-able object."""
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

        def _static(self, name):
            code, body, ctype, headers = _static_response(name)
            if not isinstance(body, (bytes, bytearray)):
                return self._send(code, body)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
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
                started = app.kick_first_sync() if st.allowed else False
                msg = st.message + (" Fetching the first data now - the dashboard will fill in shortly." if started else "")
                return self._send(200 if st.allowed else 400, {"state": st.state, "message": msg,
                                                               "allowed": st.allowed, "first_sync": started})
            if self.path == "/api/sync":                  # 'Sync now' / 'Full sync' button: run in the background
                from .. import licence
                if not licence.status(app.cfg).allowed:
                    return self._send(403, {"error": "Licence: " + licence.status(app.cfg).message})
                try:
                    n = min(int(self.headers.get("Content-Length", 0)), 1000)
                    full = bool(json.loads(self.rfile.read(n) or b"{}").get("full"))
                except Exception:
                    full = False
                return self._send(200, app.sync_now(full))
            if self.path == "/api/ack":                   # mark a Needs-attention finding read (or restore it)
                from .. import licence
                if not licence.status(app.cfg).allowed:
                    return self._send(403, {"error": "Licence: " + licence.status(app.cfg).message})
                try:
                    n = min(int(self.headers.get("Content-Length", 0)), 20000)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    return self._send(200, app.acknowledge(str(body.get("id", ""))[:200], body.get("title", ""),
                                                           body.get("who", ""), bool(body.get("undo"))))
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})
            if self.path == "/api/closing/counts":       # save edited closing counts (dashboard password already checked)
                from .. import licence
                if not licence.status(app.cfg).allowed:
                    return self._send(403, {"error": "Licence: " + licence.status(app.cfg).message})
                try:
                    n = min(int(self.headers.get("Content-Length", 0)), 100000)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    scope = "party" if str(body.get("scope", "yarn")) == "party" else "yarn"
                    clear = [str(c) for c in (body.get("clear") or [])][:300]
                    res = app.save_closing_counts(body.get("counts", {}) or {},
                                                  str(body.get("who", "")).strip()[:60],
                                                  str(body.get("party", "")).strip()[:80],
                                                  scope=scope, clear=clear)
                    return self._send(200 if res.get("ok") else 400, res)
                except Exception as e:
                    return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})
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


def _stale_watchdog(cfg):
    """The dashboard process is always up, so it can watch its own freshness: if no sync has run for longer than
    [sync] stale_alert_minutes, push ONE ntfy alert (and again if it recovers and goes stale once more). This is
    the backstop for a silently stopped Task Scheduler - the owner should not have to notice by chance."""
    import time
    from datetime import datetime
    from .. import notify
    limit = int(cfg["sync"].get("stale_alert_minutes", 90))
    if limit <= 0 or "ntfy" not in (cfg["notify"].get("channels") or []):
        return                                            # nothing to push to; skip the watchdog entirely
    alerted = False
    while True:
        time.sleep(300)                                   # check every 5 minutes
        try:
            con = store.open_store(cfg.path(cfg["store"]["path"]))
            try:
                last = con.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()[0]
            finally:
                con.close()
            age = ((datetime.now() - datetime.fromisoformat(str(last)[:19])).total_seconds() / 60) if last else 1e9
            if age > limit and not alerted:
                notify.send(cfg, "Smart Layer: data not syncing",
                            f"No sync has run for {age / 60:.1f} hours (limit {limit} min). The scheduled sync may "
                            f"have stopped - check the Smart Layer tasks in Task Scheduler on the server.",
                            priority="high", tags="warning")
                alerted = True
            elif age <= limit:
                alerted = False                           # recovered; arm again for the next outage
        except Exception:
            pass                                          # never let the watchdog take the server down


def serve(cfg):
    host = cfg["serve"].get("host", "127.0.0.1")
    port = int(cfg["serve"].get("port", 8080))
    app = App(cfg)
    if host not in ("127.0.0.1", "localhost") and not app.password:
        raise SystemExit("Set [serve] password before opening the dashboard to the network (host is not 127.0.0.1).")
    threading.Thread(target=_stale_watchdog, args=(cfg,), daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), handler(app))
    print(f"Dashboard on http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
