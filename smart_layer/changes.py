"""
Change feed for the dashboard's "what changed" animations.

Recorded AT SYNC TIME, not from the ERP's own entry timestamps: on each LIVE sync we snapshot a few
fast-changing entities before the window is replaced, diff them against the freshly inserted rows, and write
what is new/changed to the `change_log` table with the server clock (one format). This is generic across ERPs
and does not depend on entered_at (which on some ERPs is NULL, date-only, or the keying time rather than the
arrival time) - so it works where a timestamp-based feed silently returns nothing.

/api/changes?since=<server-clock ts> then returns change_log rows newer than the client's last refresh.
"""

import json
from datetime import datetime, timedelta

# entities whose before/after we diff on a live sync (all are in store.LIVE_ENTITIES)
TRACKED = ("account_balances", "gate_passes", "loom_production", "store_issues")


def snapshot(con, entity):
    """Capture the current state of a tracked entity BEFORE the sync replaces it. Returns None for untracked
    entities (and when the table is empty, i.e. the very first sync, so the first load never floods the feed)."""
    if entity not in TRACKED:
        return None
    if entity == "account_balances":
        rows = con.execute("""SELECT b.account_code, b.balance FROM account_balances b JOIN accounts a USING (account_code)
                              WHERE a.account_group IN ('cash', 'bank')""").fetchall()
        return {"bal": {c: (v or 0) for c, v in rows}} if _has(con, "account_balances") else None
    if entity == "gate_passes":
        return {"ids": {r[0] for r in con.execute("SELECT DISTINCT gatepass_id FROM gate_passes")}} if _has(con, "gate_passes") else None
    if entity == "loom_production":
        rows = con.execute("SELECT loom_code || '|' || prod_date || '|' || COALESCE(shift,''), SUM(meters) FROM loom_production GROUP BY 1").fetchall()
        return {"m": {k: (v or 0) for k, v in rows}} if _has(con, "loom_production") else None
    if entity == "store_issues":
        return {"ids": {r[0] for r in con.execute("SELECT doc_no || '|' || item_code FROM store_issues")}} if _has(con, "store_issues") else None
    return None


def _has(con, table):
    return con.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None


def record(con, entity, before, now):
    """After the sync inserted the new rows, diff against `before` and append events to change_log (server clock
    `now`, one format). No-op when before is None (untracked or first sync)."""
    if not before:
        return
    events = []
    if entity == "account_balances":
        after = {c: (v or 0) for c, v in con.execute("""SELECT b.account_code, b.balance FROM account_balances b
                 JOIN accounts a USING (account_code) WHERE a.account_group IN ('cash','bank')""")}
        old = before["bal"]
        delta = round(sum(after.values()) - sum(old.values()))
        if abs(delta) >= 1:
            codes = set(after) | set(old)
            top = max(codes, key=lambda c: abs((after.get(c, 0)) - (old.get(c, 0))))
            name = con.execute("SELECT account_name FROM accounts WHERE account_code = ?", (top,)).fetchone()
            events.append({"kind": "cash", "delta": delta, "label": (name[0] if name else "")})
    elif entity == "store_issues":
        new = before["ids"]                               # keys present before this sync (doc_no|item_code)
        rows = con.execute("""SELECT s.doc_no || '|' || s.item_code, COALESCE(i.item_name, s.item_code), s.issued_to, s.qty
                FROM store_issues s LEFT JOIN items i ON i.item_code = s.item_code""").fetchall()
        for key, item, to, qty in rows:
            if key not in new:
                events.append({"kind": "store", "item": item, "to": to, "qty": round(qty or 0, 2)})
        events = events[:6]
    elif entity == "gate_passes":
        new = before["ids"]
        # aggregate ALL lines of a gate pass (a slip can have many), so the meters/thaan/bags are the slip total,
        # not just the first line. direction / type / party / number are the same on every line, so MAX picks them.
        for gid, direction, typ, party, gp, qty, thaan, bags in con.execute("""
                SELECT gatepass_id, MAX(direction), MAX(type), MAX(party_name), MAX(gp_no),
                       SUM(COALESCE(qty,0)), SUM(COALESCE(thaan,0)), SUM(COALESCE(bags,0)) FROM gate_passes
                GROUP BY gatepass_id"""):
            if gid in new:
                continue
            what = _goods_kind(typ)                       # from the whole type text, not its first word
            ev = {"kind": "out" if direction == "out" else "in", "what": what, "party": party, "doc_no": gp}
            if what == "fabric":
                ev["meters"] = round(qty or 0)
                if thaan:
                    ev["thaan"] = round(thaan)
            elif what == "yarn":
                ev["bags"] = round(bags) if bags else None
                if not bags and qty:
                    ev["kg"] = round(qty)
            else:                                         # beams / store goods / other
                ev["qty"] = round(qty) if qty else (round(bags) if bags else None)
            events.append(ev)
        events = events[:6]
    elif entity == "loom_production":
        old = before["m"]
        after = dict(con.execute("SELECT loom_code || '|' || prod_date || '|' || COALESCE(shift,''), SUM(meters) FROM loom_production GROUP BY 1"))
        gained = sum(max(0, (after.get(k, 0) or 0) - (old.get(k, 0) or 0)) for k in after)
        if round(gained) > 0:
            events.append({"kind": "woven", "meters": round(gained)})
    if events:
        con.executemany("INSERT INTO change_log (synced_at, entity, kind, payload) VALUES (?, ?, ?, ?)",
                        [(now, entity, e["kind"], json.dumps(e)) for e in events])


