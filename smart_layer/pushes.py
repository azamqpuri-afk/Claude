"""
Phone pushes driven by the sync and the clock (0.15). These replace the old hourly production follow-up.

  notify_changes   on each live sync: push NEW production (once a day, ERP metres next to the same day's
                   scoreboard eff/m/RPM) and each NEW outward fabric gate pass (OGP no, party, metres).
  morning_summary  ~06:20: scoreboard vs ERP production for the day, fabric out and fabric stock.
  night_sensor     ~06:20: the night-shift (18:00-06:00) sensor duplicate-timings result (all clear / pairs).
  weft_check       scoreboard weft weight vs ERP weft issued for a date, once the ERP entries are keyed.

Quiet hours ([notify] quiet_from/quiet_to, 0-23) suppress these non-urgent pushes; nothing is lost - the change
cursor is not advanced while quiet, so the next run outside the window sends the accumulated items.
"""

import json
from datetime import date, datetime, timedelta

from . import notify, store


def _lnum(code):
    """The loom number inside a loom code: '10.0' -> 10 (monitor floats), 'L07' -> 7, '12' -> 12. None if none."""
    s = str(code or "").strip()
    try:
        return int(float(s))
    except ValueError:
        import re
        m = re.findall(r"\d+", s)
        return int(m[-1]) if m else None


def _quiet(cfg, now=None):
    n = cfg["notify"]
    a, b = n.get("quiet_from"), n.get("quiet_to")
    if a is None or b is None:
        return False
    h = (now or datetime.now()).hour
    return (a <= h < b) if a <= b else (h >= a or h < b)     # handles a window crossing midnight


def notify_changes(cfg, con):
    """Push new production (each production DATE, once, with that date's own scoreboard figures) and each new OGP
    fabric gate pass, from the change_log. Quiet-hours aware.

    On the FIRST run after an upgrade (no push_cursor yet) the cursor is seeded to the current MAX(id) and nothing
    is pushed, so the backlog of change_log rows that already existed is not sent in one burst."""
    if _quiet(cfg):
        return 0
    first_run = store.get_state(con, "push_cursor", None) is None
    cursor = store.get_state(con, "push_cursor", 0) or 0
    rows = con.execute("SELECT id, kind, payload FROM change_log WHERE id > ? ORDER BY id", (cursor,)).fetchall()
    if not rows:
        if first_run:                                     # nothing yet, but record that we've started
            store.set_state(con, "push_cursor", 0)
        return 0
    maxid = rows[-1][0]
    if first_run:                                         # seed the cursor past the pre-upgrade backlog, push nothing
        store.set_state(con, "push_cursor", maxid)
        return 0
    ogp_done = set(store.get_state(con, "ogp_pushed", []) or [])
    prod_done = set(store.get_state(con, "prod_pushed", []) or [])
    sent = 0
    prod_dates = set()                                    # production DATEs that gained meters this batch
    for _id, kind, payload in rows:
        try:
            e = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if kind == "woven":
            # a woven event carries the dates that gained meters (0.15.1); fall back to today for old rows.
            prod_dates.update((e.get("dates") or {}).keys() or {date.today().isoformat()})
        elif kind == "out" and e.get("what") == "fabric":
            key = str(e.get("gatepass_id") or e.get("doc_no") or "")   # dedupe on the unique gatepass_id
            doc = str(e.get("doc_no") or e.get("gatepass_id") or "?")
            if key and key not in ogp_done:
                m = e.get("meters")
                notify.send(cfg, f"Fabric out · OGP {doc}",
                            f"OGP {doc}: {m:,} m → {e.get('party') or '?'}" if m else f"OGP {doc} → {e.get('party') or '?'}",
                            tags="truck")
                ogp_done.add(key); sent += 1
    # one production push per NEW production date, with that date's own ERP vs scoreboard figures
    for ds in sorted(prod_dates):
        if ds in prod_done:
            continue
        try:
            d = date.fromisoformat(ds)
        except (ValueError, TypeError):
            continue
        notify.send(cfg, f"Production entered {d:%d-%b}", _production_line(con, d, cfg), tags="bar_chart")
        prod_done.add(ds); sent += 1
    store.set_state(con, "ogp_pushed", sorted(ogp_done)[-5000:])
    store.set_state(con, "prod_pushed", sorted(prod_done)[-400:])
    store.set_state(con, "push_cursor", maxid)
    return sent


