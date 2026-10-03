"""Loom monitoring (scoreboard / sensors) vs the ERP (generalised from Dost Textiles' 74_scoreboard_data and the
scoreboard checks in 70_erp_audit). Only runs when the mapping has a loom_monitor source.

  - the monitor's data for the day has not arrived                       ACTION
  - per loom over 14 days: ERP meters differ from the monitor by more
    than 25% and 500 m (lower = rolls not entered, higher = over-entered)  WATCH
Monitor looms are matched to ERP looms by loom code, else by the number in the loom name ("LOOM # 7" = 7).
"""

import re
from datetime import date, timedelta


def loom_key(con):
    by_code = {c: c for (c,) in con.execute("SELECT loom_code FROM looms")}
    by_num = {}
    for code, name in con.execute("SELECT loom_code, loom_name FROM looms"):
        digits = re.findall(r"\d+", name or "")
        if digits:
            by_num[str(int(digits[-1]))] = code
    return lambda m: by_code.get(m) or by_num.get(str(int(m)) if str(m).strip().isdigit() else m)


def run(a):
    if not a.q("SELECT 1 FROM loom_monitor LIMIT 1"):
        return
    d1 = date.fromisoformat(a.window[1])
    if a.d0 == a.d1:
        had = a.q("SELECT MAX(read_date) FROM loom_monitor WHERE read_date <= ?", a.window[1])[0][0]
        if had and had < a.window[1] and (d1 - date.fromisoformat(had)).days <= 7:
            a.add("ACTION", "Looms", f"monitor-missing-{a.window[1]}", f"Loom monitor data for {d1:%d-%b} has not arrived (last {had})",
                  "check the scoreboard PC: power, network, or its export")
    key = loom_key(a.con)
    start = (d1 - timedelta(days=13)).isoformat()
    mon = {}
    for loom, m in a.q("SELECT loom_code, SUM(meters) FROM loom_monitor WHERE read_date BETWEEN ? AND ? GROUP BY loom_code",
                       start, a.window[1]):
        k = key(loom)
        if k:
            mon[k] = mon.get(k, 0) + (m or 0)
    erp = dict(a.q("SELECT loom_code, SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ? GROUP BY loom_code",
                   start, a.window[1]))
    names = dict(a.q("SELECT loom_code, loom_name FROM looms"))
    for k, m in mon.items():
        e = erp.get(k, 0) or 0
        if m > 0 and abs(e - m) > max(500, 0.25 * m):
            a.add("WATCH", "Looms", f"monitor-{k}-{a.window[1]}",
                  f"{names.get(k, k)} {start} to {a.window[1]}: ERP {e:,.0f} m, loom monitor {m:,.0f} m ({100 * (e - m) / m:+.0f}%)",
                  "ERP lower than the loom's own count = rolls not entered; higher = production over-entered or booked to this loom")
