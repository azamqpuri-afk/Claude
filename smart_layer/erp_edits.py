"""
ERP edit history -> the Edits tab (item 31).

Reads the optional change-history entities (erp_change_events + erp_change_values) that a mill's ERP can be made
to write (e.g. Oracle triggers into a locked AUDIT_HIST schema). Smart Layer only ever READS them. Everything here
is generic: the mapping turns the ERP's own history into the two canonical entities; this module turns those into
a human list ("who changed what, when"), a per-event old->new detail, flags for the audit, and the figures the
pushes and the morning summary need. A mill without the history layer has empty entities and simply no Edits tab.

Nothing is mill-specific here. The few ERP table codes that have a nicer name (TM -> Voucher ...) come from a
default map that [edits].table_names can override per mill.
"""

import re
from datetime import date, datetime, timedelta

# Default friendly names for the common Dost/Oracle table codes; any mill can override via [edits].table_names.
# Kept here only as sensible defaults - the core does not depend on these codes existing.
AREA_NAMES = {"TM": "Voucher", "TD": "Voucher line", "DMN": "Store document", "DDT": "Store line",
              "ACH": "Cheque", "I": "Item", "CONTM": "Contract"}
# A detail-line table resolves its document number/date/user from its header table (same TRANS_NO).
LINE_PARENT = {"TD": "TM", "DDT": "DMN"}

ACTION_WORD = {"I": "Added", "U": "Edited", "D": "Deleted"}

# Column-name fragments that mark a changed field as money / quantity / rate / account - used to highlight the
# "material" edits (amount, qty, rate, account) the owner cares about. Matched token-wise (see _col_is) so short
# fragments like "dr"/"ac" do not match inside unrelated words.
MONEY_WORDS = ("amount", "amt", "debit", "credit", "dr", "cr", "value", "val", "rate", "price",
               "qty", "quantity", "net", "gross", "disc", "discount", "balance", "bal", "weight", "wt", "kg")
ACCOUNT_WORDS = ("account", "acno", "acc", "acode", "ac", "head", "party", "cust", "customer",
                 "supplier", "supp", "vendor", "vend", "coa", "glcode")
ITEM_WORDS = ("item", "itm", "ic", "article", "product", "prod")


def installed(con):
    """True once the change-history layer has been synced at least once (so the Edits tab should show). A mill
    without the AUDIT_HIST tables never syncs these entities, so this stays False and the tab stays hidden."""
    try:
        row = con.execute("SELECT 1 FROM sync_log WHERE entity = 'erp_change_events' LIMIT 1").fetchone()
        return bool(row)
    except Exception:
        return False


def status(con):
    """'installed' / 'not installed' plus the row count and newest change time, for the Edits tab header and check."""
    if not installed(con):
        return {"installed": False, "events": 0, "latest": None}
    n = con.execute("SELECT COUNT(*) FROM erp_change_events").fetchone()[0]
    latest = con.execute("SELECT MAX(changed_at) FROM erp_change_events").fetchone()[0]
    return {"installed": True, "events": n, "latest": latest}


def _settings(cfg):
    e = dict(cfg["edits"]) if cfg else {}
    areas = dict(AREA_NAMES)
    areas.update({str(k): str(v) for k, v in (e.get("table_names") or {}).items()})
    parent = dict(LINE_PARENT)
    parent.update({str(k): str(v) for k, v in (e.get("line_parent") or {}).items()})
    money = set(MONEY_WORDS) | {str(w).lower() for w in (e.get("amount_keywords") or [])}
    generic = {str(u).strip().upper() for u in (e.get("generic_users") or ["DOSTEX"])}
    return {"areas": areas, "parent": parent, "money": money, "generic": generic,
            "system_modules": {str(m).strip().upper() for m in (e.get("system_modules") or ["UPDATE_AVG_RATE"])},
            "system_columns": {str(c).strip().upper() for c in (e.get("system_columns") or ["AVG_RATE"])},
            "weblogic_access_log": e.get("weblogic_access_log") or "",
            "ip_names": e.get("ip_names") or {},
            "log_match_window_seconds": int(e.get("log_match_window_seconds", 180) or 180),
            "server_hosts": e.get("server_hosts") or ["DESKTOP-KUMU41H", "127.0.0.1", "localhost"],
            "old_days": int(e.get("old_edit_days", 2) or 0), "pair_minutes": int(e.get("pair_minutes", 5) or 0)}


