"""Weaving checks on loom production."""

from datetime import date, timedelta

from ..reports.production import EFF_AGG, EFF_SQL


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

    # efficiency over 100% - tiny part-shift entries round over 100, only a big one is a definite error
    for pd, loom, eff, m in a.q(f"""SELECT p.prod_date, COALESCE(l.loom_name, p.loom_code), {EFF_SQL}, p.meters
            FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
            WHERE p.prod_date BETWEEN ? AND ? AND {EFF_SQL} > 100""", d0, d1):
        a.add("CONFIRMED" if m >= 150 else "WATCH", "Weaving", f"eff100-{loom}-{pd}",
              f"{loom} {pd}: efficiency {eff:.0f}% (over 100%) on {m:,.0f} m", "meters, shift length or RPM keyed wrong")

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
        stoppage, d = 0, first
        while d <= last:
            eff = eff_by.get(f"{code}|{d.isoformat()}")
            if eff is not None and eff < floor:
                stoppage += 1
            d += timedelta(days=1)
        days = max(1, ((last - first).days + 1) - stoppage)
        return tot / days

    def pieces_for(code, contract, d_from, d_to):
        # Panels across: the scoreboard's DOMINANT pieces (its meters already count all pieces). Use the mode, not the
        # mean, so the few transition days just after a style change - still tagged with the old piece count - do not
        # skew it (2.6 -> 3). Else the beam's bwidth, else 1.
        try:
            r = a.q("""SELECT pieces FROM loom_monitor WHERE loom_code = ? AND pieces IS NOT NULL
                       AND read_date > ? AND read_date <= ? GROUP BY pieces ORDER BY COUNT(*) DESC, pieces DESC LIMIT 1""",
                    code, d_from, d_to)
            if r and r[0][0]:
                return r[0][0]
        except Exception:
            pass
        try:
            r = a.q("""SELECT bwidth FROM beams WHERE contract_no = ? AND bwidth IS NOT NULL
                       GROUP BY bwidth ORDER BY COUNT(*) DESC LIMIT 1""", contract)
            if r and r[0][0]:
                return r[0][0]
        except Exception:
            pass
        return 1.0

    names2 = dict(a.q("SELECT loom_code, loom_name FROM looms"))
    for (code,) in a.q("SELECT DISTINCT loom_code FROM loom_production WHERE prod_date > ? AND prod_date <= ?", before_from, d1):
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
        old_pieces, new_pieces = pieces_for(code, bc, before_from, before_to), pieces_for(code, rc, recent_from, d1)
        pieces_r = (new_pieces / old_pieces) if (old_pieces and new_pieces) else 1.0
        adjusted = changed and (abs(ppi_r - 1) > 0.01 or abs(pieces_r - 1) > 0.01)
        expected = usual * ppi_r * pieces_r
        ratio = (now / expected) if expected else 0
        key = f"lhist-{code}"   # stable per loom: the changing figures live in the title/detail, not the ack key
        adj = []
        if abs(ppi_r - 1) > 0.01 and op and np_:
            adj.append(f"ppi {op:g}->{np_:g}")
        if abs(pieces_r - 1) > 0.01:
            adj.append(f"pieces {old_pieces:g}->{new_pieces:g}")
        qcr = a.q("SELECT MIN(prod_date) FROM loom_production WHERE loom_code = ? AND contract_no = ?", code, rc) if changed else None
        qcd = (qcr[0][0] if qcr and qcr[0][0] else recent_from) if changed else None
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
            # the quality changed but the picks/inch and pieces are unchanged, so it cannot explain the swing
            a.add("WATCH", "Weaving", key,
                  f"{loom}: {now:,.0f} m a day vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                  f"the quality changed on {qcd} but the picks/inch and pieces are the same, so it does not explain the {now / usual:.1f}x - check it")
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