def _production_line(con, d, cfg=None):
    """'ERP production <date>: <m> m, eff X% | scoreboard eff Y% · <m> m · RPM Z' for one date. The shed scoreboard
    efficiency leaves out [monitor] exclude_looms, matching the Overview tile and the Weaving tab."""
    ds = d.isoformat()
    em, = con.execute("SELECT SUM(meters) FROM loom_production WHERE prod_date = ?", (ds,)).fetchone()
    from .reports.production import EFF_AGG
    eeff = con.execute(f"SELECT {EFF_AGG} FROM loom_production WHERE prod_date = ?", (ds,)).fetchone()[0]
    excl = {_lnum(x) for x in (cfg["monitor"].get("exclude_looms") or [])} if cfg else set()
    excl_codes = [c for (c,) in con.execute("SELECT DISTINCT loom_code FROM loom_monitor") if _lnum(c) in excl] if excl else []
    ex = (" AND loom_code NOT IN (%s)" % ",".join("?" * len(excl_codes))) if excl_codes else ""
    sm, seff, srpm = con.execute(
        f"""SELECT SUM(meters), SUM(efficiency_pct*COALESCE(shift_sec,meters))/NULLIF(SUM(COALESCE(shift_sec,meters)),0), AVG(rpm)
           FROM loom_monitor WHERE read_date = ?{ex}""", [ds, *excl_codes]).fetchone()
    erp = f"ERP {em:,.0f} m" + (f", eff {eeff:.0f}%" if eeff is not None else "") if em else "ERP: not keyed yet"
    scb = (f"scoreboard eff {seff:.0f}% · {sm:,.0f} m" + (f" · {srpm:.0f} rpm" if srpm else "")) if sm else "no scoreboard data"
    return f"{d:%d-%b}: {erp} | {scb}"