def _is_system(module, action, changed_cols, s):
    """True for an ERP's own routine posting (not a person): its Oracle MODULE is in system_modules, or it is an
    update whose ONLY changed column(s) are in system_columns (e.g. the average-cost re-rating that touches only
    AVG_RATE). These are grouped into one line and never pushed; real human edits still alert."""
    if str(module or "").strip().upper() in s["system_modules"]:
        return True
    if action != "U":
        return False
    cols = {str(c).strip().upper() for c in (changed_cols or [])}
    return bool(cols) and cols <= s["system_columns"]


def _collapse_system(rows):
    """Collapse a run of adjacent ERP-routine rows (same module) into one grouped line, so 114 AVG_RATE updates show
    as a single 'ERP rate update - 114 lines, AVG_RATE only' instead of 114 rows. `rows` is newest-first."""
    out, i, n = [], 0, len(rows)
    while i < n:
        r = rows[i]
        if not r.get("is_system"):
            out.append(r); i += 1
            continue
        mod = (r.get("module") or "")
        j, grp = i, []
        while j < n and rows[j].get("is_system") and (rows[j].get("module") or "") == mod:
            grp.append(rows[j]); j += 1
        if len(grp) == 1:
            out.append(grp[0])
        else:
            cols = sorted({c for g in grp for c in (g.get("system_cols") or [])})
            label = (mod.replace("_", " ").title() if mod else "ERP routine")
            summ = f"{label} - {len(grp)} lines" + (f", {', '.join(cols)} only" if cols else "")
            out.append({"grouped": True, "count": len(grp), "event_id": grp[0]["event_id"], "at": grp[0]["at"],
                        "table": grp[0]["table"], "area": grp[0]["area"], "action": "U", "action_word": "ERP routine",
                        "module": mod, "user": grp[0]["user"], "pc": grp[0].get("pc"),
                        "pc_by_log": grp[0].get("pc_by_log", False), "doc": f"{len(grp)} lines", "summary": summ,
                        "fields": sum(g.get("fields", 0) for g in grp), "is_system": True, "is_delete": False,
                        "is_old_edit": False, "touches_amount": False, "touches_account": False})
        i = j
    return out


def _strip_host(v):
    """'WORKGROUP\\DESKTOP-KUMU41H' -> 'DESKTOP-KUMU41H'; a plain name passes through."""
    s = str(v or "").strip()
    return s.rsplit("\\", 1)[-1] if "\\" in s else s


def _display_user(user, pc, client_host, os_user, generic):
    """Who to show. The store module stamps the DB owner (e.g. DOSTEX) on every row, which says nothing about the
    person, so when the user is blank or a generic owner, fall back to the PC, then the client host, then the OS
    user - whichever first identifies the workstation."""
    u = str(user or "").strip()
    if u and u.upper() not in (generic or set()):
        return u
    for cand in (pc, _strip_host(client_host), os_user):
        c = str(cand or "").strip()
        if c:
            return _strip_host(c)
    return u or "—"


def _col_is(col, words):
    """True if the column name carries one of the keyword fragments. Split into alnum tokens so a short fragment
    ('dr', 'ac', 'ic') must BE a whole token, while a longer one (>=4 chars) may also match as a substring."""
    c = str(col or "").lower()
    toks = re.split(r"[^a-z0-9]+", c)
    for w in words:
        if w in toks or (len(w) >= 4 and w in c):
            return True
    return False


def area_label(settings, table_name):
    t = str(table_name or "").strip()
    return settings["areas"].get(t) or settings["areas"].get(t.upper()) or (t.replace("_", " ").title() if t else "—")


def _is_old_edit(action, changed_at, doc_date, old_days):
    """True for an edit/delete of a document that was already older than old_days WHEN the change was made (i.e.
    change time minus document date). Measured against the change time, not today, so it reads the same however
    long ago the change happened."""
    if action not in ("U", "D") or not doc_date or not changed_at:
        return False
    try:
        return (date.fromisoformat(str(changed_at)[:10]) - date.fromisoformat(str(doc_date)[:10])).days > old_days
    except (ValueError, TypeError):
        return False


