"""
Beams (generalised from Dost Textiles' 52_, 60_-65_ and 75_ scripts):

  beams_on_looms  every beam on a loom: fabric still to come, days to finish, and the sized beam
                  waiting to replace it (matched on contract, or on ends within a tolerance)
  beam_stock      sized beams waiting, empty beams in the mill, beams at each sizer
  sizer_ledger    empty beams sent to each sizer vs beams that came back, and numbers not seen again

Days to finish:
  fabric to come = set length x (1 - shrinkage) - fabric woven from the beam
  days           = fabric to come / the loom's meters a day - days since production was last entered
                   (many mills enter production at roll check, so the loom has woven since then)
Shrinkage (warp length -> fabric) is learned from each contract's finished beams; a contract without one
uses contracts of the same quality, then the mill's average, then [beams] default_shrinkage_pct.
Settings in config [beams].
"""

import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta

from ..reports import Report

DEFAULTS = {"warn_days": 15, "urgent_days": 5, "rate_days": 30, "ends_tolerance": 5, "default_shrinkage_pct": 7,
            "sizer_return_days": 30, "own_beam_sizers": [], "beam_aliases": {}, "ignore_beams": [], "empty_age_out_days": 120,
            "shrink_min_pct": 1, "shrink_max_pct": 15,    # real shrinkage is ~1-12% by quality; outside = cut/on-loom beam
            "monitor_rate_days": 14,                      # loom-monitor meters/day window, as a second days-to-finish
            "warn_days_by_type": {}}                      # per loom type, e.g. {VA = 7, AIRJET = 7}; matched in the loom name


def warn_days_for(loom_name, t):
    """Days-ahead alert window for a loom, by its type. A key of warn_days_by_type found in the loom name (e.g. 'VA',
    'AIRJET') wins - fast looms eat a beam quicker, so they warn sooner; otherwise the default warn_days."""
    name = str(loom_name or "").upper()
    for key, days in (t.get("warn_days_by_type") or {}).items():
        if str(key).upper() in name:
            return float(days)
    return float(t["warn_days"])


def settings(cfg):
    return {**DEFAULTS, **cfg["beams"]}


def is_own_sizer(sizer, own_names):
    """True if this party is one of the mill's own sizing units. Matched as a substring both ways, so a short
    config name ('Hiba') still matches the full party name on the gate pass ('HIBA FABRICS') and vice versa."""
    s = (sizer or "").strip().lower()
    if not s:
        return False
    return any(o and (o in s or s in o) for o in (str(n).strip().lower() for n in own_names))


