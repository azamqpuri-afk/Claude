"""
Sizing, empty-beam and weft reports (0.15), generic and read-only.

  sizer_ledger_full   per sizer: yarn sent (kg), yarn/sized beams back, empty beams sent/back (numbers), gullay
                      returned (manual), and the running yarn & empty-beam balances. Live-formula Excel + PDF.
  empty_beams         empty beams in the mill and at each sizer, with numbers, quantity and SIZE, totals by size.
                      Optional opening list ([beams] empty_opening CSV: as_of, beam_no, size) rolled forward.
  sized_yarn_closing  the owner's sized-yarn closing-count per set (6 steps), live-formula Excel.
  weft_consumption    expected weft (owner-set count per quality) vs weft issued, variance per quality.
"""

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .. import manual
from ..reports import Report
from ..reports.xlsx import Fx, Inp
from .beams import is_own_sizer, settings
from .method import Method


# ---- #17 sizer ledger -----------------------------------------------------------------------------------------
def sizer_ledger_full(con, cfg, as_of=None):
    as_of = as_of or date.today()
    own = settings(cfg)["own_beam_sizers"]
    d = as_of.isoformat()
    ysent, yback = defaultdict(float), defaultdict(float)
    for party, mt, kg in con.execute("""SELECT party_name, move_type, SUM(kg) FROM yarn_movements
            WHERE move_date <= ? AND move_type IN ('to_sizer','from_sizer','sized_beams_received') GROUP BY party_name, move_type""", (d,)):
        if party and not is_own_sizer(party, own):
            (ysent if mt == "to_sizer" else yback)[party] += kg or 0
    esent, eback = defaultdict(int), defaultdict(int)
    for party, mt, n in con.execute("""SELECT party_name, move_type, COUNT(*) FROM beam_movements
            WHERE move_date <= ? AND move_type IN ('empty_to_sizer','empty_from_sizer','sized_received') GROUP BY party_name, move_type""", (d,)):
        if party and not is_own_sizer(party, own):
            (esent if mt == "empty_to_sizer" else eback)[party] += n or 0
    gullay = manual.latest(con, "sizer", "gullay_kg")
    sizers = sorted(set(ysent) | set(yback) | set(esent) | set(eback), key=str)
    rows, xrows = [], []
    for s in sizers:
        ys, yb = round(ysent.get(s, 0)), round(yback.get(s, 0))
        g = round((gullay.get(s, {}) or {}).get("gullay_kg", 0) or 0)
        es, eb = esent.get(s, 0), eback.get(s, 0)
        rows.append([s, ys, yb, g, ys - yb - g, es, eb, es - eb])
        xrows.append([s, Inp(ys), Inp(yb), Inp(g), Fx("=B{r}-C{r}-D{r}"), Inp(es), Inp(eb), Fx("=F{r}-G{r}")])
    cols = ["Sizer", "Yarn sent kg", "Yarn back kg", "Gullay kg", "Yarn balance kg", "Empty sent", "Empty back", "Empty balance"]
    summary = [f"{len(rows)} sizers. Yarn balance = sent − back − gullay; empty balance = sent − back.",
               "Gullay kg is a manual input (the yarn returned on baby cones)."]
    rep = Report(f"Sizer ledger - {as_of:%d-%b-%Y}", cols, rows, summary)
    rep.xlsx_sheets = [{"name": "Sizer ledger", "title": rep.title, "note": summary, "headers": cols, "rows": xrows}]
    return rep


# ---- #7 empty beams with sizes -------------------------------------------------------------------------------
def _opening(cfg):
    """Optional opening list of empty beams: [beams] empty_opening = path to a CSV of as_of, beam_no, size.
    Returns ({beam_no: size}, as_of_date or None). Missing/unreadable file -> ({}, None)."""
    path = (cfg["beams"] or {}).get("empty_opening")
    if not path:
        return {}, None
    p = cfg.path(path)
    if not Path(p).exists():
        return {}, None
    sizes, as_of = {}, None
    try:
        from ..config import read_text_any
        for row in csv.DictReader(read_text_any(p).splitlines()):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            bn = row.get("beam_no") or row.get("beam")
            if bn:
                sizes[bn] = row.get("size") or ""
                as_of = as_of or row.get("as_of") or row.get("date")
    except Exception:
        return {}, None
    return sizes, as_of


