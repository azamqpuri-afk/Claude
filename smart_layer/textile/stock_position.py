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
from .method import KG_PER_LB, Method


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
    as_of = as_of or date.today()
    hand, sizer, names = yarn_positions(con, cfg, as_of)
    bag_kg = float(Method(cfg["closing"]).cfg["bag_lb"]) * KG_PER_LB
    rows = []
    for key in sorted(set(hand) | set(sizer), key=lambda k: (str(k[0]), str(k[1]))):
        h, s = hand.get(key, 0.0), sizer.get(key, 0.0)
        if abs(h) < 1 and abs(s) < 1:
            continue
        rows.append([key[0], names.get(key[1], key[1]), round(h), round(h / bag_kg, 1), round(s), round(s / bag_kg, 1),
                     "negative - more issued than received; check entries" if h < -bag_kg else ""])
    return Report(f"Yarn stock - {as_of:%d-%b-%Y}",
                  ["Party", "Yarn", "In hand kg", "In hand bags", "At sizer kg", "At sizer bags", "Check"], rows,
                  ["At sizer = yarn sent to sizers, less yarn and sized beams back (includes sizing loss not yet written off)."])


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
    # group by quality + party, combining a party's same-quality contracts; list the contract numbers in brackets.
    agg = {}
    for cn, pn, q, fr, b, c, cp, m in con.execute(
            "SELECT contract_no, party_name, quality, fresh_m, b_m, c_m, cp_m, meters FROM fabric_stock").fetchall():
        a = agg.setdefault((q or "", pn or ""), [0.0, 0.0, 0.0, 0.0, 0.0, set()])
        a[0] += fr or 0; a[1] += b or 0; a[2] += c or 0; a[3] += cp or 0; a[4] += m or 0
        if cn:
            a[5].add(str(cn))
    rows, total = [], 0.0
    for (q, pn), (A, B, C, CP, T, conts) in agg.items():
        if abs(T) < 0.5:                                  # like the ERP's HAVING: drop rows that net to ~0
            continue                                      # (e.g. contracts 9/10/11 booked across numbers cancel per quality)
        total += T
        cs = ", ".join(sorted(conts, key=lambda x: (len(x), x)))
        check = "dispatched more than produced - check the ERP entry" if T < -1 else ""
        rows.append([q, pn, round(A), round(B), round(C), round(CP), round(T), cs, check])
    rows.sort(key=lambda r: (str(r[0]), -(r[6] or 0)))
    # cross-check: the ERP report's total should reconcile with opening + woven - dispatched + returns. We read the
    # report's figure, and flag the difference vs what we can recompute from production/dispatch, in brackets.
    woven = con.execute("SELECT COALESCE(SUM(meters),0) FROM loom_production").fetchone()[0]
    disp = con.execute("SELECT COALESCE(SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters),0) FROM fabric_dispatches").fetchone()[0]
    calc = woven - disp
    diff = total - calc
    note = f"  [cross-check: woven − dispatched = {calc:,.0f} m; difference {diff:+,.0f} m = opening stock + any unbooked]" if abs(diff) > 1 else ""
    return Report(f"Fabric stock - {as_of:%d-%b-%Y}",
                  ["Quality", "Party", "A m", "B m", "C m", "CP m", "Total m", "Contracts", "Check"], rows,
                  [f"Fabric in stock (from the ERP's own fabric-stock report): {total:,.0f} m." + note])
