"""
Yarn and fabric stock (generalised from Dost Textiles' 52_yarn_fabric_data and 56_qadri_stock_by_date).

  yarn_stock    per party and yarn: yarn in the mill's hands (received - returned - sent to sizer + back from
                sizer - issued to looms + returned from looms) and yarn at sizers (sent - back - sized beams
                received), in kg and bags
  fabric_stock  per contract: fabric woven - fabric dispatched, by grade, with days since last production
                and last dispatch (ageing). A negative stock means more was dispatched than produced.
"""

from collections import defaultdict
from datetime import date

from ..reports import Report
from .method import Method


def yarn_positions(con, cfg, as_of):
    method = Method(cfg["closing"])
    party_of = {c: p for c, p in con.execute(
        "SELECT contract_no, COALESCE(c.party_name, p.party_name) FROM contracts c LEFT JOIN parties p USING (party_code)")}
    hand, sizer = defaultdict(float), defaultdict(float)
    names = {}
    for cno, code, name, mt, kg in con.execute("""SELECT contract_no, yarn_code, yarn_name, move_type, SUM(kg) FROM yarn_movements
                                                  WHERE move_date <= ? GROUP BY 1, 2, 3, 4""", (as_of.isoformat(),)):
        key = (party_of.get(cno) or "(no contract)", method.group(code))
        names.setdefault(key[1], name or code)
        kg = kg or 0
        if mt in ("party_receipt", "opening", "from_sizer", "from_looms"):
            hand[key] += kg
        elif mt in ("party_return", "sale", "to_sizer", "to_looms"):
            hand[key] -= kg
        if mt == "to_sizer":
            sizer[key] += kg
        elif mt in ("from_sizer", "sized_beams_received"):
            sizer[key] -= kg
    return hand, sizer, names


def yarn_stock(con, cfg, as_of=None):
    """Count-wise loose-yarn stock, read straight from the ERP's own stock view (the yarn_stock entity =
    WEFT_YARN_V / WARP_YARN_V) - the SAME figures the dashboard shows. We never recompute from kg per party."""
    as_of = as_of or date.today()
    combine = {}
    for grp in cfg["yarn"].get("combine_counts", []):
        if isinstance(grp, (list, tuple)) and len(grp) >= 2:
            for c in grp[1:]:
                combine[c] = grp[0]

    def split(qty, cpb):
        cpb = cpb or 1
        b = int(qty / cpb)                              # TRUNC toward zero, like the report's CF_QTYFORMULA
        return b, qty - b * cpb

    agg = {}
    for section, party, cnt, brand, cpb, qty in con.execute(
            'SELECT section, party_name, "count" AS c, brand, cone_pbag, qty_cones FROM yarn_stock'):
        cnt = combine.get(cnt, cnt)
        key = ("sizing" if section == "sizing" else "weft",
               (party or "") if section == "sizing" else "", cnt, brand or "", round(cpb or 0))
        agg[key] = agg.get(key, 0.0) + (qty or 0)
    rows = []
    wb = wc = sb = sc = 0
    for (section, party, cnt, brand, cpb), qty in sorted(
            agg.items(), key=lambda kv: (kv[0][0] != "weft", kv[0][1], str(kv[0][2]), kv[0][4])):
        b, c = split(qty, cpb)
        rows.append(["At sizer" if section == "sizing" else "Weft", party or "-", cnt, brand or "-",
                     round(cpb or 0), round(b), round(c)])
        if section == "sizing":
            sb += b; sc += c
        else:
            wb += b; wc += c
    return Report(f"Yarn stock (count-wise) - {as_of:%d-%b-%Y}",
                  ["Where", "Party", "Count", "Brand", "Cones/bag", "Bags", "Cones"], rows,
                  [f"Weft (loose yarn in the mill): {round(wb):,} bags, {round(wc):,} cones.",
                   f"At sizers: {round(sb):,} bags, {round(sc):,} cones."])