class _WebLogicLog:
    """Optional: attribute an event to the real browser PC by matching its time to the WebLogic access log.

    Oracle Forms runs as frmweb on the DB server, so every event's recorded PC/host is the server. The real client
    IP is only in the WebLogic access log (POST /forms/lservlet lines). When [edits].weblogic_access_log is set,
    this parses those lines once, and pc_for(time) returns the client IP whose request is closest within +-60 s.
    The IP is mapped to a name via [edits].ip_names (127.0.0.1 -> 'server itself'). No log configured = disabled.
    """
    LINE = re.compile(r'^(\d{1,3}(?:\.\d{1,3}){3})\b.*\[(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2})')

    _IP = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

    def __init__(self, settings):
        self.path = str((settings or {}).get("weblogic_access_log") or "").strip()
        self.ip_names = {str(k): str(v) for k, v in ((settings or {}).get("ip_names") or {}).items()}
        self.window = int((settings or {}).get("log_match_window_seconds", 180) or 180)
        # server-side IPs (the Forms server itself / loopback): their lservlet hits are a keep-alive heartbeat, not a
        # person, so a real client IP in the same window always wins over them.
        self.server_ips = {str(h).strip() for h in ((settings or {}).get("server_hosts") or []) if self._IP.match(str(h).strip())}
        self.server_ips |= {"127.0.0.1", "::1"}
        self.enabled = bool(self.path)
        self._events = None                                 # sorted [(epoch, ip)] of lservlet POSTs, parsed lazily

    def _parse(self):
        import glob
        from datetime import datetime as _dt
        self._events = []
        if not self.enabled:
            return
        try:
            files = sorted(glob.glob(self.path))
        except Exception:
            files = []
        seen = 0
        for f in files:
            try:
                with open(f, "r", errors="ignore") as fh:
                    for line in fh:
                        if "lservlet" not in line:
                            continue
                        m = self.LINE.match(line)
                        if not m:
                            continue
                        try:
                            ts = _dt.strptime(m.group(2), "%d/%b/%Y:%H:%M:%S").timestamp()
                        except ValueError:
                            continue
                        self._events.append((ts, m.group(1)))
                        seen += 1
                        if seen >= 500000:                  # safety cap on a huge log
                            break
            except OSError:
                continue
            if seen >= 500000:
                break
        self._events.sort()

    def pc_for(self, at, window=None):
        """The client PC name for an event time `at` (ISO string), from the lservlet requests within +-window
        seconds (default from [edits].log_match_window_seconds).

        The Forms server posts a keep-alive heartbeat from loopback ~30x/hour around the clock, so a naive
        nearest-request match would attribute everything to the server. Instead: gather every request in the window,
        drop the server/loopback IPs whenever a real client IP also has hits there, and pick the client IP with the
        most hits (tie-break: the request nearest in time). Only when the window holds server IPs alone is the
        server returned. Returns a name (via ip_names, else the bare IP) or None."""
        if window is None:
            window = self.window
        if not self.enabled:
            return None
        if self._events is None:
            self._parse()
        if not self._events:
            return None
        from datetime import datetime as _dt
        try:
            t = _dt.fromisoformat(str(at)[:19]).timestamp()
        except (ValueError, TypeError):
            return None
        import bisect
        times = self._times if hasattr(self, "_times") else [e[0] for e in self._events]
        self._times = times
        i = bisect.bisect_left(times, t)
        stats = {}                                          # ip -> [hits, nearest_distance]
        for j in range(i, len(self._events)):               # forward within the window
            d = self._events[j][0] - t
            if d > window:
                break
            s = stats.setdefault(self._events[j][1], [0, d]); s[0] += 1; s[1] = min(s[1], d)
        for j in range(i - 1, -1, -1):                      # backward within the window
            d = t - self._events[j][0]
            if d > window:
                break
            s = stats.setdefault(self._events[j][1], [0, d]); s[0] += 1; s[1] = min(s[1], d)
        if not stats:
            return None
        clients = {ip: v for ip, v in stats.items() if ip not in self.server_ips}
        pool = clients or stats                             # ignore the server heartbeat unless it is all there is
        best = min(pool, key=lambda ip: (-pool[ip][0], pool[ip][1]))   # most hits, then nearest in time
        return self.ip_names.get(best, best)


