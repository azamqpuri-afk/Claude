"""Weaving checks on loom production."""

from datetime import date

from ..reports.production import EFF_AGG, EFF_SQL


def run(a):
    d0, d1 = a.window
    t = a.t

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

    # looms that wove recently but have had nothing entered for longer than usual
    gap90 = a.profile.get("loom_gap_p90", {})
    end = date.fromisoformat(d1)
    for code, loom, last in a.q("""SELECT p.loom_code, COALESCE(MAX(l.loom_name), p.loom_code), MAX(p.prod_date)
            FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
            WHERE p.prod_date BETWEEN date(?, '-30 days') AND ? GROUP BY p.loom_code""", d1, d1):
        idle = (end - date.fromisoformat(last)).days
        usual = gap90.get(code) or 3
        if idle >= max(int(t["idle_loom_min_days"]), usual + 2):
            a.add("ACTION" if idle >= max(8, usual + 5) else "WATCH", "Weaving", f"idle-{code}-{last}",
                  f"{loom}: no production entered for {idle} days (last {last})",
                  f"normally booked every {usual} days - loom stopped, or production not keyed")

    looms, m, rej = a.q("""SELECT COUNT(DISTINCT loom_code), SUM(meters), SUM(COALESCE(rejected_m, 0))
                           FROM loom_production WHERE prod_date BETWEEN ? AND ?""", d0, d1)[0]
    eff = a.q(f"""SELECT {EFF_AGG}
                  FROM loom_production WHERE prod_date BETWEEN ? AND ?""", d0, d1)[0][0]
    a.summary.append(f"Weaving: {looms} looms, {m or 0:,.0f} m" + (f", efficiency {eff:.1f}%" if eff else "")
                     + (f", rejection {100 * rej / m:.1f}%" if m else "") + ".")