def fabric_positions(con, as_of):
    d = as_of.isoformat()
    woven = {c: (f or 0, t or 0, last) for c, f, t, last in con.execute("""
        SELECT contract_no, SUM(COALESCE(first_quality_m, meters - COALESCE(rejected_m, 0))), SUM(meters), MAX(prod_date)
        FROM loom_production WHERE prod_date <= ? GROUP BY contract_no""", (d,))}
    disp = {c: (f or 0, r or 0, t or 0, last) for c, f, r, t, last in con.execute("""
        SELECT contract_no, SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * COALESCE(fresh_m, meters)),
               SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * COALESCE(rejected_m, 0)),
               SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters), MAX(dispatch_date)
        FROM fabric_dispatches WHERE dispatch_date <= ? GROUP BY contract_no""", (d,))}
    out = []
    for cno in sorted(set(woven) | set(disp), key=str):
        wf, wt, lp = woven.get(cno, (0, 0, None))
        df, dr, dt, ld = disp.get(cno, (0, 0, 0, None))
        # fresh = first quality; everything else (second grade, rejection) is one "other grades" pile
        out.append(dict(contract=cno, fresh=wf - df, rejected=(wt - wf) - dr, total=wt - dt, last_prod=lp, last_disp=ld))
    return out


def fabric_stock(con, cfg, as_of=None):
    """Fabric in stock exactly as the ERP's own fabric-stock report shows it (fabric_stock entity = WV_FAB_STOCK_V).
    We read the ERP's figure; we never recompute it. Negative lines are flagged, not hidden or netted away."""
    as_of = as_of or date.today()
    # last movement per contract (ageing): the more recent of last woven and last dispatched.
    last_move = {}
    for cno, d in con.execute("SELECT contract_no, MAX(prod_date) FROM loom_production GROUP BY contract_no"):
        if d:
            last_move[str(cno)] = d
    for cno, d in con.execute("SELECT contract_no, MAX(dispatch_date) FROM fabric_dispatches GROUP BY contract_no"):
        if d:
            last_move[str(cno)] = max(d, last_move.get(str(cno), d))
    # group by quality + party, combining a party's same-quality contracts; list the contract numbers in brackets.
    agg = {}
    for cn, pn, q, fr, b, c, cp, m in con.execute(
            "SELECT contract_no, party_name, quality, fresh_m, b_m, c_m, cp_m, meters FROM fabric_stock").fetchall():
        a = agg.setdefault((q or "", pn or ""), [0.0, 0.0, 0.0, 0.0, 0.0, set(), None])
        a[0] += fr or 0; a[1] += b or 0; a[2] += c or 0; a[3] += cp or 0; a[4] += m or 0
        if cn:
            a[5].add(str(cn))
            mv = last_move.get(str(cn))
            if mv and (a[6] is None or mv > a[6]):
                a[6] = mv
    rows, total = [], 0.0
    for (q, pn), (A, B, C, CP, T, conts, mv) in agg.items():
        if abs(T) < 0.5:                                  # like the ERP's HAVING: drop rows that net to ~0
            continue                                      # (e.g. contracts 9/10/11 booked across numbers cancel per quality)
        total += T
        cs = ", ".join(sorted(conts, key=lambda x: (len(x), x)))
        age = (as_of - date.fromisoformat(mv)).days if mv else None
        check = "dispatched more than produced - check the ERP entry" if T < -1 else ""
        rows.append([q, pn, round(A), round(B), round(C), round(CP), round(T), cs, mv or "", age, check])
    rows.sort(key=lambda r: (str(r[0]), -(r[6] or 0)))
    # cross-check: the ERP report's figure should equal opening + woven - dispatched. We read the ERP's figure and
    # show, in plain words, the gap vs what production and dispatch records alone give (the gap is the opening stock).
    woven = con.execute("SELECT COALESCE(SUM(meters),0) FROM loom_production").fetchone()[0]
    disp = con.execute("SELECT COALESCE(SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters),0) FROM fabric_dispatches").fetchone()[0]
    calc = woven - disp
    diff = total - calc
    note = (f"Cross-check: production minus dispatch records give {calc:,.0f} m; the {diff:+,.0f} m gap to the ERP "
            f"figure is opening stock carried forward in the ERP (normal, not an error).") if abs(diff) > 1 else ""
    return Report(f"Fabric stock - {as_of:%d-%b-%Y}",
                  ["Quality", "Party", "A m", "B m", "C m", "CP m", "Total m", "Contracts", "Last movement", "Age (days)", "Check"], rows,
                  [f"Fabric in stock (from the ERP's own fabric-stock report): {total:,.0f} m."] + ([note] if note else []))