def edit_pushes(cfg, con):
    """Push ERP edits that matter (item 31): every DELETE, and every EDIT of a document older than
    [edits] push_older_than_days, grouped into ONE push per sync. Cursor-based on event_id (like notify_changes),
    so each event is considered once; the first run after an upgrade seeds the cursor and pushes nothing. Quiet-hours
    aware. A mill without the change-history layer has no events, so this is a no-op."""
    if _quiet(cfg):
        return 0
    if not con.execute("SELECT 1 FROM sync_log WHERE entity = 'erp_change_events' LIMIT 1").fetchone():
        return 0                                           # the edit-history layer is not installed here
    first_run = store.get_state(con, "edit_push_cursor", None) is None
    cursor = store.get_state(con, "edit_push_cursor", 0) or 0
    rows = con.execute("""SELECT event_id, changed_at, table_name, action, row_key, doc_ref, doc_date, erp_user,
                                 pc_name, client_host, os_user, module
                          FROM erp_change_events WHERE event_id > ? ORDER BY event_id""", (cursor,)).fetchall()
    if not rows:
        if first_run:
            store.set_state(con, "edit_push_cursor", 0)
        return 0
    maxid = rows[-1][0]
    if first_run:                                          # seed past the pre-upgrade backlog, push nothing
        store.set_state(con, "edit_push_cursor", maxid)
        return 0
    from . import erp_edits
    s = erp_edits._settings(cfg)
    older = int(cfg["edits"].get("push_older_than_days", 2) or 0)
    headers = {}                                           # parent table -> TRANS_NO -> (doc_ref, doc_date, user)
    seen, flagged = set(), []                              # collapse a voucher's header + its line edits into one item
    for eid, at, table, act, rk, dref, ddt, usr, pc, chost, osuser, module in rows:
        # skip the ERP's own routine postings (e.g. AVG_RATE re-rating) - grouped in the tab, never pushed (item 5)
        if str(module or "").strip().upper() in s["system_modules"]:
            continue
        if act == "U":
            cols = [c for (c,) in con.execute("SELECT column_name FROM erp_change_values WHERE event_id = ?", (eid,))]
            if erp_edits._is_system(module, act, cols, s):
                continue
        is_del = act == "D"
        area, doc, who, pcn, ddoc = erp_edits.area_label(s, table), dref or rk or "?", usr, pc, ddt
        parent = s["parent"].get(table) or s["parent"].get(str(table).upper())
        if parent:                                         # a detail line: name it by its header document/date/user
            headers.setdefault(table, erp_edits._header_index(con, s, table))
            tn = erp_edits._rowkey_field(rk, "TRANS_NO")
            hd = headers[table].get(tn) if tn else None
            if hd:
                area, doc = erp_edits.area_label(s, parent), hd["doc"] or doc
                ddoc = ddoc or hd["date"]; who = who or hd["user"]; pcn = pcn or hd["pc"]
        old_edit = act == "U" and erp_edits._is_old_edit(act, at, ddoc, older)
        if not (is_del or old_edit):
            continue
        who = erp_edits._display_user(who, pcn, chost, osuser, s["generic"])
        ddt = ddoc
        key = (is_del, doc, who or "")
        if key in seen:                                    # same document already listed (header + its lines)
            continue
        seen.add(key)
        flagged.append({"area": area, "del": is_del, "doc": doc,
                        "date": str(ddt)[:10] if ddt else None, "user": who or "?"})
    store.set_state(con, "edit_push_cursor", maxid)        # advance past everything we have now considered
    if not flagged:
        return 0
    ndel = sum(1 for f in flagged if f["del"])
    nold = len(flagged) - ndel
    bits = ([f"{ndel} delete{'s' if ndel != 1 else ''}"] if ndel else []) + \
           ([f"{nold} old-entry edit{'s' if nold != 1 else ''}"] if nold else [])
    title = "ERP edits: " + ", ".join(bits)
    lines = []
    for f in flagged[:10]:
        what = "Deleted" if f["del"] else "Edited"
        when = f" ({f['date']})" if f["date"] else ""
        lines.append(f"• {what} {f['area']} {f['doc']}{when} by {f['user']}")
    if len(flagged) > 10:
        lines.append(f"… and {len(flagged) - 10} more")
    notify.send(cfg, title, "\n".join(lines), tags=("warning" if ndel else "pencil"),
                priority=("high" if ndel else "default"))
    return 1


def _edits_by_user_line(con, d, cfg=None):
    """One line summarising the day's edits per ERP user, for the morning summary. '' when nothing / not installed."""
    from . import erp_edits
    try:
        rows = erp_edits.by_user(con, d, d, cfg)
    except Exception:
        return ""
    if not rows:
        return ""
    parts = []
    for r in rows[:6]:
        extra = f", {r['deletes']} del" if r["deletes"] else ""
        parts.append(f"{r['user']} {r['total']}{extra}")
    return "ERP edits today: " + "; ".join(parts)


def morning_summary(cfg, con, d=None):
    """Push the morning line for date d (default yesterday): scoreboard vs ERP production, fabric out, fabric stock.
    If the ERP day is not keyed, say so and show the last keyed day."""
    d = d or (date.today() - timedelta(days=1))
    line = _production_line(con, d, cfg)
    keyed = con.execute("SELECT MAX(prod_date) FROM loom_production").fetchone()[0]
    if not con.execute("SELECT 1 FROM loom_production WHERE prod_date = ? LIMIT 1", (d.isoformat(),)).fetchone():
        line += f"\n(ERP not keyed for {d:%d-%b}; last keyed day {keyed})"
    out_m = con.execute("""SELECT SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters) FROM fabric_dispatches
                           WHERE dispatch_date = ?""", (d.isoformat(),)).fetchone()[0] or 0
    stock = con.execute("SELECT SUM(meters) FROM fabric_stock").fetchone()[0] or 0
    body = f"{line}\nFabric out {out_m:,.0f} m · fabric in stock {stock:,.0f} m"
    edits_line = _edits_by_user_line(con, d, cfg)          # item 31: one "edits by user" line when the history layer is on
    if edits_line:
        body += f"\n{edits_line}"
    notify.send(cfg, f"Morning summary {d:%d-%b}", body, tags="sunrise")
    return body


