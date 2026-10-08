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
from collections import defaultdict
from datetime import date, timedelta

from ..reports import Report

DEFAULTS = {"warn_days": 15, "urgent_days": 5, "rate_days": 30, "ends_tolerance": 5, "default_shrinkage_pct": 7,
            "sizer_return_days": 30, "own_beam_sizers": [],
            "shrink_min_pct": 1, "shrink_max_pct": 15}   # real shrinkage is ~1-12% by quality; outside = cut/on-loom beam


def settings(cfg):
    return {**DEFAULTS, **cfg["beams"]}


def learned_shrinkage(con, default_pct, lo=0.01, hi=0.20):
    """Shrinkage (warp length -> fabric) learned from genuinely finished beams: the median per contract, per
    quality (construction), and mill-wide. Beams still on a loom, and any outside the sane band `lo`..`hi`
    (a half-woven or cut beam looks like huge 'shrinkage'), are left out - they are not finished shrinkage."""
    per_c = defaultdict(list)
    for cno, length, woven in con.execute("""
            SELECT m.contract_no, COALESCE(m.length_m, s.length_m), m.woven_m FROM beam_movements m
            LEFT JOIN (SELECT beam_no, set_no, MAX(length_m) length_m FROM beam_movements WHERE move_type = 'sized_received'
                       GROUP BY beam_no, set_no) s ON s.beam_no = m.beam_no AND s.set_no = m.set_no
            WHERE m.move_type = 'from_loom_finished' AND m.woven_m > 0
              AND NOT EXISTS (SELECT 1 FROM beams b WHERE b.status = 'on_loom'
                              AND b.beam_no = m.beam_no AND b.set_no = m.set_no)"""):
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
        since_entry = (as_of - date.fromisoformat(last_entry[loom])).days if last_entry.get(loom) else 0
        days = max(0.0, to_come / r - since_entry) if (to_come is not None and r > 0) else None
        rows.append(dict(loom=loom, loom_name=names.get(loom, loom), beam=beam, set_no=set_no, contract=cno, ends=ends,
                         set_length=length, woven=woven, shrinkage=shrink, to_come=to_come, rate=r, days=days,
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


def sizer_ledger(con, cfg, as_of=None):
    """Empty beams sent to each sizer vs beams back (sized, or empty), by count; numbers sent and not seen again."""
    as_of = as_of or date.today()
    t = settings(cfg)
    skip = {s.lower() for s in t["own_beam_sizers"]}
    moves = con.execute("""SELECT party_name, beam_no, move_type, move_date FROM beam_movements
                           WHERE move_type IN ('empty_to_sizer', 'empty_from_sizer', 'sized_received') AND move_date <= ?
                           ORDER BY move_date""", (as_of.isoformat(),)).fetchall()
    sent, back = defaultdict(int), defaultdict(int)
    out_beams = defaultdict(dict)                    # sizer -> beam -> date sent (still out)
    for sizer, beam, mt, d in moves:
        if (sizer or "").lower() in skip:
            continue
        if mt == "empty_to_sizer":
            sent[sizer] += 1
            out_beams[sizer][beam] = d
        else:
            back[sizer] += 1
            out_beams[sizer].pop(beam, None)
    limit = (as_of - timedelta(days=int(t["sizer_return_days"]))).isoformat()
    rows = []
    for sizer in sorted(set(sent) | set(back), key=str):
        old = sorted((d, b) for b, d in out_beams[sizer].items() if d < limit)
        rows.append([sizer, sent[sizer], back[sizer], len(out_beams[sizer]), len(old),
                     ", ".join(f"{b} ({d})" for d, b in old[:12]) + (" ..." if len(old) > 12 else "")])
    return Report(f"Beams with sizers - {as_of:%d-%b-%Y}",
                  ["Sizer", "Empty sent", "Back", "Still out", f"Out over {t['sizer_return_days']} days", "Beams to ask about"],
                  rows, [f"Beams sent to a sizer and not back within {t['sizer_return_days']} days are listed by number."])
