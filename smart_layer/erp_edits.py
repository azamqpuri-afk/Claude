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
    return {"areas": areas, "parent": parent, "money": money,
            "old_days": int(e.get("old_edit_days", 2) or 0), "pair_minutes": int(e.get("pair_minutes", 5) or 0)}


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
        acct = _col_is(col, ACCOUNT_WORDS)
        touches_money = touches_money or money
        touches_account = touches_account or acct
        nm = names if (acct or _col_is(col, ITEM_WORDS)) else None
        pieces.append(f"{col} {_fmt_val(ov, nm)} → {_fmt_val(nv, nm)}")
    return "; ".join(pieces[:8]) + (" …" if len(pieces) > 8 else ""), touches_money, touches_account


def _header_index(con, settings, table):
    """For a detail table (TD/DDT), map TRANS_NO -> (doc_ref, doc_date, erp_user) from its header table's events,
    so a line edit can show the voucher/document number, its date and who keyed the header."""
    parent = settings["parent"].get(table) or settings["parent"].get(str(table).upper())
    if not parent:
        return {}
    idx = {}
    for rk, dref, ddt, usr in con.execute(
            "SELECT row_key, doc_ref, doc_date, erp_user FROM erp_change_events WHERE table_name = ? OR table_name = ?",
            (parent, parent.upper())):
        tn = _rowkey_field(rk, "TRANS_NO")
        if tn:
            idx.setdefault(tn, (dref, ddt, usr))           # first header row wins; they share the TRANS_NO's doc
    return idx


def list_edits(con, cfg, d0=None, d1=None, user=None, area=None, action=None, doc=None, limit=500):
    """The Edits tab list, newest change first. Filters: date range on the CHANGE time (d0..d1), ERP user, area
    (ERP table_name), action (I/U/D), and a document-number search (doc_ref / row_key contains)."""
    if not installed(con):
        return {"installed": False, "rows": [], "users": [], "areas": [], "total": 0}
    s = _settings(cfg)
    where, params = [], []
    if d0:
        where.append("substr(changed_at,1,10) >= ?"); params.append(d0.isoformat() if hasattr(d0, "isoformat") else str(d0))
    if d1:
        where.append("substr(changed_at,1,10) <= ?"); params.append(d1.isoformat() if hasattr(d1, "isoformat") else str(d1))
    if user:
        where.append("erp_user = ?"); params.append(user)
    if area:
        where.append("table_name = ?"); params.append(area)
    if action:
        where.append("action = ?"); params.append(str(action)[:1].upper())
    if doc:
        where.append("(doc_ref LIKE ? OR row_key LIKE ?)"); params += [f"%{doc}%", f"%{doc}%"]
    wsql = (" WHERE " + " AND ".join(where)) if where else ""
    total = con.execute(f"SELECT COUNT(*) FROM erp_change_events{wsql}", params).fetchone()[0]
    rows = con.execute(
        f"SELECT event_id, changed_at, table_name, action, row_key, doc_ref, doc_date, erp_user, pc_name "
        f"FROM erp_change_events{wsql} ORDER BY event_id DESC LIMIT ?", [*params, int(limit)]).fetchall()
    names = _resolve_names(con)
    vals = _values(con, [r[0] for r in rows])
    headers = {}                                           # table -> TRANS_NO header index (built lazily per table)
    out = []
    for eid, at, table, act, rk, dref, ddt, usr, pc in rows:
        s_area = area_label(s, table)
        # resolve the document and (for detail lines) the user from the header when the line has none
        doc_txt, doc_date, who = dref, ddt, usr
        if table in s["parent"] or str(table).upper() in s["parent"]:
            headers.setdefault(table, _header_index(con, s, table))
            tn = _rowkey_field(rk, "TRANS_NO")
            hd = headers[table].get(tn) if tn else None
            if hd:
                doc_txt = f"{hd[0]} · {dref}" if (dref and hd[0]) else (hd[0] or dref)
                doc_date = doc_date or hd[1]
                who = who or hd[2]
        summary, touches_money, touches_account = _change_summary(s, vals.get(eid, []), names)
        old_edit = _is_old_edit(act, at, doc_date, s["old_days"])
        out.append({
            "event_id": eid, "at": str(at)[:19] if at else None, "table": table, "area": s_area,
            "action": act, "action_word": ACTION_WORD.get(act, act), "row_key": rk,
            "doc": doc_txt, "doc_date": str(doc_date)[:10] if doc_date else None, "user": who or "—",
            "pc": pc or None, "summary": summary, "fields": len(vals.get(eid, [])),
            "is_delete": act == "D", "is_old_edit": old_edit,
            "touches_amount": touches_money or touches_account, "touches_account": touches_account,
        })
    _mark_pairs(out, s["pair_minutes"])
    users = [r[0] for r in con.execute(
        "SELECT DISTINCT erp_user FROM erp_change_events WHERE erp_user IS NOT NULL AND TRIM(erp_user) <> '' ORDER BY 1")]
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
                       "material": _col_is(col, s["money"]) or _col_is(col, ACCOUNT_WORDS)})
    return {
        "event_id": ev["event_id"], "at": str(ev["changed_at"])[:19] if ev["changed_at"] else None,
        "table": ev["table_name"], "area": area_label(s, ev["table_name"]),
        "action": ev["action"], "action_word": ACTION_WORD.get(ev["action"], ev["action"]),
        "row_key": ev["row_key"], "doc": ev["doc_ref"], "doc_date": str(ev["doc_date"])[:10] if ev["doc_date"] else None,
        "user": ev["erp_user"] or "—", "pc": ev["pc_name"], "ip": ev["ip_addr"],
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
    rows = con.execute(f"SELECT table_name, action, erp_user, row_key FROM erp_change_events{wsql}", params).fetchall()
    headers, agg = {}, {}
    for table, act, usr, rk in rows:
        who = usr
        if not who and (table in s["parent"] or str(table).upper() in s["parent"]):   # a detail line: borrow the header user
            headers.setdefault(table, _header_index(con, s, table))
            tn = _rowkey_field(rk, "TRANS_NO")
            hd = headers[table].get(tn) if tn else None
            who = hd[2] if hd else None
        who = who or "—"
        a = agg.setdefault(who, {"user": who, "total": 0, "inserts": 0, "edits": 0, "deletes": 0})
        a["total"] += 1
        a["inserts" if act == "I" else "edits" if act == "U" else "deletes" if act == "D" else "total"] += 0 if act not in ("I", "U", "D") else 1
    return sorted(agg.values(), key=lambda r: -r["total"])
