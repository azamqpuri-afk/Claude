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


def _quiet(cfg, now=None):
    n = cfg["notify"]
    a, b = n.get("quiet_from"), n.get("quiet_to")
    if a is None or b is None:
        return False
    h = (now or datetime.now()).hour
    return (a <= h < b) if a <= b else (h >= a or h < b)     # handles a window crossing midnight


def notify_changes(cfg, con):
    """Push new production (once/day) and each new OGP fabric gate pass, from the change_log. Quiet-hours aware."""
    if _quiet(cfg):
        return 0
    cursor = store.get_state(con, "push_cursor", 0) or 0
    rows = con.execute("SELECT id, kind, payload FROM change_log WHERE id > ? ORDER BY id", (cursor,)).fetchall()
    if not rows:
        return 0
    maxid = rows[-1][0]
    ogp_done = set(store.get_state(con, "ogp_pushed", []) or [])
    prod_done = set(store.get_state(con, "prod_pushed", []) or [])
    sent = 0
    saw_woven = False
    for _id, kind, payload in rows:
        try:
            e = json.loads(payload)
        except (ValueError, TypeError):
            continue
        if kind == "woven":
            saw_woven = True
        elif kind == "out" and e.get("what") == "fabric":
            doc = str(e.get("doc_no") or "")
            if doc and doc not in ogp_done:
                m = e.get("meters")
                notify.send(cfg, f"Fabric out · OGP {doc}",
                            f"OGP {doc}: {m:,} m → {e.get('party') or '?'}" if m else f"OGP {doc} → {e.get('party') or '?'}",
                            tags="truck")
                ogp_done.add(doc); sent += 1
    today = date.today().isoformat()
    if saw_woven and today not in prod_done:
        notify.send(cfg, f"Production entered {date.today():%d-%b}", _production_line(con, date.today()), tags="bar_chart")
        prod_done.add(today); sent += 1
    store.set_state(con, "ogp_pushed", sorted(ogp_done)[-5000:])
    store.set_state(con, "prod_pushed", sorted(prod_done)[-400:])
    store.set_state(con, "push_cursor", maxid)
    return sent


def _production_line(con, d):
    """'ERP production <date>: <m> m, eff X% | scoreboard eff Y% · <m> m · RPM Z' for one date."""
    ds = d.isoformat()
    em, = con.execute("SELECT SUM(meters) FROM loom_production WHERE prod_date = ?", (ds,)).fetchone()
    from .reports.production import EFF_AGG
    eeff = con.execute(f"SELECT {EFF_AGG} FROM loom_production WHERE prod_date = ?", (ds,)).fetchone()[0]
    sm, seff, srpm = con.execute(
        """SELECT SUM(meters), SUM(efficiency_pct*COALESCE(shift_sec,meters))/NULLIF(SUM(COALESCE(shift_sec,meters)),0), AVG(rpm)
           FROM loom_monitor WHERE read_date = ?""", (ds,)).fetchone()
    erp = f"ERP {em:,.0f} m" + (f", eff {eeff:.0f}%" if eeff is not None else "") if em else "ERP: not keyed yet"
    scb = (f"scoreboard eff {seff:.0f}% · {sm:,.0f} m" + (f" · {srpm:.0f} rpm" if srpm else "")) if sm else "no scoreboard data"
    return f"{d:%d-%b}: {erp} | {scb}"


def morning_summary(cfg, con, d=None):
    """Push the morning line for date d (default yesterday): scoreboard vs ERP production, fabric out, fabric stock.
    If the ERP day is not keyed, say so and show the last keyed day."""
    d = d or (date.today() - timedelta(days=1))
    line = _production_line(con, d)
    keyed = con.execute("SELECT MAX(prod_date) FROM loom_production").fetchone()[0]
    if not con.execute("SELECT 1 FROM loom_production WHERE prod_date = ? LIMIT 1", (d.isoformat(),)).fetchone():
        line += f"\n(ERP not keyed for {d:%d-%b}; last keyed day {keyed})"
    out_m = con.execute("""SELECT SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters) FROM fabric_dispatches
                           WHERE dispatch_date = ?""", (d.isoformat(),)).fetchone()[0] or 0
    stock = con.execute("SELECT SUM(meters) FROM fabric_stock").fetchone()[0] or 0
    body = f"{line}\nFabric out {out_m:,.0f} m · fabric in stock {stock:,.0f} m"
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
