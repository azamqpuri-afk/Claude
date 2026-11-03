"""
Contract progress and yarn-shortage warning (0.15), generic and read-only.

  contract_progress  every running contract: ordered, woven, dispatched, balance, looms, m/day, expected finish;
                     overdue / slow ones flagged.
  yarn_shortage      yarn needed for the remaining metres of running contracts (by nominal count) vs available
                     (mill godown + at sizers), with the days of cover left. Feeds a dashboard panel and a push.

Yarn weight per metre comes from the mill's own closing method (textile/method.py), so it follows the same
construction/divisor/count rules as the closing report.
"""

import re
from datetime import date, timedelta

from ..reports import Report
from .method import Method


def _count_num(s):
    """Nominal yarn count as a number: '40/1 CTN' -> 40, '52/1' -> 52, 40.0 -> 40. None if not found."""
    if s is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(s))
    return round(float(m.group())) if m else None


def _running(con, as_of):
    """Running contracts: ordered > 0 and woven < ordered, with production in the last 120 days OR a beam on a loom."""
    d = as_of.isoformat()
    woven = dict(con.execute("SELECT contract_no, SUM(meters) FROM loom_production WHERE prod_date <= ? GROUP BY contract_no", (d,)))
    recent = {c for (c,) in con.execute("SELECT DISTINCT contract_no FROM loom_production WHERE prod_date > ?",
                                        ((as_of - timedelta(days=120)).isoformat(),))}
    on_loom = {}
    for c, n in con.execute("SELECT contract_no, COUNT(*) FROM beams WHERE status = 'on_loom' GROUP BY contract_no"):
        on_loom[str(c)] = n
    rows = []
    for cno, party, quality, ordered, cdate, epi, ppi, width, wy, wc, fy, fc, owner in con.execute(
            """SELECT contract_no, party_name, quality, ordered_meters, contract_date, ends_per_inch, picks_per_inch,
                      width_in, warp_yarn, warp_count, weft_yarn, weft_count, yarn_owner FROM contracts"""):
        o = ordered or 0
        w = woven.get(cno, 0) or 0
        if o <= 0 or w >= o * 1.001:
            continue
        if cno not in recent and str(cno) not in on_loom:
            continue
        rows.append(dict(contract=cno, party=party, quality=quality, ordered=o, woven=w, cdate=cdate,
                         epi=epi, ppi=ppi, width=width, warp_yarn=wy, warp_count=wc, weft_yarn=fy, weft_count=fc,
                         looms=on_loom.get(str(cno), 0)))
    return rows


def contract_progress(con, cfg, as_of=None):
    as_of = as_of or date.today()
    d = as_of.isoformat()
    since = (as_of - timedelta(days=30)).isoformat()
    disp = dict(con.execute("""SELECT contract_no, SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters)
                               FROM fabric_dispatches WHERE dispatch_date <= ? GROUP BY contract_no""", (d,)))
    rate30 = dict(con.execute("""SELECT contract_no, SUM(meters) FROM loom_production WHERE prod_date > ? AND prod_date <= ?
                                 GROUP BY contract_no""", (since, d)))
    rows, slow = [], 0
    for r in sorted(_running(con, as_of), key=lambda r: -(r["ordered"] - r["woven"])):
        bal = max(0, r["ordered"] - r["woven"])
        mday = (rate30.get(r["contract"], 0) or 0) / 30.0
        days = bal / mday if mday > 0 else None
        finish = (as_of + timedelta(days=round(days))).isoformat() if days is not None else None
        note = ""
        if r["looms"] == 0 and bal > 0:
            note = "no loom running it"; slow += 1
        elif days is not None and days > 120:
            note = "slow"; slow += 1
        rows.append([r["contract"], r["party"], r["quality"], round(r["ordered"]), round(r["woven"]),
                     round(disp.get(r["contract"], 0) or 0), round(bal), r["looms"], round(mday),
                     (f"{days:.0f} d → {finish[5:]}" if days is not None else "—"), note])
    summary = [f"{len(rows)} running contracts; {slow} slow or with no loom on them.",
               "Balance = ordered − woven. m/day is the last 30 days; finish = balance ÷ m/day."]
    return Report(f"Contract progress - {as_of:%d-%b-%Y}",
                  ["Contract", "Party", "Quality", "Ordered m", "Woven m", "Dispatched m", "Balance m", "Looms",
                   "m/day", "Finish (est)", "Note"], rows, summary)


def yarn_needed(con, cfg, as_of=None):
    """kg of yarn needed for the remaining metres of running contracts, bucketed by nominal count. Returns
    {count: kg}. Uses the mill's closing weight formula for warp and weft per metre."""
    as_of = as_of or date.today()
    method = Method(cfg["closing"])
    need = {}
    for r in _running(con, as_of):
        bal = max(0.0, (r["ordered"] or 0) - (r["woven"] or 0))
        if bal <= 0:
            continue
        st = method.settings(r["contract"], None)
        for side, tpi, yarn, cnt in (("warp", r["epi"], r["warp_yarn"], r["warp_count"]),
                                     ("weft", r["ppi"], r["weft_yarn"], r["weft_count"])):
            c = _count_num(cnt)
            if not c or not tpi or not r["width"]:
                continue
            lbm = method.weight(side, st, tpi, r["width"], method.closing_count(side, yarn, cnt, st))
            if lbm:
                need[c] = need.get(c, 0.0) + bal * lbm / 2.2046         # lbs/m -> kg
    return need


def yarn_available(con, as_of=None):
    """kg of yarn available now by nominal count: net mill movements (received − consumed/sold) plus yarn at
    sizers, from yarn_movements (kg), bucketed by the count parsed from the yarn name."""
    as_of = as_of or date.today()
    avail = {}
    for name, mt, kg in con.execute("""SELECT yarn_name, move_type, SUM(kg) FROM yarn_movements
            WHERE move_date <= ? GROUP BY yarn_name, move_type""", (as_of.isoformat(),)):
        c = _count_num(name)
        if not c:
            continue
        sign = 1 if mt in ("party_receipt", "from_looms", "from_sizer", "opening", "sized_beams_received") else -1
        if mt in ("sale", "party_return"):
            sign = -1
        avail[c] = avail.get(c, 0.0) + sign * (kg or 0)
    return avail


def yarn_shortage(con, cfg, as_of=None):
    """Counts where the yarn needed for running contracts exceeds what is available; worst (most short) first."""
    as_of = as_of or date.today()
    need, avail = yarn_needed(con, cfg, as_of), yarn_available(con, as_of)
    rows = []
    for c in sorted(set(need) | set(avail)):
        n, a = round(need.get(c, 0)), round(max(0.0, avail.get(c, 0)))
        short = n - a
        rows.append([f"{c}s", n, a, short, ("SHORT" if short > 0 else "ok")])
    rows.sort(key=lambda r: -r[3])
    shorts = [r for r in rows if r[3] > 0]
    summary = [f"{len(shorts)} count(s) will run short for the running contracts' remaining metres."
               if shorts else "No yarn shortage for the running contracts.",
               "Needed = remaining metres × the construction's warp+weft weight; available = net mill yarn + at sizers, by nominal count."]
    return Report(f"Yarn shortage - {as_of:%d-%b-%Y}",
                  ["Count", "Needed kg", "Available kg", "Short kg", "Status"], rows, summary)