def _resolve_pc(s, wlog, pcn, chost, osuser, client_ip, at):
    """Return (display_pc, by_log): the PC to show, and whether it came from the access-log match. When the recorded
    PC/host is really the Forms server, try the WebLogic log; otherwise show the recorded PC."""
    recorded = _strip_host(pcn) or _strip_host(chost) or (str(osuser).strip() if osuser else "") or None
    servers = {str(h).strip().upper() for h in (s.get("server_hosts") or [])}
    is_server = (str(recorded or "").upper() in servers) or (str(client_ip or "").strip() in servers) or not recorded
    if wlog.enabled and is_server:
        match = wlog.pc_for(at)
        if match:
            # a request from the Forms server itself (loopback) means the user worked on the server console or over
            # AnyDesk, not a networked PC - name it plainly, with no "by log match" dressing
            if str(match).strip().lower() in ("127.0.0.1", "localhost", "::1"):
                return ("Server (local/AnyDesk)", True)
            return (f"{match} (by log match)", True)
    return (recorded, False)


def _rowkey_field(row_key, field):
    """Pull a field out of a row_key like 'TRANS_NO=123;SR_NO=2'. Case-insensitive on the field name."""
    for part in str(row_key or "").split(";"):
        k, _, v = part.partition("=")
        if k.strip().upper() == field.upper():
            return v.strip()
    return None


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _fmt_val(v, names=None):
    """Render one old/new value for display: group thousands for numbers, resolve a known account/item code to its
    name, blank for a missing value."""
    if v is None or str(v).strip() == "":
        return "∅"
    s = str(v).strip()
    if names and s in names:
        return f"{s} ({names[s]})"
    n = _num(s)
    if n is not None and re.fullmatch(r"-?\d+(\.\d+)?", s):
        if n == int(n):
            return f"{int(n):,}"
        return f"{n:,.2f}"
    return s


def _resolve_names(con):
    """code -> name maps so an account/item code in a changed value can be shown as its name."""
    names = {}
    for t, code, nm in (("accounts", "account_code", "account_name"), ("items", "item_code", "item_name"),
                        ("parties", "party_code", "party_name")):
        try:
            for c, n in con.execute(f"SELECT {code}, {nm} FROM {t}"):
                if c is not None and n:
                    names.setdefault(str(c).strip(), str(n))
        except Exception:
            pass
    return names


def _values(con, event_ids):
    """{event_id: [(column_name, old_value, new_value), ...]} for a set of events, in a stable order."""
    out = {}
    if not event_ids:
        return out
    ids = list(event_ids)
    for i in range(0, len(ids), 400):                      # chunk to keep the IN(...) list sane
        chunk = ids[i:i + 400]
        marks = ",".join("?" * len(chunk))
        for eid, col, ov, nv in con.execute(
                f"SELECT event_id, column_name, old_value, new_value FROM erp_change_values "
                f"WHERE event_id IN ({marks}) ORDER BY event_id, rowid", chunk):
            out.setdefault(eid, []).append((col, ov, nv))
    return out


def _change_summary(settings, vals, names):
    """A short 'what changed' line from the changed fields, e.g. 'DR 50,000 → 5,000; AC 101 → 205'. Also returns
    whether any money/qty/rate or account field changed (for the highlight)."""
    pieces, touches_money, touches_account = [], False, False
    for col, ov, nv in vals:
        money = _col_is(col, settings["money"])
        item = _col_is(col, ITEM_WORDS)
        acct = _col_is(col, ACCOUNT_WORDS) or item     # an item swap (IC) is as material as an account change
        touches_money = touches_money or money
        touches_account = touches_account or acct
        nm = names if acct else None
        pieces.append(f"{col} {_fmt_val(ov, nm)} → {_fmt_val(nv, nm)}")
    return "; ".join(pieces[:8]) + (" …" if len(pieces) > 8 else ""), touches_money, touches_account


