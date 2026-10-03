"""
Daily weaving report: meters, efficiency and rejection per loom for one day,
plus looms that wove nothing (generalised from Dost Textiles' 12_/13_/23_).

Efficiency: the ERP's own efficiency_pct where the mapping provides it, otherwise
meters / meters_at_100; combined over looms and shifts as total meters / total
meters at 100%.
"""

from datetime import date, timedelta

from . import Report

EFF_SQL = "COALESCE(efficiency_pct, CASE WHEN meters_at_100 > 0 THEN 100.0 * meters / meters_at_100 END)"
# Combined efficiency = total meters / total meters at 100% (as ERP loom-efficiency reports do): each row's
# efficiency weighted by its meters at 100%; rows without that are weighted by meters.
_W = "COALESCE(meters_at_100, meters)"
EFF_AGG = f"SUM({EFF_SQL} * {_W}) / NULLIF(SUM(CASE WHEN {EFF_SQL} IS NOT NULL THEN {_W} END), 0)"


def idle_looms(con, day, min_days=5):
    """Looms that wove in the last 30 days but have had nothing entered for longer than their usual booking gap
    (many mills book production every few days at roll check, so 'nothing today' alone means little)."""
    dates = {}
    for code, name, d in con.execute("""
            SELECT p.loom_code, COALESCE(l.loom_name, p.loom_code), p.prod_date FROM loom_production p
            LEFT JOIN looms l ON l.loom_code = p.loom_code WHERE p.prod_date BETWEEN ? AND ?
            GROUP BY p.loom_code, p.prod_date ORDER BY 1, 3""", ((day - timedelta(days=120)).isoformat(), day.isoformat())):
        dates.setdefault((code, name), []).append(date.fromisoformat(d))
    out = []
    for (code, name), ds in dates.items():
        if ds[-1] < day - timedelta(days=30):
            continue
        gaps = sorted((b - a).days for a, b in zip(ds, ds[1:]))
        usual = gaps[int(0.9 * (len(gaps) - 1))] if len(gaps) >= 5 else 3
        idle = (day - ds[-1]).days
        if idle >= max(min_days, usual + 2):
            out.append((name, idle))
    return sorted(out, key=lambda x: -x[1])


def daily_production(con, cfg, day=None):
    day = day or (date.today() - timedelta(days=1))      # most ERPs get production keyed the next day
    d = day.isoformat()
    rows = con.execute(f"""
        SELECT COALESCE(l.loom_name, p.loom_code), SUM(p.meters),
               {EFF_AGG},
               SUM(COALESCE(p.rejected_m, 0)), GROUP_CONCAT(DISTINCT p.quality)
        FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
        WHERE p.prod_date = ? GROUP BY p.loom_code ORDER BY 1""", (d,)).fetchall()
    idle = idle_looms(con, day, int(cfg["audit"]["idle_loom_min_days"]))
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
        summary.append(f"Longer than usual with no production entered ({len(idle)}): "
                       + ", ".join(f"{n} ({days} days)" for n, days in idle))
    return Report(f"Weaving production - {day:%d-%b-%Y}", ["Loom", "Meters", "Efficiency %", "Rejection %", "Quality"],
                  out, summary)
