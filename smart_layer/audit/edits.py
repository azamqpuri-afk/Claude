"""
Entries changed after they were saved (generalised from Dost Textiles' 26_prod_entry_snapshot_diff and the
issue-value check in 70_erp_audit).

Each daily audit keeps a snapshot of recent issue slips (quantity and value per slip and item) and production
entries (meters per day, loom, shift, quality). The next day every change is reported:
  - an issue quantity changed on an old slip                 CONFIRMED (someone edited the slip)
  - an issue value changed and no receipt of that item was
    keyed since that could explain it (re-rating at the
    store's average rate after a late GRN is normal)       ACTION, or WATCH when a late receipt explains it
  - a slip or production entry disappeared                   WATCH
  - production meters changed for a day older than 3 days    WATCH
The first run only takes the snapshot.
"""

from datetime import datetime

from ..store import get_state, set_state


def run(a):
    if a.d0 != a.d1:                                 # daily audit only
        return
    cur = a.cur
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    issues = {f"{doc}|{item}": [d, name, qty or 0, round(amt or 0, 2)] for doc, item, d, name, qty, amt in a.q("""
        SELECT s.doc_no, s.item_code, MIN(s.doc_date), COALESCE(MAX(i.item_name), s.item_code), SUM(s.qty), SUM(s.amount)
        FROM store_issues s LEFT JOIN items i ON i.item_code = s.item_code
        WHERE s.doc_date >= date(?, '-180 days') GROUP BY s.doc_no, s.item_code""", a.window[1])}
    prod = {f"{d}|{loom}|{shift}|{q}": [d, name, m or 0] for d, loom, name, shift, q, m in a.q("""
        SELECT p.prod_date, p.loom_code, COALESCE(MAX(l.loom_name), p.loom_code), COALESCE(p.shift, '-'), COALESCE(p.quality, '-'),
               SUM(p.meters)
        FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
        WHERE p.prod_date >= date(?, '-60 days') GROUP BY p.prod_date, p.loom_code, p.shift, p.quality""", a.window[1])}
    old = get_state(a.con, "edit_snapshot")
    set_state(a.con, "edit_snapshot", {"taken": now, "issues": issues, "prod": prod})
    if not old:
        return
    since = old.get("taken", now)
    old_from = min((v[0] for v in old.get("issues", {}).values()), default=None)
    for key, (d, name, qty, amt) in old.get("issues", {}).items():
        doc, item = key.split("|", 1)
        new = issues.get(key)
        if new is None:
            if old_from and d >= old_from and d >= min((v[0] for v in issues.values()), default=d):
                a.add("WATCH", "Edits", f"isdel-{key}", f"Issue slip {doc} ({d}): {name} x{qty:g} is no longer in the ERP",
                      "deleted after it was saved - check with the store why")
            continue
        if abs(new[2] - qty) > 1e-6:
            a.add("CONFIRMED", "Edits", f"isqty-{key}-{new[2]:g}", f"Issue slip {doc} ({d}): {name} quantity changed {qty:g} -> {new[2]:g}",
                  "an old issue slip was edited after it was saved - check with the storekeeper why")
        elif abs(new[3] - amt) >= 1000:
            late = a.q("""SELECT doc_no, doc_date FROM store_receipts WHERE item_code = ? AND entered_at > ? AND doc_date <= ? LIMIT 1""",
                       item, since, d)
            if late:
                if abs(new[3] - amt) >= 5000:
                    a.add("WATCH", "Edits", f"isrerate-{key}-{round(new[3])}", f"Issue slip {doc} ({d}): {name} re-valued "
                          f"{cur} {amt:,.0f} -> {cur} {new[3]:,.0f}", f"receipt {late[0][0]} dated {late[0][1]} was keyed late - "
                          f"the ERP re-rated earlier issues at the new average rate (normal)")
            else:
                a.add("ACTION", "Edits", f"isval-{key}-{round(new[3])}", f"Issue slip {doc} ({d}): {name} value changed "
                      f"{cur} {amt:,.0f} -> {cur} {new[3]:,.0f} with no late receipt to explain it",
                      "rate edited by hand? check with the store / accountant")
    taken = since[:10]
    for key, (d, name, m) in old.get("prod", {}).items():
        if (datetime.fromisoformat(taken) - datetime.fromisoformat(d)).days <= 3:
            continue                                  # recent days are still being keyed
        new = prod.get(key)
        if new is None:
            a.add("WATCH", "Edits", f"proddel-{key}", f"{name} production of {d} ({m:,.0f} m) is no longer in the ERP",
                  "deleted after it was entered")
        elif abs(new[2] - m) >= 1:
            a.add("WATCH", "Edits", f"prodchg-{key}-{round(new[2])}", f"{name} production of {d} changed {m:,.0f} -> {new[2]:,.0f} m",
                  "changed days after it was entered - a correction, or an edit to check")