def _header_index(con, settings, table):
    """For a detail table (TD/DDT), map TRANS_NO -> {doc, date, user, pc} so a line edit can show its document
    number, date, who and where. Two sources, in order: the header's OWN change events (when the header was edited
    too), then the live-document entity erp_doc_headers (the header still in the ERP even when only lines changed -
    this is what makes the doc/date/user resolve for a line-only edit, and lets is_old_edit fire)."""
    parent = settings["parent"].get(table) or settings["parent"].get(str(table).upper())
    if not parent:
        return {}
    idx = {}
    for rk, dref, ddt, usr in con.execute(
            "SELECT row_key, doc_ref, doc_date, erp_user FROM erp_change_events WHERE table_name = ? OR table_name = ?",
            (parent, parent.upper())):
        tn = _rowkey_field(rk, "TRANS_NO")
        if tn and tn not in idx:
            idx[tn] = {"doc": dref, "date": ddt, "user": usr, "pc": None}
    try:
        for tn, dref, ddt, usr, pc in con.execute(
                "SELECT trans_no, doc_ref, doc_date, erp_user, pc_name FROM erp_doc_headers "
                "WHERE table_name = ? OR table_name = ?", (parent, parent.upper())):
            tn = str(tn)
            cur = idx.get(tn)
            if cur is None:
                idx[tn] = {"doc": dref, "date": ddt, "user": usr, "pc": pc}
            else:                                          # fill gaps the header event did not carry
                cur["doc"] = cur["doc"] or dref
                cur["date"] = cur["date"] or ddt
                cur["user"] = cur["user"] or usr
                cur["pc"] = cur["pc"] or pc
    except Exception:
        pass                                               # erp_doc_headers not mapped -> header events only
    return idx


def list_edits(con, cfg, d0=None, d1=None, user=None, area=None, action=None, doc=None, limit=500):
    """The Edits tab list, newest change first. Filters: date range on the CHANGE time (d0..d1), ERP user, area
    (ERP table_name), action (I/U/D), and a document-number search (doc_ref / row_key contains)."""
    if not installed(con):
        return {"installed": False, "rows": [], "users": [], "areas": [], "total": 0}
    s = _settings(cfg)
    # date / area / action narrow in SQL; doc and user are matched AFTER resolution (a line's doc/user come from its
    # header, so filtering the raw columns would miss them - DBSERVER's live finding).
    where, params = [], []
    if d0:
        where.append("substr(changed_at,1,10) >= ?"); params.append(d0.isoformat() if hasattr(d0, "isoformat") else str(d0))
    if d1:
        where.append("substr(changed_at,1,10) <= ?"); params.append(d1.isoformat() if hasattr(d1, "isoformat") else str(d1))
    if area:
        where.append("table_name = ?"); params.append(area)
    if action:
        where.append("action = ?"); params.append(str(action)[:1].upper())
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    CAP = 6000                                             # resolve at most this many recent matching events
    rows = con.execute(
        f"SELECT event_id, changed_at, table_name, action, row_key, doc_ref, doc_date, erp_user, pc_name, "
        f"client_host, os_user, module, client_ip FROM erp_change_events{wsql} ORDER BY event_id DESC LIMIT ?",
        [*params, CAP]).fetchall()
    names = _resolve_names(con)
    vals = _values(con, [r[0] for r in rows])
    headers = {}                                           # table -> TRANS_NO header index (built lazily per table)
    wlog = _WebLogicLog(s)                                 # item 6: real PC by access-log match (when configured)
    doc_q = str(doc).lower() if doc else None
    user_q = str(user).lower() if user else None
    out = []
    for eid, at, table, act, rk, dref, ddt, usr, pc, chost, osuser, module, cip in rows:
        s_area = area_label(s, table)
        # resolve the document, date, user and PC - for a detail line, from its header (event or live document)
        doc_txt, doc_date, who, pcn = dref, ddt, usr, pc
        if table in s["parent"] or str(table).upper() in s["parent"]:
            headers.setdefault(table, _header_index(con, s, table))
            tn = _rowkey_field(rk, "TRANS_NO")
            hd = headers[table].get(tn) if tn else None
            if hd:
                doc_txt = f"{hd['doc']} · {dref}" if (dref and hd["doc"]) else (hd["doc"] or dref)
                doc_date = doc_date or hd["date"]
                who = who or hd["user"]
                pcn = pcn or hd["pc"]
        pc_disp, pc_by_log = _resolve_pc(s, wlog, pcn, chost, osuser, cip, at)
        who = _display_user(who, pcn, chost, osuser, s["generic"])
        if doc_q and doc_q not in str(doc_txt or "").lower() and doc_q not in str(rk or "").lower():
            continue
        if user_q and user_q not in str(who or "").lower():
            continue
        changed_cols = [c for (c, _ov, _nv) in vals.get(eid, [])]
        summary, touches_money, touches_account = _change_summary(s, vals.get(eid, []), names)
        old_edit = _is_old_edit(act, at, doc_date, s["old_days"])
        sysr = _is_system(module, act, changed_cols, s)
        out.append({
            "event_id": eid, "at": str(at)[:19] if at else None, "table": table, "area": s_area,
            "action": act, "action_word": ACTION_WORD.get(act, act), "row_key": rk, "module": module,
            "doc": doc_txt, "doc_date": str(doc_date)[:10] if doc_date else None, "user": who,
            "pc": pc_disp, "pc_by_log": pc_by_log, "summary": summary, "fields": len(vals.get(eid, [])),
            "system_cols": changed_cols, "is_system": sysr,
            "is_delete": act == "D" and not sysr, "is_old_edit": old_edit and not sysr,
            "touches_amount": (touches_money or touches_account) and not sysr,
            "touches_account": touches_account and not sysr,
        })
    out = _collapse_system(out)                            # 114 AVG_RATE rows -> one grouped line, before the limit
    total = len(out)
    out = out[:int(limit)]
    _mark_pairs(out, s["pair_minutes"])
    # the user dropdown uses the RESOLVED display users (so it shows the PC/host for generic DB-owner logins, which
    # is what the filter matches), not the raw erp_user column (DBSERVER: it was showing only "DOSTEX").
    users = sorted({r["user"] for r in by_user(con, d0, d1, cfg) if r["user"] and r["user"] != "—"})
    areas = [{"table": t, "area": area_label(s, t)} for (t,) in con.execute(
        "SELECT DISTINCT table_name FROM erp_change_events WHERE table_name IS NOT NULL ORDER BY 1")]
    return {"installed": True, "rows": out, "users": users, "areas": areas, "total": total}


