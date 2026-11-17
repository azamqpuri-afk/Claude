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
    names = dict(a.q("SELECT loom_code, loom_name FROM looms"))
    # ERP production is booked once per ROLL CUT (every 3-5 days); the monitor counts every day. A fixed date->today
    # window therefore counts cloth still on the loom (uncut) against the ERP, and splits the first roll - a false
    # shortfall. So compare each loom CUT TO CUT: end the window at the loom's last ERP cut (its last production
    # entry), and start it just after the previous cut ~2 weeks earlier. Both figures already include every width.
    d1s = a.window[1]
    lookback = (d1 - timedelta(days=30)).isoformat()
    # the monitor loom codes that map to each ERP loom (a monitor may name a loom differently)
    mon_codes = {}
    for (mc,) in a.q("SELECT DISTINCT loom_code FROM loom_monitor WHERE read_date BETWEEN ? AND ?", lookback, d1s):
        mk = key(mc)
        if mk:
            mon_codes.setdefault(mk, []).append(mc)
    for k, codes in mon_codes.items():
        lc = a.q("SELECT MAX(prod_date) FROM loom_production WHERE loom_code = ? AND prod_date <= ?", k, d1s)
        last_cut = lc[0][0] if lc else None
        if not last_cut:
            continue                                     # no ERP production to compare; the idle-loom check covers silence
        target = (date.fromisoformat(last_cut) - timedelta(days=13)).isoformat()
        pc = a.q("SELECT MAX(prod_date) FROM loom_production WHERE loom_code = ? AND prod_date <= ?", k, target)
        prev_cut = pc[0][0] if pc and pc[0][0] else None
        if prev_cut:                                     # window (previous cut, last cut] - whole roll cycles
            lo_label, lo_op = prev_cut, ">"
            e = a.q("SELECT SUM(meters) FROM loom_production WHERE loom_code = ? AND prod_date > ? AND prod_date <= ?",
                    k, prev_cut, last_cut)[0][0] or 0
        else:                                            # only one cut cycle of history
            lo_label, lo_op = target, ">="
            e = a.q("SELECT SUM(meters) FROM loom_production WHERE loom_code = ? AND prod_date >= ? AND prod_date <= ?",
                    k, target, last_cut)[0][0] or 0
        ph = ",".join("?" for _ in codes)
        bound = f"read_date {lo_op} ? AND read_date <= ?"
        m = a.q(f"SELECT SUM(meters) FROM loom_monitor WHERE loom_code IN ({ph}) AND {bound}",
                *codes, lo_label, last_cut)[0][0] or 0
        if m > 0 and abs(e - m) > max(500, 0.25 * m):
            a.add("WATCH", "Looms", f"monitor-{k}",
                  f"{names.get(k, k)} {lo_label} to {last_cut} (cut to cut): ERP {e:,.0f} m, loom monitor {m:,.0f} m ({100 * (e - m) / m:+.0f}%)",
                  "ERP lower than the loom's own count = rolls not entered; higher = production over-entered or booked to this loom")

    # Quality change not entered in the ERP (alert C): the scoreboard shows a new PICKS/INCH on a loom, but the ERP's
    # current contract/beam on that loom still has the OLD ppi and no new beam was issued - so production on the new
    # quality is not being entered. We compare PICKS/INCH (and pieces where known), NEVER the style text: the same
    # cloth has different names in the scoreboard and the ERP ("Mehak Cotton" vs "90x82 ... MHK"), so a text compare
    # gives false alarms. Only looms with a recent ERP beam/production are considered, and only changes in the last
    # lookback days, so looms the mill removed (no ERP data, an old change) never fire.
    grace = int(a.t.get("quality_change_erp_grace_days", 3))
    look = (d1 - timedelta(days=30)).isoformat()
    for k, codes in mon_codes.items():
        ph = ",".join("?" for _ in codes)
        cur = a.q(f"""SELECT read_date, picks_per_inch, pieces, style FROM loom_monitor
                      WHERE loom_code IN ({ph}) AND picks_per_inch IS NOT NULL
                      ORDER BY read_date DESC LIMIT 1""", *codes)
        if not cur or cur[0][1] is None:
            continue
        cur_date, cur_ppi, cur_pieces, cur_style = cur[0]
        # last scoreboard day whose ppi differed from now = last day of the old quality; the change is the next day
        prev = a.q(f"""SELECT MAX(read_date) FROM loom_monitor WHERE loom_code IN ({ph}) AND read_date <= ?
                       AND picks_per_inch IS NOT NULL AND ABS(picks_per_inch - ?) > 0.5""", *codes, cur_date, cur_ppi)
        prev_diff = prev[0][0] if prev and prev[0][0] else None
        if not prev_diff:
            continue                                     # the scoreboard shows no picks/inch change for this loom
        cd = a.q(f"SELECT MIN(read_date) FROM loom_monitor WHERE loom_code IN ({ph}) AND read_date > ? AND ABS(picks_per_inch - ?) <= 0.5",
                 *codes, prev_diff, cur_ppi)
        change_date = cd[0][0] if cd and cd[0][0] else None
        if not change_date or change_date < look or (d1 - date.fromisoformat(change_date)).days < grace:
            continue                                     # no change, too old, or still inside the grace period
        # only consider a loom the ERP still tracks (recent production or a beam on it) - not one the mill removed
        lastp = a.q("SELECT MAX(prod_date) FROM loom_production WHERE loom_code = ?", k)
        last_prod = lastp[0][0] if lastp and lastp[0][0] else None
        onbeam = a.q("""SELECT beam_no, contract_no FROM beam_movements WHERE loom_code = ? AND move_type = 'to_loom'
                        ORDER BY move_date DESC LIMIT 1""", k)
        if not ((last_prod and last_prod >= look) or (onbeam and onbeam[0][0])):
            continue
        # "entered" = the ERP's current contract on the loom already has the new ppi, OR a beam was issued on/after the
        # change. Current ERP contract = the loom's most recent production contract (falls back to the mounted beam's).
        erp_cn = None
        rc = a.q("""SELECT contract_no FROM loom_production WHERE loom_code = ? AND contract_no IS NOT NULL
                    ORDER BY prod_date DESC LIMIT 1""", k)
        erp_cn = (rc[0][0] if rc and rc[0][0] else (onbeam[0][1] if onbeam else None))
        erp_ppi = None
        if erp_cn:
            pr = a.q("SELECT picks_per_inch FROM contracts WHERE contract_no = ?", erp_cn)
            erp_ppi = pr[0][0] if pr and pr[0][0] is not None else None
        if erp_ppi is not None and abs(erp_ppi - cur_ppi) <= 0.5:
            continue                                     # the ERP's current contract already has the new ppi - entered
        issued = a.q("SELECT 1 FROM beam_movements WHERE loom_code = ? AND move_type = 'to_loom' AND move_date >= ? LIMIT 1",
                     k, change_date)
        if issued:
            continue                                     # a new beam was recorded on/after the change - entered
        sb_m = a.q(f"SELECT SUM(meters) FROM loom_monitor WHERE loom_code IN ({ph}) AND read_date >= ?",
                   *codes, change_date)[0][0] or 0
        ob = f"beam {onbeam[0][0]} / contract {erp_cn}" if (onbeam and onbeam[0][0]) else (f"contract {erp_cn}" if erp_cn else "the old beam/contract")
        styletxt = (str(cur_style) + ", " if cur_style else "") + f"{cur_ppi:g} ppi"
        a.add("ACTION", "Looms", f"qcnotentered-{k}-{change_date}",
              f"{names.get(k, k)}: quality changed on {change_date} (scoreboard: {styletxt}) - not entered in the ERP after {grace} days",
              f"the ERP still has {ob} ({erp_ppi:g} ppi); last ERP production {last_prod or 'none'}; about {sb_m:,.0f} m "
              f"run on the scoreboard since the change are not in the ERP" if erp_ppi is not None else
              f"the ERP still has {ob}; last ERP production {last_prod or 'none'}; about {sb_m:,.0f} m run on the "
              f"scoreboard since the change are not in the ERP")