def empty_beams(con, cfg, as_of=None):
    """Empty beams now, in the mill and at each sizer, with beam numbers, quantity and SIZE; totals by size.
    Opening (optional CSV) is rolled forward: empty in mill = opening + off looms − to sizers + back from sizers.
    Beam sizes come from the opening list, else a [beams] beam_sizes map, else '?'. Flags a beam seen in two places."""
    as_of = as_of or date.today()
    t = settings(cfg)
    alias = {str(k): str(v) for k, v in (t.get("beam_aliases") or {}).items()}
    ignore = {str(b) for b in (t.get("ignore_beams") or [])}
    opening, op_date = _opening(cfg)
    size_map = {str(k): str(v) for k, v in (t.get("beam_sizes") or {}).items()}
    size_map.update({str(k): str(v) for k, v in opening.items() if v})

    def size_of(b):
        return size_map.get(str(b)) or "?"
    d = as_of.isoformat()
    # the beam snapshot is authoritative for beams currently on a loom / sized-waiting (not empty)
    held = {alias.get(str(b), str(b)) for (b,) in con.execute(
        "SELECT beam_no FROM beams WHERE beam_no IS NOT NULL AND status IN ('on_loom','sized_waiting')")}
    # at each sizer: empty beams sent minus back, by beam number
    at_sizer = defaultdict(set)                           # sizer -> {beam_no}
    sent_back = defaultdict(lambda: defaultdict(int))     # sizer -> beam -> net (sent +1, back -1)
    for party, beam, mt in con.execute("""SELECT party_name, beam_no, move_type FROM beam_movements
            WHERE move_date <= ? AND beam_no IS NOT NULL AND move_type IN ('empty_to_sizer','empty_from_sizer','sized_received')""", (d,)):
        b = alias.get(str(beam), str(beam))
        if b in ignore:
            continue
        sent_back[party][b] += 1 if mt == "empty_to_sizer" else -1
    # empty in the mill: opening ∪ off-loom, minus those sent to a sizer since / re-held
    latest = defaultdict(dict)
    for beam, mt, dd in con.execute("""SELECT beam_no, move_type, move_date FROM beam_movements
            WHERE beam_no IS NOT NULL AND move_type IN ('to_loom','from_loom_finished','from_loom_cut','empty_to_sizer')
            ORDER BY move_date, rowid""", ()):
        latest[alias.get(str(beam), str(beam))][mt] = dd
    floor = (as_of - timedelta(days=int(t.get("empty_age_out_days", 120)))).isoformat()
    in_mill = set(b for b in opening) if opening else set()
    for b, mv in latest.items():
        off = max(mv.get("from_loom_finished", ""), mv.get("from_loom_cut", ""))
        if off and off > mv.get("empty_to_sizer", "") and off > mv.get("to_loom", "") and off >= floor:
            in_mill.add(b)
    in_mill = {b for b in in_mill if b not in ignore and b not in held}
    # resolve at-sizer sets
    for sizer, bmap in sent_back.items():
        for b, net in bmap.items():
            if net > 0 and b not in held:
                at_sizer[sizer].add(b)
    # rows + size totals + double-location flag
    rows = []
    by_size = defaultdict(int)
    loc = defaultdict(list)                               # beam -> [locations]
    for b in sorted(in_mill):
        rows.append(["Mill", b, size_of(b)]); by_size[size_of(b)] += 1; loc[b].append("Mill")
    for sizer in sorted(at_sizer):
        for b in sorted(at_sizer[sizer]):
            rows.append([sizer, b, size_of(b)]); by_size[size_of(b)] += 1; loc[b].append(sizer)
    dup = [b for b, ls in loc.items() if len(ls) > 1]
    for r in rows:
        r.append("in two places" if r[1] in dup else "")
    summary = [f"{len(rows)} empty beams: {len(in_mill)} in the mill, {sum(len(v) for v in at_sizer.values())} at sizers.",
               "By size: " + (", ".join(f"{n}×{s}\"" for s, n in sorted(by_size.items(), key=lambda x: -x[1])) or "—")
               + (f". Opening rolled from {op_date}." if op_date else ". No opening list configured; sizes '?' until one is added.")]
    if dup:
        summary.append(f"{len(dup)} beam number(s) show in two places at once - reconcile: {', '.join(dup[:10])}.")
    return Report(f"Empty beams - {as_of:%d-%b-%Y}", ["Where", "Beam no.", "Size", "Note"], rows, summary)


