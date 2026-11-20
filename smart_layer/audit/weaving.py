"""Weaving checks on loom production."""

import re
from datetime import date, timedelta

from ..reports.production import EFF_AGG, EFF_SQL


def _lnum(code):
    """The loom number inside a loom code: '18.0' -> 18 (monitor floats), 'L07' -> 7, '12' -> 12; None if none.
    Lets the ERP loom code and the scoreboard's (often float) loom code be matched to the same physical loom."""
    s = str(code or "").strip()
    try:
        return int(float(s))
    except ValueError:
        m = re.findall(r"\d+", s)
        return int(m[-1]) if m else None


def run(a):
    d0, d1 = a.window
    t = a.t
    scb = bool(a.q("SELECT 1 FROM loom_monitor LIMIT 1"))     # is a loom scoreboard connected?
    qc_days, lag_days = int(t.get("quality_change_days", 2)), int(t.get("entry_lag_days", 4))

    def _ppi(cno):
        r = a.q("SELECT picks_per_inch FROM contracts WHERE contract_no = ?", cno)
        return r[0][0] if r and r[0][0] is not None else None

    def _quality(cno):
        r = a.q("SELECT quality FROM contracts WHERE contract_no = ?", cno)
        return r[0][0] if r and r[0][0] is not None else None

    def _dom_contract(code, d_from, d_to):               # the loom's main contract in a window (most meters)
        r = a.q("""SELECT contract_no, SUM(meters) FROM loom_production WHERE loom_code = ? AND prod_date > ?
                   AND prod_date <= ? AND contract_no IS NOT NULL GROUP BY contract_no ORDER BY 2 DESC LIMIT 1""",
                code, d_from, d_to)
        return r[0][0] if r else None

    # efficiency over 100% - only in the DAILY audit (a.persist), and only on days that are fully keyed (prod_date on
    # or before yesterday). A live / mid-entry shift often shows a transient >100% that settles once the other shift is
    # keyed (e.g. a 306 m entry at 103% that reads 96% once the 358 m second shift lands), so it must NEVER fire from
    # the live/urgent run. WATCH the first daily sighting (prod_date = yesterday); CONFIRMED only if it is STILL over
    # 100% a day later (prod_date older than yesterday), re-read fresh each daily run so a corrected entry drops out.
    if a.persist:
        yday = (date.fromisoformat(str(d1)) - timedelta(days=1)).isoformat()
        for pd, loom, eff, m in a.q(f"""SELECT p.prod_date, COALESCE(l.loom_name, p.loom_code), {EFF_SQL}, p.meters
                FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
                WHERE p.prod_date BETWEEN ? AND ? AND {EFF_SQL} > 100""", d0, yday):
            sev = "WATCH" if str(pd) >= yday else "CONFIRMED"
            a.add(sev, "Weaving", f"eff100-{loom}-{pd}",
                  f"{loom} {pd}: efficiency {eff:.0f}% (over 100%) on {m:,.0f} m",
                  "meters, shift length or RPM keyed wrong" + ("" if sev == "WATCH" else " - still over 100% a day later"))

    # production without a beam - only for ERPs that record beams on production
    with_beam, rows = a.q("""SELECT SUM(beam_no IS NOT NULL), COUNT(*) FROM loom_production WHERE prod_date >= date(?, '-120 days')""", d1)[0]
    if rows and with_beam and with_beam > 0.5 * rows:
        for pd, loom, cn, m in a.q("""SELECT p.prod_date, COALESCE(l.loom_name, p.loom_code), p.contract_no, p.meters
                FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
                WHERE p.prod_date BETWEEN ? AND ? AND p.beam_no IS NULL""", d0, d1):
            a.add("CONFIRMED", "Weaving", f"nobeam-{loom}-{pd}", f"{loom} {pd}: {m:,.0f} m of contract {cn or '?'} entered without a beam")

    # high rejection per loom and contract
    for loom, cn, rej, tot in a.q("""SELECT COALESCE(l.loom_name, p.loom_code), p.contract_no, SUM(COALESCE(p.rejected_m, 0)), SUM(p.meters)
            FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code WHERE p.prod_date BETWEEN ? AND ?
            GROUP BY p.loom_code, p.contract_no HAVING SUM(p.meters) > ? AND SUM(COALESCE(p.rejected_m, 0)) > ? * SUM(p.meters) / 100.0""",
            d0, d1, float(t["rejection_min_meters"]), float(t["rejection_pct"])):
        a.add("WATCH", "Weaving", f"rej-{loom}-{cn}", f"{loom} (contract {cn or '?'}): rejection {100 * rej / tot:.0f}% of {tot:,.0f} m")

    # looms that wove recently but have had nothing entered for longer than usual. A new beam / quality change is a
    # normal reason for a gap, so before alerting we check for one and give it a grace period; otherwise we use the
    # scoreboard to tell an ENTRY problem (it ran but nothing was keyed) from a LOOM problem (it is stopped).
    gap90 = a.profile.get("loom_gap_p90", {})
    end = date.fromisoformat(d1)
    for code, loom, last in a.q("""SELECT p.loom_code, COALESCE(MAX(l.loom_name), p.loom_code), MAX(p.prod_date)
            FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
            WHERE p.prod_date BETWEEN date(?, '-30 days') AND ? GROUP BY p.loom_code""", d1, d1):
        idle = (end - date.fromisoformat(last)).days
        usual = gap90.get(code) or 3
        if idle < max(int(t["idle_loom_min_days"]), usual + 2):
            continue
        qc = a.q("SELECT MIN(mounted_date) FROM beams WHERE loom_code = ? AND mounted_date > ? AND mounted_date <= ?", code, last, d1)
        qcd = qc[0][0] if qc and qc[0][0] else None
        if qcd:                                            # a new beam was mounted after the last entry
            expect = date.fromisoformat(qcd) + timedelta(days=qc_days + lag_days)
            if end <= expect:
                a.add("INFO", "Weaving", f"idle-{code}-{last}", f"{loom}: no production entered for {idle} days (last {last})",
                      f"quality change on {qcd}; production expected by {expect.isoformat()}")
                continue
        ranm = 0
        if scb:
            r = a.q("SELECT SUM(meters) FROM loom_monitor WHERE loom_code = ? AND read_date > ?", code, last)
            ranm = (r[0][0] or 0) if r else 0
        sev = "ACTION" if idle >= max(8, usual + 5) else "WATCH"
        if qcd:
            detail = f"quality change on {qcd} but still nothing entered - chase the production entry"
        elif ranm:
            detail = f"ran about {ranm:,.0f} m on the scoreboard since {last} but nothing entered in the ERP - an entry problem"
        elif scb:
            detail = f"stopped on the scoreboard since {last} - the loom is down, not an entry problem"
        else:
            detail = f"normally booked every {usual} days - loom stopped, or production not keyed"
        a.add(sev, "Weaving", f"idle-{code}-{last}", f"{loom}: no production entered for {idle} days (last {last})", detail)

    # A loom's output vs its own usual, REBASED for a quality change. A new quality changes the picks/inch AND the
    # pieces across the beam, so the meters change at the same effort - not an error. "usual" comes only from the
    # loom's OLD-quality days and "now" only from its NEW-quality days, both over RUNNING days (a stoppage / beam-
    # change / knotting day - monitor efficiency below the floor - is left out). expected = usual x old_ppi/new_ppi x
    # new_pieces/old_pieces. Within tolerance the change fully explains the output -> INFO (out of Needs Attention);
    # still off beyond tolerance -> WATCH, showing the adjusted expected figure.
    floor = float(t.get("stoppage_efficiency_floor_pct", 30))
    recent_from = (end - timedelta(days=14)).isoformat()
    before_from, before_to = (end - timedelta(days=74)).isoformat(), (end - timedelta(days=14)).isoformat()
    eff_by = {}
    try:
        for lk, le in a.q("""SELECT loom_code || '|' || read_date, MAX(efficiency_pct) FROM loom_monitor
                             WHERE read_date > ? GROUP BY loom_code, read_date""", before_from):
            eff_by[lk] = le
    except Exception:
        pass
    mon_ppi = {}    # item V: loom number -> [(read_date, picks_per_inch)] - when the loom PHYSICALLY changed quality,
    try:            # from the scoreboard, so a late ERP entry does not pin the change to the wrong (later) date
        for rd, lc, p in a.q("""SELECT read_date, loom_code, picks_per_inch FROM loom_monitor
                                WHERE read_date > ? AND picks_per_inch IS NOT NULL""", before_from):
            mon_ppi.setdefault(_lnum(lc), []).append((rd, p))
    except Exception:
        pass

    def run_avg(code, contract, d_from, d_to):
        # Meters per RUNNING day. ERP production is booked in lumps - one entry per roll cut, every 3-5 days - so the
        # number of ENTRIES is not the number of days worked. Spread the meters over the contract's active CALENDAR
        # span in the window and drop stoppage / beam-change / knotting days (monitor efficiency below the floor).
        rows = a.q("""SELECT prod_date, SUM(meters) FROM loom_production
                WHERE loom_code = ? AND contract_no = ? AND prod_date > ? AND prod_date <= ? GROUP BY prod_date""",
                code, contract, d_from, d_to)
        if not rows:
            return 0.0
        tot = sum((m or 0) for _, m in rows)
        pds = sorted(date.fromisoformat(str(pd_)[:10]) for pd_, _ in rows if pd_)
        if not pds:
            return 0.0
        first, last = pds[0], pds[-1]
        # CUT TO CUT (item V): the meters booked on an entry were woven SINCE THE PREVIOUS CUT, not on that one day.
        # One catch-up entry (e.g. 10 days of cloth keyed on 17-Nov) would otherwise read as a single day -> a huge
        # false rate. Start the span the day after the previous ERP entry for this loom+contract, so the meters spread
        # over the real days. If there is no previous cut (a brand-new contract), fall back to the scoreboard's own
        # running days in the window rather than 1.
        prev = a.q("""SELECT MAX(prod_date) FROM loom_production WHERE loom_code = ? AND contract_no = ?
                      AND prod_date < ?""", code, contract, first.isoformat())
        span_start = first
        if prev and prev[0][0]:
            try:
                pc = date.fromisoformat(str(prev[0][0])[:10])
                if pc < first:
                    span_start = pc + timedelta(days=1)
            except ValueError:
                pass
        stoppage, d = 0, span_start
        while d <= last:
            eff = eff_by.get(f"{code}|{d.isoformat()}")
            if eff is not None and eff < floor:
                stoppage += 1
            d += timedelta(days=1)
        span_days = ((last - span_start).days + 1) - stoppage
        if span_days <= 1:                                 # no previous cut to span from -> use scoreboard running days
            scb = a.q("""SELECT COUNT(DISTINCT read_date) FROM loom_monitor WHERE loom_code = ?
                         AND read_date > ? AND read_date <= ? AND COALESCE(meters,0) > 0""", code, d_from, d_to)
            if scb and scb[0][0] and scb[0][0] > 1:
                span_days = scb[0][0]
        return tot / max(1, span_days)

    names2 = dict(a.q("SELECT loom_code, loom_name FROM looms"))
    seen_looms = set()                                     # one finding per PHYSICAL loom (item V dedupe): if the ERP
    for (code,) in a.q("SELECT DISTINCT loom_code FROM loom_production WHERE prod_date > ? AND prod_date <= ?", before_from, d1):
        ln = _lnum(code)                                   # has two codes for one loom (e.g. 18 and 18.0) only the
        if ln is not None and ln in seen_looms:            # first is reported, not the same finding twice
            continue
        seen_looms.add(ln)
        loom = names2.get(code, code)
        rc = _dom_contract(code, recent_from, d1)          # the loom's current (new) contract
        bc = _dom_contract(code, before_from, before_to)   # its previous (old) contract
        if not (rc and bc):
            continue
        usual = run_avg(code, bc, before_from, before_to)  # old quality, before window
        now = run_avg(code, rc, recent_from, d1)           # new quality, recent window
        if not (usual >= 50 and now):
            continue
        if 0.5 <= now / usual <= 1.8:
            continue                                       # not a material swing - leave it
        changed = rc != bc
        op, np_ = _ppi(bc), _ppi(rc)
        ppi_r = (op / np_) if (op and np_) else 1.0
        # item R: a picks/inch change DOES scale ERP meters/day (more picks per inch = fewer linear metres for the same
        # loom speed). A PANELS change does NOT: ERP meters are per loom length, so weaving 1 vs 3 panels gives the same
        # metres/day. So pieces must never multiply the expected ERP figure, and an unknown piece count is never
        # defaulted to 1 (that stale "pieces 1 -> 3" was the loom-27 false alarm). Panels are checked on the scoreboard
        # side in audit/monitor.py, not here.
        adjusted = changed and abs(ppi_r - 1) > 0.01
        expected = usual * ppi_r
        ratio = (now / expected) if expected else 0
        key = f"lhist-{code}"   # stable per loom: the changing figures live in the title/detail, not the ack key
        adj = []
        if abs(ppi_r - 1) > 0.01 and op and np_:
            adj.append(f"ppi {op:g}->{np_:g}")
        qcr = a.q("SELECT MIN(prod_date) FROM loom_production WHERE loom_code = ? AND contract_no = ?", code, rc) if changed else None
        qcd = (qcr[0][0] if qcr and qcr[0][0] else recent_from) if changed else None
        if changed and np_ and qcd:                        # item V: if the scoreboard shows the new picks/inch BEFORE
            seen = [rd for rd, p in mon_ppi.get(_lnum(code), []) if p and abs(p - np_) < 1]   # the ERP entry, the
            if seen and min(seen) < qcd:                   # loom really changed earlier - use that (ERP entry was late)
                qcd = min(seen)
        if adjusted and 0.75 <= ratio <= 1.25:
            # the quality change FULLY explains the output - INFO only, not Needs Attention
            a.add("INFO", "Weaving", key,
                  f"{loom}: output changed because the quality changed on {qcd} ({', '.join(adj)})",
                  f"{now:,.0f} m a day over its running days vs its usual {usual:,.0f} - about the {expected:,.0f} expected from the change")
        elif adjusted:
            # still off AFTER adjusting for the quality change - Needs Attention, with the adjusted expected figure
            a.add("WATCH", "Weaving", key,
                  f"{loom}: {now:,.0f} m a day vs {expected:,.0f} expected after the quality change ({ratio:.1f}x of expected)",
                  f"even allowing for the quality change on {qcd} ({', '.join(adj)}, so about {expected:,.0f} m expected) the output is {ratio:.1f}x of that - check it")
        elif changed:
            # the quality changed but the picks/inch is unchanged, so it cannot explain the swing in ERP metres/day
            a.add("WATCH", "Weaving", key,
                  f"{loom}: {now:,.0f} m a day vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                  f"the quality changed on {qcd} but the picks/inch is the same, so it does not explain the {now / usual:.1f}x - check it")
        else:
            # same quality throughout - a real swing in the loom's own output
            a.add("WATCH", "Weaving", key,
                  f"{loom}: {now:,.0f} m a day vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                  "a big change in the loom's output with no quality change to explain it - stoppage, or entries booked to the wrong loom")

    looms, m, rej = a.q("""SELECT COUNT(DISTINCT loom_code), SUM(meters), SUM(COALESCE(rejected_m, 0))
                           FROM loom_production WHERE prod_date BETWEEN ? AND ?""", d0, d1)[0]
    eff = a.q(f"""SELECT {EFF_AGG}
                  FROM loom_production WHERE prod_date BETWEEN ? AND ?""", d0, d1)[0][0]
    a.summary.append(f"Weaving: {looms} looms, {m or 0:,.0f} m" + (f", efficiency {eff:.1f}%" if eff else "")
                     + (f", rejection {100 * rej / m:.1f}%" if m else "") + ".")