def _goods_kind(typ):
    """What the gate pass carries, from its type text (generic): fabric, yarn, beams, store goods, or goods."""
    t = str(typ or "").lower()
    if "fabric" in t:
        return "fabric"
    if "yarn" in t or "sizing" in t:
        return "yarn"
    if "beam" in t:
        return "beams"
    if "store" in t or "grn" in t:
        return "store goods"
    return "goods"


def prune(con, days=2):
    cutoff = (datetime.now() - timedelta(days=int(days))).isoformat(sep=" ", timespec="seconds")
    con.execute("DELETE FROM change_log WHERE synced_at < ?", (cutoff,))


def cursor(con):
    """The newest change_log id - the client stores this and passes it back as `since` on the next refresh."""
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM change_log").fetchone()[0]


def feed(con, cfg, since):
    """Events recorded since the client's last refresh, aggregated for the animations. `since` is the change_log
    id the client last saw (a monotonic cursor, so a refresh that lands mid-sync never skips rows). Returns
    {events, cursor}. An absent/blank `since` yields no events (first load never animates)."""
    cur = cursor(con)
    try:
        since_id = int(since)
    except (TypeError, ValueError):
        return {"events": [], "cursor": cur}             # no prior cursor -> nothing to animate yet
    rows = con.execute("SELECT kind, payload FROM change_log WHERE id > ? ORDER BY id", (since_id,)).fetchall()
    cash_delta, cash_label, woven = 0, "", 0
    store, out, inb = [], [], []
    for kind, payload in rows:
        try:
            e = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if kind == "cash":
            cash_delta += e.get("delta", 0)
            cash_label = e.get("label", cash_label)
        elif kind == "woven":
            woven += e.get("meters", 0)
        elif kind == "store":
            store.append(e)
        elif kind == "out":
            out.append(e)
        elif kind == "in":
            inb.append(e)
    events = []
    if abs(round(cash_delta)) >= 1:
        events.append({"kind": "cash", "delta": round(cash_delta), "label": cash_label, "currency": cfg["company"]["currency"]})
    if round(woven) > 0:
        events.append({"kind": "woven", "meters": round(woven)})
    events += out[:4] + inb[:4] + store[:6]
    return {"events": events, "cursor": cur}