def learned_shrinkage(con, default_pct, lo=0.01, hi=0.20):
    """Shrinkage (warp length -> fabric) learned from genuinely finished beams: the median per contract, per
    quality (construction), and mill-wide. Beams still on a loom, and any outside the sane band `lo`..`hi`
    (a half-woven or cut beam looks like huge 'shrinkage'), are left out - they are not finished shrinkage."""
    # panels per beam, by contract (3 for a 3-panel beam): sized length x bwidth = the fabric length a full beam
    # yields. Without it, a 3-panel beam's woven meters (3x the single-panel warp length) looks like huge negative
    # "shrinkage" and is dropped, so wide cloth fell back to the mill average and its beam-end alerts fired early.
    bw = {str(c): (w or 1) for c, w in con.execute(
        "SELECT contract_no, MAX(bwidth) FROM beams WHERE bwidth IS NOT NULL GROUP BY contract_no")}
    per_c = defaultdict(list)
    for cno, length, woven in con.execute("""
            SELECT m.contract_no, COALESCE(m.length_m, s.length_m), m.woven_m FROM beam_movements m
            LEFT JOIN (SELECT beam_no, set_no, MAX(length_m) length_m FROM beam_movements WHERE move_type = 'sized_received'
                       GROUP BY beam_no, set_no) s ON s.beam_no = m.beam_no AND s.set_no = m.set_no
            WHERE m.move_type = 'from_loom_finished' AND m.woven_m > 0
              AND NOT EXISTS (SELECT 1 FROM beams b WHERE b.status = 'on_loom'
                              AND b.beam_no = m.beam_no AND b.set_no = m.set_no)"""):
        panels = bw.get(str(cno), 1) or 1
        length = (length or 0) * panels                   # BLENGTH x BWIDTH = full fabric length the beam yields
        if length and length > 0 and woven and woven > 0:
            s = 1 - woven / length
            if lo <= s <= hi:
                per_c[cno].append(s)
    by_c = {c: statistics.median(v) for c, v in per_c.items() if v}
    qual = {c: q for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    by_qvals = defaultdict(list)
    for c, v in per_c.items():
        if qual.get(c):                              # skip contracts with no construction, so they fall back to mill
            by_qvals[qual[c]].extend(v)
    by_q = {q: statistics.median(v) for q, v in by_qvals.items() if v}
    allv = [s for v in per_c.values() for s in v]
    mill = statistics.median(allv) if allv else float(default_pct) / 100
    return by_c, by_q, mill, qual


def _norm(s):
    return " ".join(str(s or "").lower().split())


def beams_on_looms(con, cfg, as_of=None):
    as_of = as_of or date.today()
    t = settings(cfg)
    by_c, by_q, mill, qual = learned_shrinkage(con, t["default_shrinkage_pct"],
                                               float(t["shrink_min_pct"]) / 100, float(t["shrink_max_pct"]) / 100)
    # Order of preference: (1) learned from this contract's finished beams, (2) learned from the same
    # construction, (3) the mill's set rate by cloth, (4) by contract number, (5) the mill-wide average.
    # So learned data is trusted first; the mill's set rates fill cloths with no finished history:
    #   [beams.shrinkage]               by construction/quality, e.g. "90x82 54/54 46" = 6
    #   [beams.shrinkage_by_contract]   by contract number, e.g. "70" = 6
    set_q = {_norm(k): float(v) / 100 for k, v in t.get("shrinkage", {}).items()}
    set_c = {str(k): float(v) / 100 for k, v in t.get("shrinkage_by_contract", {}).items()}
    d = as_of.isoformat()
    since = (as_of - timedelta(days=int(t["rate_days"]))).isoformat()
    rate = {loom: (m or 0) / int(t["rate_days"]) for loom, m in con.execute(
        "SELECT loom_code, SUM(meters) FROM loom_production WHERE prod_date > ? AND prod_date <= ? GROUP BY loom_code", (since, d))}
    # a second daily rate from the loom monitor (scoreboard), over its own shorter window, used as an independent
    # days-to-finish; where both exist the beam report takes the EARLIER finish of the two.
    mdays = int(t["monitor_rate_days"])
    msince = (as_of - timedelta(days=mdays)).isoformat()
    mon_rate = {loom: (m or 0) / mdays for loom, m in con.execute(
        "SELECT loom_code, SUM(meters) FROM loom_monitor WHERE read_date > ? AND read_date <= ? GROUP BY loom_code", (msince, d))}
    last_entry = dict(con.execute("SELECT loom_code, MAX(prod_date) FROM loom_production WHERE prod_date <= ? GROUP BY loom_code", (d,)))
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    rows = []
    for beam, set_no, cno, loom, ends, length, woven, left, mounted in con.execute("""
            SELECT beam_no, set_no, contract_no, loom_code, ends, set_length_m, woven_m, length_left_m, mounted_date
            FROM beams WHERE status = 'on_loom'"""):
        if woven is None:                            # not kept by the ERP: production on this loom with this beam since mounting
            woven = con.execute("""SELECT SUM(meters) FROM loom_production WHERE loom_code = ? AND prod_date >= COALESCE(?, '0000')
                                   AND prod_date <= ? AND (beam_no = ? OR beam_no IS NULL)""", (loom, mounted, d, beam)).fetchone()[0] or 0
        qy = qual.get(cno)
        if cno in by_c:                              # 1. learned from this cloth's own finished beams
            shrink = by_c[cno]
        elif qy in by_q:                             # 2. learned from finished beams of the same construction
            shrink = by_q[qy]
        elif _norm(qy) in set_q:                     # 3. the mill's set rate by cloth
            shrink = set_q[_norm(qy)]
        elif str(cno) in set_c:                      # 4. the mill's set rate by contract number
            shrink = set_c[str(cno)]
        else:                                        # 5. the mill-wide average
            shrink = mill
        to_come = max(0.0, (length or 0) * (1 - shrink) - woven) if length else None
        r = rate.get(loom, 0)
        mr = mon_rate.get(loom, 0)
        since_entry = (as_of - date.fromisoformat(last_entry[loom])).days if last_entry.get(loom) else 0
        days_erp = max(0.0, to_come / r - since_entry) if (to_come is not None and r > 0) else None
        days_mon = max(0.0, to_come / mr - since_entry) if (to_come is not None and mr > 0) else None
        have = [x for x in (days_erp, days_mon) if x is not None]
        days = min(have) if have else None               # the EARLIER finish of the two rates
        finish = (as_of + timedelta(days=round(days))).isoformat() if days is not None else None
        rows.append(dict(loom=loom, loom_name=names.get(loom, loom), beam=beam, set_no=set_no, contract=cno, ends=ends,
                         set_length=length, woven=woven, shrinkage=shrink, to_come=to_come, rate=r, mon_rate=mr,
                         days=days, days_erp=days_erp, days_mon=days_mon, finish=finish,
                         last_entry=last_entry.get(loom), over=bool(length and woven > 1.02 * length),
                         backup=None))
    # backups: sized beams waiting, matched on contract, else ends within tolerance; earliest-finishing looms first
    waiting = [dict(beam=b, set_no=s, contract=c, ends=e) for b, s, c, e in con.execute(
        "SELECT beam_no, set_no, contract_no, ends FROM beams WHERE status = 'sized_waiting'")]
    tol = float(t["ends_tolerance"])
    for r in sorted(rows, key=lambda r: (r["days"] is None, r["days"] or 0)):
        pick = next((w for w in waiting if w["contract"] and w["contract"] == r["contract"]), None) or next(
            (w for w in waiting if w["ends"] and r["ends"] and abs(w["ends"] - r["ends"]) <= tol), None)
        if pick:
            r["backup"] = pick
            waiting.remove(pick)
    return sorted(rows, key=lambda r: (r["days"] is None, r["days"] if r["days"] is not None else 0))


def beam_report(con, cfg, as_of=None):
    as_of = as_of or date.today()
    t = settings(cfg)
    rows = beams_on_looms(con, cfg, as_of)
    alert = [r for r in rows if r["days"] is not None and r["days"] <= float(t["warn_days"]) and not r["backup"]]
    stock = dict(con.execute("SELECT status, COUNT(*) FROM beams GROUP BY status"))
    summary = [f"{len(rows)} beams on looms; {len(alert)} finish within {t['warn_days']} days with no sized beam waiting to replace them.",
               f"Beam stock: {stock.get('sized_waiting', 0)} sized waiting, {stock.get('empty_in_mill', 0)} empty in the mill, "
               f"{stock.get('at_sizer', 0)} at sizers."]
    out = []
    for r in rows:
        b = r["backup"]
        out.append([r["loom_name"], r["beam"], r["contract"], round(r["set_length"]) if r["set_length"] else None, round(r["woven"]),
                    f"{100 * r['shrinkage']:.1f}%", round(r["to_come"]) if r["to_come"] is not None else None, round(r["rate"]),
                    round(r["days"], 1) if r["days"] is not None else None,
                    f"{b['beam']} (set {b['set_no'] or '?'})" if b else ("NONE" if r in alert else "-"),
                    "woven more than the beam holds" if r["over"] else ""])
    return Report(f"Beams on looms - {as_of:%d-%b-%Y}",
                  ["Loom", "Beam", "Contract", "Set m", "Woven m", "Shrinkage", "Fabric to come m", "m / day", "Days left",
                   "Backup beam", "Note"], out, summary)


def weekly_beam_report(con, cfg, as_of=None):
    """The weekly beam report: every beam on a loom with fabric still to come and days to finish by BOTH the ERP
    rate and the loom-monitor rate (the earlier wins), the finish date, and whether a sized backup of the same ends
    is waiting. Looms ending within their type's warn window with no backup are flagged. Summary lines give where
    all the beams are (on looms / sized waiting / empty / at sizers). Saved as a PDF and pushed weekly."""
    as_of = as_of or date.today()
    t = settings(cfg)
    rows = beams_on_looms(con, cfg, as_of)
    quality = {c: q for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    fd = lambda s: date.fromisoformat(str(s)[:10]).strftime("%d-%b") if s else "-"   # noqa: E731
    alert = [r for r in rows if r["days"] is not None and r["days"] <= warn_days_for(r["loom_name"], t) and not r["backup"] and not r["over"]]
    out = []
    for r in rows:
        b = r["backup"]
        ending = r in alert
        out.append([r["loom_name"], r["beam"], r["contract"], quality.get(r["contract"]),
                    round(r["set_length"]) if r["set_length"] else None, round(r["woven"]),
                    round(r["to_come"]) if r["to_come"] is not None else None,
                    round(r["rate"]), round(r["days_erp"], 1) if r["days_erp"] is not None else None,
                    round(r["mon_rate"]) if r["mon_rate"] else None, round(r["days_mon"], 1) if r["days_mon"] is not None else None,
                    round(r["days"], 1) if r["days"] is not None else None, fd(r["finish"]),
                    f"{b['beam']} (set {b['set_no'] or '?'})" if b else ("NONE" if ending else "-"),
                    "woven more than the beam holds" if r["over"] else ("ending soon, no backup" if ending else "")])
    stock = dict(con.execute("SELECT status, COUNT(*) FROM beams GROUP BY status"))
    at_sizer = con.execute("SELECT COALESCE(SUM(CASE WHEN beams > 0 THEN beams ELSE 0 END), 0) FROM empty_beam_stock").fetchone()[0] or 0
    summary = [f"{len(rows)} beams on looms; {len(alert)} finish within their alert window with no sized beam of the "
               f"same ends waiting to replace them.",
               f"Where the beams are: {stock.get('sized_waiting', 0)} sized and waiting, "
               f"{stock.get('empty_in_mill', 0)} empty in the mill, {round(at_sizer)} empty at sizers.",
               "Days to finish is the EARLIER of the ERP rate (last %d days of production) and the loom-monitor rate "
               "(last %d days); the finish date uses it." % (int(t["rate_days"]), int(t["monitor_rate_days"]))]
    if alert:
        summary.append("Ending soon with no backup: " + ", ".join(f"{r['loom_name']} ({r['days']:.0f}d)" for r in alert) + ".")
    return Report(f"Weekly beam report - {as_of:%d-%b-%Y}",
                  ["Loom", "Beam", "Contract", "Quality", "Set m", "Woven m", "To come m", "ERP m/day", "Days (ERP)",
                   "Mon m/day", "Days (mon)", "Days left", "Finish", "Backup beam", "Note"], out, summary)


def sizer_ledger(con, cfg, as_of=None):
    """Empty beams with each sizer counted by QUANTITY (sent vs back), with a separate flag for when the quantity
    comes back but under different beam numbers (sizers routinely swap physical beams, so an identity mismatch is
    not a shortage). Net out is the real exposure; 'Number mismatch' is just book-keeping to reconcile."""
    as_of = as_of or date.today()
    t = settings(cfg)
    own = t["own_beam_sizers"]
    # an empty beam sent to a sizer comes back EITHER empty again OR wound as a sized beam - both mean "returned".
    moves = con.execute("""SELECT party_name, beam_no, move_type, move_date FROM beam_movements
                           WHERE move_type IN ('empty_to_sizer', 'empty_from_sizer', 'sized_received') AND move_date <= ?
                           ORDER BY move_date""", (as_of.isoformat(),)).fetchall()
    sent, back = defaultdict(Counter), defaultdict(Counter)   # sizer -> Counter(beam_no)
    sent_date = defaultdict(dict)                             # sizer -> beam -> last date sent
    for sizer, beam, mt, d in moves:
        if is_own_sizer(sizer, own):
            continue
        if mt == "empty_to_sizer":
            sent[sizer][beam] += 1
            sent_date[sizer][beam] = d
        else:                                                # empty_from_sizer or sized_received
            back[sizer][beam] += 1
    limit = (as_of - timedelta(days=int(t["sizer_return_days"]))).isoformat()
    rows = []
    for sizer in sorted(set(sent) | set(back), key=str):
        s, b = sent[sizer], back[sizer]
        n_sent, n_back = sum(s.values()), sum(b.values())
        net_out = n_sent - n_back                            # the real quantity still with the sizer
        still = s - b                                        # sent, no matching number back
        unexpected = b - s                                   # returned under a number never sent
        mismatch = min(sum(still.values()), sum(unexpected.values()))   # quantity reconciles but numbers differ
        old = sorted((sent_date[sizer].get(bm, ""), bm) for bm in still.elements() if sent_date[sizer].get(bm, "") < limit)
        ask = ", ".join(f"{bm} ({d})" for d, bm in old[:12]) + (" ..." if len(old) > 12 else "")
        rows.append([sizer, n_sent, n_back, net_out, mismatch or "", ask])
    return Report(f"Empty beams with sizers - {as_of:%d-%b-%Y}",
                  ["Sizer", "Empty sent", "Back", "Net out (qty)", "Number mismatch", f"Out over {t['sizer_return_days']} days (by number)"],
                  rows,
                  ["Net out is by quantity (sent - back) - the real exposure.",
                   "'Number mismatch' = beams returned under a different number than sent: the count balances, only the identity differs."])
