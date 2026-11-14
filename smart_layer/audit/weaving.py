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
        a.add("CONFIRMED" if m >= 150 else "WATCH", "Weaving", f"eff100-{pd}-{loom}",
              f"{loom} {pd}: efficiency {eff:.0f}% (over 100%) on {m:,.0f} m", "meters, shift length or RPM keyed wrong")

    # production without a beam - only for ERPs that record beams on production
    with_beam, rows = a.q("""SELECT SUM(beam_no IS NOT NULL), COUNT(*) FROM loom_production WHERE prod_date >= date(?, '-120 days')""", d1)[0]
    if rows and with_beam and with_beam > 0.5 * rows:
        for pd, loom, cn, m in a.q("""SELECT p.prod_date, COALESCE(l.loom_name, p.loom_code), p.contract_no, p.meters
                FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
                WHERE p.prod_date BETWEEN ? AND ? AND p.beam_no IS NULL""", d0, d1):
            a.add("CONFIRMED", "Weaving", f"nobeam-{pd}-{loom}", f"{loom} {pd}: {m:,.0f} m of contract {cn or '?'} entered without a beam")

    # high rejection per loom and contract
    for loom, cn, rej, tot in a.q("""SELECT COALESCE(l.loom_name, p.loom_code), p.contract_no, SUM(COALESCE(p.rejected_m, 0)), SUM(p.meters)
            FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code WHERE p.prod_date BETWEEN ? AND ?
            GROUP BY p.loom_code, p.contract_no HAVING SUM(p.meters) > ? AND SUM(COALESCE(p.rejected_m, 0)) > ? * SUM(p.meters) / 100.0""",
            d0, d1, float(t["rejection_min_meters"]), float(t["rejection_pct"])):
        a.add("WATCH", "Weaving", f"rej-{d1}-{loom}-{cn}", f"{loom} (contract {cn or '?'}): rejection {100 * rej / tot:.0f}% of {tot:,.0f} m")

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

    # a loom's output far from its own usual (last 14 days vs the 60 days before). A quality change moves the picks
    # per inch, which moves the meters at the same effort, so that is not an error - compare the loom's main quality
    # and, when it changed, report it as INFO with the ppi change instead of a WATCH.
    recent = dict(a.q("""SELECT loom_code, SUM(meters) / 14.0 FROM loom_production
                         WHERE prod_date > date(?, '-14 days') AND prod_date <= ? GROUP BY loom_code""", d1, d1))
    before = a.q("""SELECT p.loom_code, COALESCE(MAX(l.loom_name), p.loom_code), SUM(p.meters) / 60.0 FROM loom_production p
                    LEFT JOIN looms l ON l.loom_code = p.loom_code
                    WHERE p.prod_date > date(?, '-74 days') AND p.prod_date <= date(?, '-14 days') GROUP BY p.loom_code""", d1, d1)
    recent_from = (end - timedelta(days=14)).isoformat()
    before_from, before_to = (end - timedelta(days=74)).isoformat(), (end - timedelta(days=14)).isoformat()
    for code, loom, usual in before:
        now = recent.get(code, 0) or 0
        if not (usual and usual >= 50 and now and (now < 0.5 * usual or now > 1.8 * usual)):
            continue
        rc, bc = _dom_contract(code, recent_from, d1), _dom_contract(code, before_from, before_to)
        rq = _quality(rc)
        changed = bool(rc and bc and rc != bc)
        # the quality-change date is the FIRST production date of the new contract on this loom - not the first date
        # inside the recent window (which is wrong when the change happened before the 14-day window opened).
        qcd = None
        if changed:
            qcr = a.q("SELECT MIN(prod_date) FROM loom_production WHERE loom_code = ? AND contract_no = ?", code, rc)
            qcd = qcr[0][0] if qcr and qcr[0][0] else recent_from
        op, np_ = (_ppi(bc), _ppi(rc)) if (rc and bc) else (None, None)
        ppi_changed = bool(op and np_ and abs(op - np_) > 0.01)
        key = f"lhist-{code}-{d1}"
        if not changed:
            # same contract throughout the comparison - a real swing in the loom's own output
            a.add("WATCH", "Weaving", f"{key}-{round(now / usual, 1)}",
                  f"{loom}: {now:,.0f} m a day over 14 days vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                  "a big change in the loom's output - stoppage, or entries booked to the wrong loom")
            continue
        # the quality changed, so the 'usual' baseline is the OLD quality and cannot judge the new one. Prefer a
        # like-for-like baseline: earlier days of the SAME quality on this loom. If there are none, we cannot say the
        # output is off from history alone - fall back to the picks/inch ratio, and if the ppi did not change either,
        # report it as INFO ("not enough same-quality history") rather than a false WATCH.
        sq = a.q("""SELECT SUM(meters) FROM loom_production WHERE loom_code = ? AND quality = ?
                    AND prod_date > date(?, '-74 days') AND prod_date <= date(?, '-14 days')""", code, rq, d1, d1)
        sq_usual = ((sq[0][0] or 0) / 60.0) if sq else 0
        if sq_usual >= 50:
            if now < 0.5 * sq_usual or now > 1.8 * sq_usual:
                a.add("WATCH", "Weaving", f"{key}-{round(now / sq_usual, 1)}",
                      f"{loom}: {now:,.0f} m a day over 14 days vs its usual {sq_usual:,.0f} for the same quality ({now / sq_usual:.1f}x)",
                      f"compared only against earlier days of quality '{rq}', so the quality change is not the reason - check it")
            continue
        if ppi_changed:
            # expected = usual x old_ppi/new_ppi; within +-25% the picks/inch change explains the meters (INFO)
            expected = usual * op / np_
            ratio = (now / expected) if expected else 0
            if 0.75 <= ratio <= 1.25:
                a.add("INFO", "Weaving", key, f"{loom}: output changed because the quality changed on {qcd} (ppi {op:g} -> {np_:g})",
                      f"{now:,.0f} m a day over 14 days vs its usual {usual:,.0f} - about the {expected:,.0f} expected from the picks/inch change")
            else:
                a.add("WATCH", "Weaving", f"{key}-{round(now / usual, 1)}",
                      f"{loom}: {now:,.0f} m a day over 14 days vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                      f"even allowing for the quality change on {qcd} (ppi {op:g} -> {np_:g}, so about {expected:,.0f} m expected) the output is {ratio:.1f}x - check it")
        else:
            # the picks/inch did NOT change, so the meters are directly comparable to the loom's usual - a quality
            # change that leaves the ppi the same does not explain a big swing. Stays a WATCH (not a soft INFO).
            ppic = (f"the picks/inch is the same ({op:g})" if (op and np_) else "the picks/inch is not recorded")
            a.add("WATCH", "Weaving", f"{key}-{round(now / usual, 1)}",
                  f"{loom}: {now:,.0f} m a day over 14 days vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                  f"the quality changed on {qcd} but {ppic}, so the quality change does not explain the {now / usual:.1f}x - check it")

    looms, m, rej = a.q("""SELECT COUNT(DISTINCT loom_code), SUM(meters), SUM(COALESCE(rejected_m, 0))
                           FROM loom_production WHERE prod_date BETWEEN ? AND ?""", d0, d1)[0]
    eff = a.q(f"""SELECT {EFF_AGG}
                  FROM loom_production WHERE prod_date BETWEEN ? AND ?""", d0, d1)[0][0]
    a.summary.append(f"Weaving: {looms} looms, {m or 0:,.0f} m" + (f", efficiency {eff:.1f}%" if eff else "")
                     + (f", rejection {100 * rej / m:.1f}%" if m else "") + ".")