def _mark_pairs(rows, pair_minutes):
    """Flag a delete + insert of the SAME line (row_key) within pair_minutes of each other - some ERP screens save
    a detail line by deleting and re-inserting it, which otherwise shows as a scary delete. `rows` is newest-first."""
    if pair_minutes <= 0:
        return
    by_key = {}
    for r in rows:
        if not r.get("row_key") or r["action"] not in ("I", "D") or not r.get("at"):
            continue
        by_key.setdefault(r["row_key"], []).append(r)
    for key, group in by_key.items():
        for a in group:
            if a["action"] != "D":
                continue
            for b in group:
                if b["action"] != "I":
                    continue
                try:
                    ta, tb = datetime.fromisoformat(a["at"]), datetime.fromisoformat(b["at"])
                except ValueError:
                    continue
                if abs((ta - tb).total_seconds()) <= pair_minutes * 60:
                    a["paired"] = True
                    b["paired"] = True


def detail(con, cfg, event_id):
    """Every field of one change event, old → new, plus the event's header line. For a delete, every old field; for
    an edit, only the fields that changed; for an insert, no field rows (the values live in the ERP)."""
    if not installed(con):
        return None
    row = con.execute(
        "SELECT event_id, changed_at, table_name, action, row_key, doc_ref, doc_date, erp_user, pc_name, ip_addr, "
        "db_user, os_user, client_host, client_ip, module FROM erp_change_events WHERE event_id = ?",
        (event_id,)).fetchone()
    if not row:
        return None
    s = _settings(cfg)
    names = _resolve_names(con)
    cols = ["event_id", "changed_at", "table_name", "action", "row_key", "doc_ref", "doc_date", "erp_user",
            "pc_name", "ip_addr", "db_user", "os_user", "client_host", "client_ip", "module"]
    ev = dict(zip(cols, row))
    vals = _values(con, [event_id]).get(event_id, [])
    fields = []
    for col, ov, nv in vals:
        nm = names if _col_is(col, ACCOUNT_WORDS) or _col_is(col, ITEM_WORDS) else None
        fields.append({"column": col, "old": _fmt_val(ov, nm), "new": _fmt_val(nv, nm),
                       "material": _col_is(col, s["money"]) or _col_is(col, ACCOUNT_WORDS) or _col_is(col, ITEM_WORDS)})
    # resolve the document / date / user / PC from the header for a detail line (same as the list)
    doc_txt, doc_date, who, pcn = ev["doc_ref"], ev["doc_date"], ev["erp_user"], ev["pc_name"]
    table = ev["table_name"]
    if table in s["parent"] or str(table).upper() in s["parent"]:
        tn = _rowkey_field(ev["row_key"], "TRANS_NO")
        hd = _header_index(con, s, table).get(tn) if tn else None
        if hd:
            doc_txt = f"{hd['doc']} · {ev['doc_ref']}" if (ev["doc_ref"] and hd["doc"]) else (hd["doc"] or ev["doc_ref"])
            doc_date = doc_date or hd["date"]; who = who or hd["user"]; pcn = pcn or hd["pc"]
    who = _display_user(who, pcn, ev["client_host"], ev["os_user"], s["generic"])
    pc_disp, pc_by_log = _resolve_pc(s, _WebLogicLog(s), pcn, ev["client_host"], ev["os_user"], ev["client_ip"], ev["changed_at"])
    return {
        "event_id": ev["event_id"], "at": str(ev["changed_at"])[:19] if ev["changed_at"] else None,
        "table": ev["table_name"], "area": area_label(s, ev["table_name"]),
        "action": ev["action"], "action_word": ACTION_WORD.get(ev["action"], ev["action"]),
        "row_key": ev["row_key"], "doc": doc_txt, "doc_date": str(doc_date)[:10] if doc_date else None,
        "user": who, "pc": pc_disp or _strip_host(pcn), "pc_by_log": pc_by_log, "ip": ev["ip_addr"],
        "module": ev["module"],
        "session": {k: ev[k] for k in ("db_user", "os_user", "client_host", "client_ip", "module") if ev[k]},
        "fields": fields,
    }