def night_sensor(cfg, con, d=None):
    """Push the night-shift sensor duplicate-timings result for the night that started on date d (default
    yesterday). Reuses the generic twin-episode logic over the last two days of the stop log."""
    from .audit import sensor
    d = d or (date.today() - timedelta(days=1))
    if not con.execute("SELECT 1 FROM loom_stops LIMIT 1").fetchone():
        return None
    t = sensor.settings(cfg)
    since = (d - timedelta(days=1)).isoformat()
    rows = con.execute("""SELECT loom_code, stop_time, restart_time FROM loom_stops
                          WHERE stop_date >= ? AND stop_time IS NOT NULL AND restart_time IS NOT NULL ORDER BY stop_time""",
                       (since,)).fetchall()
    # night window 18:00 d -> 06:00 d+1
    lo, hi = f"{d.isoformat()} 18:00:00", f"{(d + timedelta(days=1)).isoformat()} 06:00:00"
    night = [r for r in rows if lo <= str(r[1])[:19] <= hi]
    eps = [e for e in sensor.episodes(night, t) if e["twins"] >= 3]
    conf = [e for e in eps if e["confirmed"]]
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    nm = lambda c: names.get(c) or names.get(str(sensor._loom_num(c))) or f"loom {c}"   # noqa: E731
    if conf:
        body = "Likely sensor move(s):\n" + "\n".join(
            f"• {nm(e['a'])} & {nm(e['b'])}: {e['twins']} same-second stops ({e['share']:.0%})" for e in conf[:6])
        title = f"Night sensor {d:%d-%b}: {len(conf)} likely"
    elif eps:
        body = "Possible (below threshold):\n" + "\n".join(f"• {nm(e['a'])} & {nm(e['b'])}: {e['twins']}" for e in eps[:6])
        title = f"Night sensor {d:%d-%b}: {len(eps)} possible"
    else:
        body, title = "All clear - no two looms stopped together on the night shift.", f"Night sensor {d:%d-%b}: all clear"
    notify.send(cfg, title, body, tags=("warning" if conf else "white_check_mark"))
    return body


def weft_check(cfg, con, d=None):
    """Scoreboard weft weight vs ERP weft issued for a date, pushed once the ERP entries are keyed. Compares total
    WEIGHT (kg), since the two systems may name counts differently; flags a big difference (>10%)."""
    d = d or (date.today() - timedelta(days=1))
    ds = d.isoformat()
    if not con.execute("SELECT 1 FROM loom_production WHERE prod_date = ? LIMIT 1", (ds,)).fetchone():
        return None                                         # ERP not keyed yet - don't push
    from .textile.method import Method
    method = Method(cfg["closing"])
    scb_kg = 0.0
    meta = {c: (q, ppi, w, fc) for c, q, ppi, w, fc in con.execute(
        "SELECT contract_no, quality, picks_per_inch, width_in, weft_count FROM contracts")}
    for cno, m in con.execute("SELECT contract_no, SUM(meters) FROM loom_production WHERE prod_date = ? GROUP BY contract_no", (ds,)):
        q, ppi, w, fc = meta.get(cno, (None, None, None, None))
        if ppi and w and fc:
            lbm = method.weight("weft", method.settings(cno, None), ppi, w, fc)
            if lbm:
                scb_kg += (m or 0) * lbm / 2.2046
    issued = con.execute("SELECT SUM(kg) FROM yarn_movements WHERE move_type='to_looms' AND move_date = ?", (ds,)).fetchone()[0] or 0
    diff = issued - scb_kg
    pct = (diff / scb_kg * 100) if scb_kg else 0
    flag = abs(pct) > 10
    body = f"{d:%d-%b}: expected weft {scb_kg:,.0f} kg (from production) vs issued {issued:,.0f} kg ({diff:+,.0f} kg, {pct:+.0f}%)."
    notify.send(cfg, f"Weft check {d:%d-%b}" + (" - check" if flag else ""), body, tags=("warning" if flag else "scales"),
                priority=("high" if flag else "default"))
    return body
