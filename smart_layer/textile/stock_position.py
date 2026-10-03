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
    as_of = as_of or date.today()
    party_of = dict(con.execute(
        "SELECT contract_no, COALESCE(c.party_name, p.party_name) FROM contracts c LEFT JOIN parties p USING (party_code)"))
    rows = []
    for f in fabric_positions(con, as_of):
        if abs(f["total"]) < 1 and abs(f["fresh"]) < 1:
            continue
        age = (as_of - date.fromisoformat(f["last_prod"])).days if f["last_prod"] else None
        check = []
        if f["fresh"] < -1 or f["rejected"] < -1:
            check.append("dispatched more than produced in a grade - grade keyed wrong on a gate pass or production")
        if age is not None and age > 60 and f["fresh"] > 500:
            check.append("fabric lying more than 60 days after the last production")
        rows.append([f["contract"], party_of.get(f["contract"], ""), round(f["fresh"]), round(f["rejected"]), round(f["total"]),
                     f["last_prod"], f["last_disp"], age, "; ".join(check)])
    rows.sort(key=lambda r: -(r[4] or 0))
    return Report(f"Fabric stock - {as_of:%d-%b-%Y}",
                  ["Contract", "Party", "Fresh m", "Other grades m", "Total m", "Last production", "Last dispatch",
                   "Days since production", "Check"], rows,
                  [f"Fabric in the mill: {sum(r[4] for r in rows if r[4] > 0):,.0f} m (woven - dispatched + returned)."])