# ---- #22 weft consumption by owner-given counts --------------------------------------------------------------
def weft_consumption(con, cfg, d0=None, d1=None):
    """Expected weft (owner-set weft count per quality) vs weft actually issued, per quality, as a variance.
    The owner sets a weft count per quality on the dashboard (stored in manual inputs, scope 'quality'
    field 'weft_count'); without one, the contract's own weft count is used. Read-only."""
    d1 = d1 or date.today()
    d0 = d0 or (d1 - timedelta(days=30))
    method = Method(cfg["closing"])
    owner_counts = manual.latest(con, "quality", "weft_count")    # {quality: {weft_count: v}}
    # production by (quality, contract) over the period, with construction
    rows = []
    con_meta = {c: (q, ppi, width, fy, fc) for c, q, ppi, width, fy, fc in con.execute(
        "SELECT contract_no, quality, picks_per_inch, width_in, weft_yarn, weft_count FROM contracts")}
    prod = con.execute("""SELECT contract_no, quality, SUM(meters) FROM loom_production
                          WHERE prod_date BETWEEN ? AND ? GROUP BY contract_no, quality""", (d0.isoformat(), d1.isoformat())).fetchall()
    cur = cfg["company"]["currency"]
    agg = defaultdict(lambda: [0.0, 0.0])                 # quality -> [expected_kg, meters]
    for cno, pq, m in prod:
        q, ppi, width, fy, fc = con_meta.get(cno, (pq, None, None, None, None))
        q = q or pq or "?"
        oc = (owner_counts.get(q, {}) or {}).get("weft_count")
        count = oc or fc
        if not (ppi and width and count):
            continue
        lbm = method.weight("weft", q, ppi, width, count)
        if lbm:
            agg[q][0] += (m or 0) * lbm / 2.2046
            agg[q][1] += m or 0
    # weft issued (to looms) by nothing-quality: yarn_movements to_looms kg in the period, total (cannot split by
    # quality reliably), so we compare the TOTAL expected vs issued and list per-quality expected.
    issued = con.execute("""SELECT SUM(kg) FROM yarn_movements WHERE move_type = 'to_looms' AND move_date BETWEEN ? AND ?""",
                         (d0.isoformat(), d1.isoformat())).fetchone()[0] or 0
    exp_total = 0.0
    for q, (ekg, m) in sorted(agg.items(), key=lambda x: -x[1][0]):
        exp_total += ekg
        rows.append([q, round(m), round(ekg), ("owner" if (owner_counts.get(q, {}) or {}).get("weft_count") else "contract")])
    rows.append(["TOTAL", round(sum(v[1] for v in agg.values())), round(exp_total), ""])
    summary = [f"{d0:%d-%b-%Y} to {d1:%d-%b-%Y}: expected weft {exp_total:,.0f} kg vs issued to looms {issued:,.0f} kg "
               f"(variance {issued - exp_total:+,.0f} kg).",
               "Expected uses the owner's weft count per quality where set, else the contract's; set counts on the dashboard."]
    return Report(f"Weft consumption vs issued - {d0:%d-%b-%Y} to {d1:%d-%b-%Y}",
                  ["Quality", "Woven m", "Expected weft kg", "Count from"], rows, summary)


