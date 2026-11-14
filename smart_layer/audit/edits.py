"""
Entries changed after they were saved (generalised from Dost Textiles' 26_prod_entry_snapshot_diff and the
issue-value check in 70_erp_audit).

Each daily audit keeps a snapshot of recent issue slips (quantity and value per slip and item) and production
entries (meters per day, loom, shift, quality). The next day every change is reported:
  - an issue quantity changed on an old slip                 CONFIRMED (someone edited the slip)
  - an issue value changed, explained by a late receipt      WATCH (the ERP re-rated at the new average)
  - an issue value changed, line was saved unrated (0)       INFO  (the ERP filled in the rate - normal)
  - an issue value changed, the edit history shows only the
    average-rate ROUTINE (system module) touched rates       INFO  (re-rated by the routine - normal)
  - an issue value changed, a person edited a rate, or no
    history explains it and the old value was non-zero       ACTION (rate edited by hand? check)
  - a slip or production entry disappeared                   WATCH
  - production meters changed for a day older than 3 days    WATCH
Only ACTION / CONFIRMED are phone-pushed, so the benign ERP re-ratings (INFO) no longer wake the owner. The
system-module names are generic ([edits].system_modules). The first run only takes the snapshot.
"""

from datetime import date, datetime, timedelta

from ..store import get_state, set_state


def _rerate_signals(a, since, now):
    """Read the ERP edit history (when the optional change-history entities are mapped) for the window since the last
    snapshot and report, for amount/rate columns only, whether the ERP's own average-rate ROUTINE re-rated lines and
    whether a PERSON edited a rate/amount. Returns (system_rerate, human_rate_edit); (False, False) when there is no
    history to read. Generic: the routine's module names and the 'system column' come from [edits] config."""
    from .. import erp_edits
    try:
        if not erp_edits.installed(a.con):
            return (False, False)
        s = erp_edits._settings(a.cfg)
        money = s["money"]
        events = {}                                      # event_id -> {"module", "action", "cols"[]}
        for eid, mod, act, col in a.q(
                "SELECT e.event_id, e.module, e.action, v.column_name FROM erp_change_events e "
                "JOIN erp_change_values v ON v.event_id = e.event_id WHERE e.changed_at > ? AND e.changed_at <= ?",
                since, now):
            ev = events.setdefault(eid, {"module": mod, "action": act, "cols": []})
            ev["cols"].append(col)
        system_rerate = human_rate_edit = False
        for ev in events.values():
            if not any(erp_edits._col_is(c, money) for c in ev["cols"]):
                continue                                 # this edit did not touch a rate / amount column
            if erp_edits._is_system(ev["module"], ev["action"], ev["cols"], s):
                system_rerate = True
            else:
                human_rate_edit = True
        return (system_rerate, human_rate_edit)
    except Exception:
        return (False, False)                            # never let the audit fall over on a history-read problem


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
    # ONLY the daily audit updates the baseline, and only when auditing a day AT OR AFTER the baseline's day - so
    # re-running an audit for an OLD date (or a dashboard load / the 5-minute urgent check) never moves the baseline
    # backwards and makes the next daily audit report everything since as "deleted".
    prev_day = (old or {}).get("taken", "")[:10]
    if a.persist and a.window[1] >= (prev_day or a.window[1]):
        set_state(a.con, "edit_snapshot", {"taken": now, "issues": issues, "prod": prod})
    if not old:
        return
    since = old.get("taken", now)
    system_rerate, human_rate_edit = _rerate_signals(a, since, now)   # read once for the whole window
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
            elif amt <= 0.01:
                # the line was saved unrated (rate 0) and the ERP's average-rate routine filled it in later - normal,
                # not a hand edit. INFO is listed in the report but never phone-pushed.
                a.add("INFO", "Edits", f"isfill-{key}-{round(new[3])}", f"Issue slip {doc} ({d}): {name} rate filled in "
                      f"{cur} 0 -> {cur} {new[3]:,.0f}", "saved unrated; the ERP filled in the average rate (normal)")
            elif human_rate_edit:
                # a person edited a rate/amount somewhere in this window - stay cautious
                a.add("ACTION", "Edits", f"isval-{key}-{round(new[3])}", f"Issue slip {doc} ({d}): {name} value changed "
                      f"{cur} {amt:,.0f} -> {cur} {new[3]:,.0f} with no late receipt to explain it",
                      "rate edited by hand? check with the store / accountant")
            elif system_rerate:
                # the edit history shows only the ERP's average-rate routine touched rates in this window
                a.add("INFO", "Edits", f"isrerate2-{key}-{round(new[3])}", f"Issue slip {doc} ({d}): {name} re-valued "
                      f"{cur} {amt:,.0f} -> {cur} {new[3]:,.0f}", "re-rated by the ERP's average-rate routine (normal)")
            else:
                # no history to explain it and the old value was non-zero - worth a look
                a.add("ACTION", "Edits", f"isval-{key}-{round(new[3])}", f"Issue slip {doc} ({d}): {name} value changed "
                      f"{cur} {amt:,.0f} -> {cur} {new[3]:,.0f} with no late receipt to explain it",
                      "rate edited by hand? check with the store / accountant")
    taken = since[:10]

    def by_day_loom(p):                                # the ERP re-keys production across shift/quality as it finalises,
        out = {}                                       # so compare at day+loom level - otherwise every reshuffle looks
        for key, (d, name, m) in p.items():            # like a "deleted" row (~20-28 false findings a day).
            loom = key.split("|")[1]
            k = (d, loom)
            if k in out:
                out[k][1] += m or 0
            else:
                out[k] = [name, m or 0]
        return out

    oldp, newp = by_day_loom(old.get("prod", {})), by_day_loom(prod)
    window_from = (date.fromisoformat(a.window[1]) - timedelta(days=60)).isoformat()   # we only re-read 60 days of production
    for (d, loom), (name, m) in oldp.items():
        if d < window_from:
            continue                                  # older than the window we re-read - absence is not a deletion
        if (datetime.fromisoformat(taken) - datetime.fromisoformat(d)).days <= 3:
            continue                                  # recent days are still being keyed
        new = newp.get((d, loom))
        if new is None:
            a.add("WATCH", "Edits", f"proddel-{d}-{loom}", f"{name} production of {d} ({m:,.0f} m) is no longer in the ERP",
                  "deleted after it was entered")
        elif abs(new[1] - m) >= 5:                    # a few meters of rounding on re-batching is not an edit
            a.add("WATCH", "Edits", f"prodchg-{d}-{loom}-{round(new[1])}", f"{name} production of {d} changed {m:,.0f} -> {new[1]:,.0f} m",
                  "changed days after it was entered - a correction, or an edit to check")