def by_user(con, d0=None, d1=None, cfg=None):
    """Edit/insert/delete counts per ERP user over a date range (default: all). For the morning line and the
    'edits by ACCOUNT per day' owner check. Detail-line events whose own user is blank are attributed to their
    header's user (so line edits are not all bucketed under an unknown user). Call with a date range - it reads the
    matching event rows, so it is meant for a day / a short span, not the whole history."""
    if not installed(con):
        return []
    s = _settings(cfg)
    where, params = [], []
    if d0:
        where.append("substr(changed_at,1,10) >= ?"); params.append(d0.isoformat() if hasattr(d0, "isoformat") else str(d0))
    if d1:
        where.append("substr(changed_at,1,10) <= ?"); params.append(d1.isoformat() if hasattr(d1, "isoformat") else str(d1))
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = con.execute(f"SELECT table_name, action, erp_user, row_key, pc_name, client_host, os_user "
                       f"FROM erp_change_events{wsql}", params).fetchall()
    headers, agg = {}, {}
    for table, act, usr, rk, pc, chost, osuser in rows:
        who, pcn = usr, pc
        if (not who or who.strip().upper() in s["generic"]) and (table in s["parent"] or str(table).upper() in s["parent"]):
            headers.setdefault(table, _header_index(con, s, table))   # a detail line: borrow the header user/pc
            tn = _rowkey_field(rk, "TRANS_NO")
            hd = headers[table].get(tn) if tn else None
            if hd:
                who = who or hd["user"]; pcn = pcn or hd["pc"]
        who = _display_user(who, pcn, chost, osuser, s["generic"])
        a = agg.setdefault(who, {"user": who, "total": 0, "inserts": 0, "edits": 0, "deletes": 0})
        a["total"] += 1
        a["inserts" if act == "I" else "edits" if act == "U" else "deletes" if act == "D" else "total"] += 0 if act not in ("I", "U", "D") else 1
    return sorted(agg.values(), key=lambda r: -r["total"])