# ---- #12 sized-yarn closing count ----------------------------------------------------------------------------
def _sets(con):
    """One row per sized set from the beam snapshot: set_no, contract, quality, sizer, total ends, widths,
    set length (m). Sets with no ends or length are skipped (nothing to compute)."""
    qual = {c: q for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    seen, out = set(), []
    for setno, cno, sizer, ends, length, bwidth in con.execute(
            """SELECT set_no, contract_no, sizer_name, MAX(ends), MAX(set_length_m), MAX(COALESCE(bwidth,1))
               FROM beams WHERE set_no IS NOT NULL GROUP BY set_no, contract_no, sizer_name"""):
        if not setno or setno in seen or not ends or not length:
            continue
        seen.add(setno)
        out.append(dict(set_no=setno, contract=cno, quality=qual.get(cno), sizer=sizer,
                        ends=ends, widths=(bwidth or 1), length=length))
    return out


def sized_yarn_closing(con, cfg, as_of=None):
    """The owner's sized-yarn closing count per set (6 steps), with a live-formula workbook. Manual inputs
    (sized kg, gullay kg, gullay cones, rewind kg) are shaded; every result is a formula. Constants: the
    gullay allowance (g/cone), the pick factor (1.0936) and the divisor are from config and inlined into the
    formulas so the sheet is self-contained."""
    as_of = as_of or date.today()
    s = cfg["sizing"]
    g = float(s.get("gullay_g", 55))
    pf = float(s.get("pick_factor", 1.0936))
    shrink = float(s.get("shrink_pct", 7))
    divisor = float((cfg["closing"] or {}).get("divisor", 840) or 840)
    mset = manual.latest(con, "set")                     # {set_no: {sized_kg, gullay_kg, gullay_cones, rewind_kg}}
    sets = _sets(con)
    rows, xrows = [], []
    for st in sets:
        man = mset.get(str(st["set_no"]), {})
        sized = man.get("sized_kg") or 0
        gk = man.get("gullay_kg") or 0
        gc = man.get("gullay_cones") or 0
        rw = man.get("rewind_kg") or 0
        metres = st["length"] * st["widths"] * (1 - shrink / 100)
        net_gullay = gk - (g * gc / 1000)
        net_sized = sized - net_gullay
        kgm = net_sized / metres if metres else 0
        lbm = kgm / 2.2046 if kgm else 0
        cc = (st["ends"] / st["widths"]) * pf / divisor / lbm if lbm else 0
        rows.append([st["set_no"], st["contract"], st["quality"], st["sizer"], round(st["ends"]), st["widths"],
                     round(st["length"]), round(metres), round(sized), round(net_gullay), round(net_sized),
                     round(kgm, 4), round(lbm, 5), round(cc, 2)])
        # xlsx columns: A set B contract C quality D sizer E ends F widths G setm H shrink%
        #               I metres J sized(Inp) K gullaykg(Inp) L gullaycones(Inp) M rewind(Inp)
        #               N netgullay O netsized P kg/m Q lbs/m R closing count S rewind check
        xrows.append([st["set_no"], st["contract"], st["quality"], st["sizer"], round(st["ends"]), st["widths"],
                      round(st["length"]), Inp(shrink),
                      Fx("=G{r}*F{r}*(1-H{r}/100)"), Inp(round(sized)), Inp(round(gk)), Inp(round(gc)), Inp(round(rw)),
                      Fx(f"=K{{r}}-({g}*L{{r}}/1000)"), Fx("=J{r}-N{r}"), Fx("=O{r}/I{r}"), Fx("=P{r}/2.2046"),
                      Fx(f"=(E{{r}}/F{{r}})*{pf}/{divisor}/Q{{r}}"), Fx("=M{r}-N{r}")])
    headers = ["Set", "Contract", "Quality", "Sizer", "Ends", "Widths", "Set m", "Shrink %", "Metres after shrink",
               "Sized kg", "Gullay kg", "Gullay cones", "Rewind kg", "Net gullay kg", "Net sized kg", "kg/m",
               "lbs/m", "Closing count", "Rewind check"]
    disp_cols = ["Set", "Contract", "Quality", "Sizer", "Ends", "Widths", "Set m", "Metres", "Sized kg",
                 "Net gullay kg", "Net sized kg", "kg/m", "lbs/m", "Closing count"]
    # view (b): by count (from closing count), avg/min/max
    bycount = defaultdict(list)
    for r in rows:
        if r[13]:
            bycount[round(r[13])].append(r[13])
    byc_rows = [[c, len(v), round(sum(v) / len(v), 2), round(min(v), 2), round(max(v), 2)]
                for c, v in sorted(bycount.items())]
    # view (c): sizer comparison (avg closing count per sizer), ranked
    bysizer = defaultdict(list)
    for r in rows:
        if r[13]:
            bysizer[r[3] or "?"].append(r[13])
    bys_rows = sorted(([s2, len(v), round(sum(v) / len(v), 2), round(min(v), 2), round(max(v), 2)]
                       for s2, v in bysizer.items()), key=lambda x: x[2])
    summary = [f"{len(rows)} sized sets. Closing count = (ends ÷ widths) × {pf} ÷ {divisor:g} ÷ (lbs/m); "
               f"net sized = sized − (gullay kg − {g:g} g × gullay cones). Shaded cells in Excel are editable inputs.",
               "Views: all sets; by count (avg/min/max); sizer comparison (which sizer gives back more yarn). "
               "Verify against one real set with the owner."]
    rep = Report(f"Sized-yarn closing count - {as_of:%d-%b-%Y}", disp_cols,
                 [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13]] for r in rows],
                 summary)
    rep.xlsx_sheets = [
        {"name": "All sets", "title": rep.title, "note": summary, "headers": headers, "rows": xrows},
        {"name": "By count", "title": "By yarn count", "headers": ["Count", "Sets", "Avg", "Min", "Max"], "rows": byc_rows},
        {"name": "Sizer comparison", "title": "Sizer comparison (avg closing count, lower = gives back more yarn)",
         "headers": ["Sizer", "Sets", "Avg count", "Min", "Max"], "rows": bys_rows},
    ]
    return rep
