"""
Daily weaving report: meters, efficiency and rejection per loom for one day,
plus looms that wove nothing (generalised from Dost Textiles' 12_/13_/23_).

Efficiency is meter-weighted: the ERP's own efficiency_pct where the mapping
provides it, otherwise meters / meters_at_100.
"""

from datetime import date, timedelta

from . import Report

EFF_SQL = "COALESCE(efficiency_pct, CASE WHEN meters_at_100 > 0 THEN 100.0 * meters / meters_at_100 END)"


def daily_production(con, cfg, day=None):
    day = day or (date.today() - timedelta(days=1))      # most ERPs get production keyed the next day
    d = day.isoformat()
    rows = con.execute(f"""
        SELECT COALESCE(l.loom_name, p.loom_code), SUM(p.meters),
               SUM({EFF_SQL} * p.meters) / NULLIF(SUM(CASE WHEN {EFF_SQL} IS NOT NULL THEN p.meters END), 0),
               SUM(COALESCE(p.rejected_m, 0)), GROUP_CONCAT(DISTINCT p.quality)
        FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
        WHERE p.prod_date = ? GROUP BY p.loom_code ORDER BY 1""", (d,)).fetchall()
    idle = [r[0] for r in con.execute("""
        SELECT COALESCE(l.loom_name, p.loom_code) FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
        WHERE p.prod_date BETWEEN ? AND ? GROUP BY p.loom_code
        HAVING SUM(p.prod_date = ?) = 0 ORDER BY 1""", ((day - timedelta(days=30)).isoformat(), d, d))]
    total_m = sum(r[1] or 0 for r in rows)
    w = [(r[2], r[1]) for r in rows if r[2] is not None]
    eff = sum(e * m for e, m in w) / sum(m for _, m in w) if w else None
    rej = sum(r[3] or 0 for r in rows)
    out = [[n, m, round(e, 1) if e is not None else None, round(100.0 * rj / m, 1) if m else None, q]
           for n, m, e, rj, q in rows]
    summary = [f"{len(rows)} looms wove {total_m:,.0f} m"
               + (f", efficiency {eff:.1f}%" if eff is not None else "")
               + (f", rejection {100.0 * rej / total_m:.1f}%" if total_m else "") + "."]
    if idle:
        summary.append(f"No production entered for {len(idle)} loom(s) that ran in the last 30 days: {', '.join(idle)}")
    return Report(f"Weaving production - {day:%d-%b-%Y}", ["Loom", "Meters", "Efficiency %", "Rejection %", "Quality"],
                  out, summary)
