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
        finish = (as_of + timedelta(days=round(days))) if days is not None else None
        note = ""
        if r["looms"] == 0 and bal > 0:
            note = "no loom running it"; slow += 1
        elif days is not None and days > 120:
            note = "slow"; slow += 1
        rows.append([r["contract"], r["party"], r["quality"], round(r["ordered"]), round(r["woven"]),
                     round(disp.get(r["contract"], 0) or 0), round(bal), r["looms"], round(mday),
                     (f"{days:.0f} d → {finish:%d-%b-%Y}" if days is not None else "—"), note])
    summary = [f"{len(rows)} running contracts; {slow} slow or with no loom on them.",
               "Balance = ordered − woven. m/day is the last 30 days; finish = balance ÷ m/day."]
    return Report(f"Contract progress - {as_of:%d-%b-%Y}",
                  ["Contract", "Party", "Quality", "Ordered m", "Woven m", "Dispatched m", "Balance m", "Looms",
                   "m/day", "Finish (est)", "Note"], rows, summary)


def _canon_map(cfg):
    """Map a nominal count to the canonical count of its combine-group ([reports] combine_counts, e.g. [[52,54]]);
    the smallest count in a group names it. Counts not in any group map to themselves."""
    canon = {}
    for grp in (cfg["reports"].get("combine_counts") or []):
        nums = sorted(round(float(x)) for x in grp)
        if nums:
            for x in nums:
                canon[x] = nums[0]
    return canon


def yarn_needed(con, cfg, as_of=None):
    """kg of yarn needed for the remaining metres of running contracts, split into WARP and WEFT, bucketed by
    (combined) nominal count. Returns {count: {"warp": kg, "weft": kg}}. Warp and weft are kept apart because
    warp is provisioned as sized beams while weft is drawn from loose yarn."""
    as_of = as_of or date.today()
    method = Method(cfg["closing"])
    canon = _canon_map(cfg)
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
            c = canon.get(c, c)
            lbm = method.weight(side, st, tpi, r["width"], method.closing_count(side, yarn, cnt, st))
            if lbm:
                need.setdefault(c, {"warp": 0.0, "weft": 0.0})[side] += bal * lbm / 2.2046   # lbs/m -> kg
    return need


def yarn_available(con, cfg=None, as_of=None):
    """kg of LOOSE yarn available now by (combined) nominal count: net mill movements (received − consumed/sold)
    plus yarn at sizers, from yarn_movements (kg), bucketed by the count parsed from the yarn name."""
    as_of = as_of or date.today()
    canon = _canon_map(cfg) if cfg else {}
    avail = {}
    for name, mt, kg in con.execute("""SELECT yarn_name, move_type, SUM(kg) FROM yarn_movements
            WHERE move_date <= ? GROUP BY yarn_name, move_type""", (as_of.isoformat(),)):
        c = _count_num(name)
        if not c:
            continue
        c = canon.get(c, c)
        sign = 1 if mt in ("party_receipt", "from_looms", "from_sizer", "opening", "sized_beams_received") else -1
        if mt in ("sale", "party_return"):
            sign = -1
        avail[c] = avail.get(c, 0.0) + sign * (kg or 0)
    return avail


def warp_on_beams(con, cfg, as_of=None):
    """kg of warp yarn already provisioned as sized beams (not loose), by (combined) warp count. From the ERP's
    sized-set receipts (sized_sets.yarn_sent_kg) for running contracts, so warp need is seen as already covered."""
    as_of = as_of or date.today()
    canon = _canon_map(cfg)
    running = {r["contract"]: _count_num(r["warp_count"]) for r in _running(con, as_of)}
    out = {}
    try:
        rows = con.execute("SELECT contract_no, SUM(COALESCE(yarn_sent_kg,0)) FROM sized_sets GROUP BY contract_no").fetchall()
    except Exception:
        return out
    for cno, kg in rows:
        c = running.get(cno)
        if not c:
            continue
        c = canon.get(c, c)
        out[c] = out.get(c, 0.0) + (kg or 0)
    return out


def yarn_shortage(con, cfg, as_of=None):
    """Counts where LOOSE weft yarn will run short for the running contracts' remaining metres; worst first. Warp
    is provisioned as sized beams (shown separately), so only the weft need is compared against loose yarn stock;
    counts that are the same yarn are combined via [reports] combine_counts."""
    as_of = as_of or date.today()
    need = yarn_needed(con, cfg, as_of)
    avail = yarn_available(con, cfg, as_of)
    beams = warp_on_beams(con, cfg, as_of)
    rows = []
    for c in sorted(set(need) | set(avail) | set(beams)):
        weft = round(need.get(c, {}).get("weft", 0))
        warp = round(need.get(c, {}).get("warp", 0))
        loose = round(max(0.0, avail.get(c, 0)))
        onbeam = round(max(0.0, beams.get(c, 0)))
        short = weft - loose                                   # only weft draws on loose yarn
        rows.append([f"{c}s", weft, loose, short, warp, onbeam, ("SHORT" if short > 0 else "ok")])
    rows.sort(key=lambda r: -r[3])
    shorts = [r for r in rows if r[3] > 0]
    summary = [f"{len(shorts)} count(s) will run short of loose weft yarn for the running contracts' remaining metres."
               if shorts else "No weft-yarn shortage for the running contracts.",
               "Weft need = remaining metres × weft weight; loose = net mill + sizer yarn. Warp is provisioned as "
               "sized beams (warp need and warp-on-beams shown for reference), so it is not a loose-yarn shortage."]
    return Report(f"Yarn shortage - {as_of:%d-%b-%Y}",
                  ["Count", "Weft need kg", "Loose yarn kg", "Short kg", "Warp need kg", "Warp on beams kg", "Status"],
                  rows, summary)
