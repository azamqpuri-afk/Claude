"""
48_contract_closing.py
Contract closing workbook (2026-09-26). Closing is done ONLY in this Google
Sheet; the Oracle ERP is read, never changed.

Tabs:
  Inputs          owner's closing counts + rates per lb per yarn (editable), allowance %
  All contracts   one row per contract (finished and running) with every figure;
                  formulas read Inputs, so changing a count/rate recalculates
  <nn PARTY>      one tab per contract: closing statement (or PENDING CLOSING for
                  running contracts), yarn gate passes (bags, kg) and fabric dispatches
  Weight check    our weight per meter vs the contract's, differences to decide
  Party summary   totals per party (finished contracts)
  How this works  method and caveats

Owner's rules (26-Sep-2026):
  - Finished = no production for 60+ days (cutoff below); others are PENDING CLOSING.
  - Weight per meter = threads/inch x width x 1.0936 / (840 x closing count), warp and
    weft separately; wastage is built into the owner's counts. Allowance % default 0.
  - Yarn compared in total: yarn in (IGP) + opening sized beams at go-live - yarn
    returned (OGP) vs yarn needed (all meters woven x weight per meter).
  - Gain/(loss) valued at the owner's rate per lb (contract yarn rates not used).
  - Rejection belongs to the mill.
"""

import importlib.util
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("dash", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)

OUT = HERE / "financial_statements" / "dost_contract_closing.xlsx"
KG_PER_LB = 0.45359237
CUTOFF = date(2026, 8, 13)   # finished = last production on/before this (60+ days; 65 included as over-quantity)

# Owner's closing counts and rates per lb by ERP yarn code
YARNS = {  # ycode: (name, warp count, weft count, rate per lb)
    91: ("12/1 CTN", None, 11, 275), 3: ("12/1 SLUB", None, 11, 290), 104: ("16/1 VIS SHORT SLUB", None, 14.5, 520),
    5: ("30/1 CTN", 28, 29, 440), 109: ("30/1 PC", 29, 30, 380), 7: ("30/1 STAPLE DULL", 28, None, 400),
    34: ("30/1 VISCOSE (PV)", 28, None, 400), 103: ("30/1 STAPLE (VIS)", 28, None, 400), 14: ("32/1 CTN", 30, 31, 440),
    98: ("36/2 VA", 16, 17, 350), 4: ("40/1 CTN", 38, 39, 550), 97: ("40/1 VISCOSE", 38, None, 400),
    115: ("120/1 VISCOSE", 42, 42, 800), 6: ("52/1 CTN", 53, 53, 610), 18: ("54/1 CTN", 51.5, 52.5, 610),
    2: ("60/1 CTN", 58, 59, 650), 1: ("80/1 CTN", 78, 79, 750),
    # Wahab 46 warp: party sent 42 staple (ERP codes 102 and 120); owner takes count 40 for the weight
    102: ("40/1 STAPLE", 40, None, 425), 120: ("42/1 STAPLE (40 staple)", 40, None, 425),
    122: ("20/2 VISCOSE (10s)", None, 9, 375),
}

# Contracts woven with two weft yarns (owner's Wahab closing): 64 picks, pattern 11:1 (58.667 + 5.333 picks).
# contract no: [(ycode, weft closing count, picks per inch), ...]
WEFT_MIX = {
    46: [(115, 42, 64 * 11 / 12), (122, 9, 64 / 12)],   # Wahab: 120/1 viscose @42 + 20/2 viscose (10s) @9
    43: [(2, 59, 64 * 11 / 12), (91, 11, 64 / 12)],     # Wahab cotton: 60/1 @59 + 12/1 cotton @11 (ERP says 54 picks)
}
# party-specific rates per lb (owner's Wahab closing)
PARTY_RATES = {(2, "WAHAB"): 640, (115, "WAHAB"): 750, (91, "WAHAB"): 275, (102, "WAHAB"): 425,
               (120, "WAHAB"): 425, (122, "WAHAB"): 375}


def _mix_entry(no, parts):
    """Pseudo yarn for a two-weft contract: effective count gives the same weight per meter; rate weighted by weight."""
    picks = sum(p for _, _, p in parts)
    load = sum(p / c for _, c, p in parts)
    rate = sum((p / c) / load * YARNS[y][3] for y, c, p in parts)
    name = " + ".join(f"{p:.3g} picks {YARNS[y][0]} @{c:g}" for y, c, p in parts)
    return f"MIX{no}", (f"MIX (contract {no}): {name}", None, round(picks / load, 2), round(rate))


for _no, _parts in WEFT_MIX.items():
    _k, _v = _mix_entry(_no, _parts)
    YARNS[_k] = _v
WEFT_CODE_FOR = {no: f"MIX{no}" for no in WEFT_MIX}   # contract no -> pseudo weft code
OVERRIDES = {(6, "QADRI"): (51.5, 52.5), (18, "QADRI"): (51.5, 52.5), (6, "HM FABRICS"): (53, 53),
             (18, "HM FABRICS"): (53, 53), (18, "AZAM PURI"): (51.5, 52.5)}

NAVY = PatternFill("solid", fgColor="1F3B57"); INPUT = PatternFill("solid", fgColor="FFF4C2")
SEC = PatternFill("solid", fgColor="E8EDF2"); GREEN = PatternFill("solid", fgColor="D9F2E3")
AMBER = PatternFill("solid", fgColor="FFE3B3")
WHITE = Font(bold=True, color="FFFFFF"); B = Font(bold=True)
NUM0 = '#,##0;[Red](#,##0);"â€“"'; NUM1 = '#,##0.0;[Red](#,##0.0);"â€“"'; NUM4 = '0.0000'; PCT = '0.0%;[Red]-0.0%'

CONTRACTS_SQL = """
 WITH p AS (SELECT cont_id, MIN(td) f, MAX(td) l, SUM(total_mtr) mtr, SUM(NVL(a,0)+NVL(a2,0)) ag,
                   SUM(NVL(b,0)+NVL(c,0)+NVL(cp,0)) rj, MAX(quality) q FROM dostex.wv_prod_v GROUP BY cont_id)
 SELECT c.cont_id, c.cont_no, a.an, c.cont_date, c.expiry_date, c.cont_qty, p.f, p.l, p.mtr, p.ag, p.rj, p.q,
        c.warp_yarn_code, c.warp_thread, c.warp_weight, c.weft_yarn_code, c.weft_thread, c.weft_weight, c.width,
        c.conver_per_meter
 FROM dostex.contm c JOIN p ON p.cont_id = c.cont_id LEFT JOIN dostex.ach a ON a.ac = c.party_code"""
# Weight typed on a yarn line is often wrong (0, a few kg for 40 bags, or the whole gate-pass total repeated on
# every line). Owner counts bags of 100 lb, so the typed kg is used only when it is 15-60 kg per bag.
# Cone-only lines (bags = 0): the typed kg is used only up to ~5 kg a cone (a whole set's weight is sometimes typed
# against a few leftover cones, e.g. 2,830.5 kg for 24 cones on 13-Aug-2026).
KG = """CASE WHEN NVL(d.yarn_wt,0) > 0 AND ((NVL(d.bags,0) = 0 AND (NVL(d.cones,0) = 0 OR d.yarn_wt <= d.cones*5 + 5))
                                          OR (NVL(d.bags,0) > 0 AND d.yarn_wt BETWEEN d.bags*15 AND d.bags*60 + 60))
             THEN d.yarn_wt
             ELSE NVL(d.bags,0)*45.36 + NVL(d.cones,0)*45.36/NVL(NULLIF(d.cone_pbag,0), CASE WHEN y.count > 40 THEN 40 ELSE 24 END) END"""
YARN_SQL = f"""
 SELECT d.cont_id, m.tt, SUM({KG}), SUM(CASE WHEN NVL(d.yarn_wt,0) > 0 THEN 0 ELSE 1 END), SUM(NVL(d.bags,0)), SUM(NVL(d.cones,0))
 FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no LEFT JOIN dostex.yarn y ON y.ycode = d.ycode
 WHERE m.tt IN ('IGP_YARN','OGP_YARN','OPN_SIZED_BEAMS','TO_SIZING','FROM_SIZING','YARN_TO_LOOMS','YARN_FROM_LOOMS') GROUP BY d.cont_id, m.tt"""
YARN_DETAIL_SQL = f"""
 SELECT d.cont_id, m.tt, m.tno, m.gp_no, m.td, a.an, y.yarn_desc, d.bags, d.cones, {KG},
        CASE WHEN NVL(d.yarn_wt,0) > 0 THEN 'no' ELSE 'YES' END
 FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no LEFT JOIN dostex.yarn y ON y.ycode = d.ycode
 LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2)
 WHERE m.tt IN ('IGP_YARN','OGP_YARN','OPN_SIZED_BEAMS') ORDER BY m.td, m.tno"""
FAB_SQL = """
 SELECT s.cont_id, v.trans_type, SUM(v.total_mtr), SUM(CASE WHEN NVL(v.rate,0) <> 0 THEN v.amt END),
        SUM(CASE WHEN NVL(v.rate,0) <> 0 THEN v.total_mtr END)
 FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id GROUP BY s.cont_id, v.trans_type"""
FAB_DETAIL_SQL = """
 SELECT s.cont_id, v.trans_type, v.gp_no, v.trans_no, v.trans_date, v.inv_no, v.party, v.deliver_to,
        SUM(NVL(v.a,0)+NVL(v.a2,0)), SUM(NVL(v.b,0)+NVL(v.c,0)+NVL(v.cp,0)), SUM(v.total_mtr), MAX(v.rate), SUM(v.amt)
 FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id
 GROUP BY s.cont_id, v.trans_type, v.gp_no, v.trans_no, v.trans_date, v.inv_no, v.party, v.deliver_to
 ORDER BY v.trans_date, v.trans_no"""
STOCK_SQL = """
 SELECT s.cont_id, SUM(v.total_mtr) FROM dostex.wv_fab_stock_v v
 JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id GROUP BY s.cont_id"""
PARTY_BAL_SQL = """
 SELECT a.an, SUM(l.amt) FROM dostex.acl l JOIN dostex.ach a ON a.ac = l.ac
 WHERE l.ac LIKE '3003%' OR l.ac LIKE '2001%' GROUP BY a.an"""
YARN_NAMES_SQL = "SELECT ycode, yarn_desc FROM dostex.yarn"
# Sized beams received back from the sizer. The weight on the ERP form is calculated by the software
# (usually more than actual); the owner says the weight typed in the gate pass DESCRIPTION is the
# correct one, e.g. "B#1515 30/1 PC 1796.500 KG @40+GST/-". Where the description has no kg
# (per-meter warping jobs "3200 MTR @2.25", blank templates) the ERP figure is used and marked.
SIZED_SQL = """
 SELECT m.trans_no, d.cont_id, m.tno, m.gp_no, m.td, m.tdesc, SUM(NVL(d.yarn_wt,0)), SUM(NVL(d.sized_kg,0))
 FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no
 WHERE m.tt = 'IGP_SIZED_BEAMS' GROUP BY m.trans_no, d.cont_id, m.tno, m.gp_no, m.td, m.tdesc ORDER BY m.td, m.tno"""
KG_IN_DESC = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*KGS?\b", re.I)


def desc_kg(desc):
    m = KG_IN_DESC.search(desc or "")
    return float(m.group(1).replace(",", "")) if m else None

# Summary columns: key -> header (order = column order)
COLS = [("no", "Contract"), ("party", "Party"), ("status", "Status"), ("quality", "Quality"), ("cdate", "Contract date"),
        ("first", "First production"), ("last", "Last production"), ("cqty", "Contract qty (m)"),
        ("mtr", "Meters woven"), ("ag", "A grade (m)"), ("rj", "Rejection (m) - mill's"), ("rjpct", "Rejection %"),
        ("wyarn", "Warp yarn"), ("ends", "Ends / inch"), ("wcnt", "Warp closing count"), ("wrate", "Warp rate / lb"),
        ("fyarn", "Weft yarn"), ("picks", "Picks / inch"), ("fcnt", "Weft closing count"), ("frate", "Weft rate / lb"),
        ("width", "Width (in)"), ("wlbm", "Warp lb / m"), ("flbm", "Weft lb / m"), ("lbm", "Our lb / m"),
        ("clbm", "Contract lb / m"), ("wdiff", "Diff vs contract"),
        ("wneed", "Warp needed (lb)"), ("fneed", "Weft needed (lb)"), ("need", "Yarn needed (lb)"), ("needkg", "Yarn needed (kg)"),
        ("igpb", "Yarn IGP (bags)"), ("igpc", "Yarn IGP (loose cones)"), ("igpkg", "Yarn IGP (kg)"),
        ("opnkg", "Opening sized beams at go-live (kg)"), ("retb", "Yarn returned (bags)"), ("retkg", "Yarn returned (kg)"),
        ("netkg", "Net yarn received (kg)"), ("netlb", "Net yarn received (lb)"),
        ("sizkg", "Yarn sent to sizing, net (kg)"),
        ("szact", "Sized beams received - ACTUAL kg (gate pass description)"), ("szerp", "Sized beams - ERP calculated kg"),
        ("szloss", "Sizing loss kg (sent - received actual)"), ("szlpct", "Sizing loss %"),
        ("weftkg", "Weft issued to looms, net (kg)"),
        ("usedkg", "Yarn used (sized beams actual + weft) kg"), ("leftkg", "Received minus used (kg)"),
        ("glb", "Yarn gain / (loss) (lb)"), ("gkg", "Yarn gain / (loss) (kg)"), ("gpct", "Gain / (loss) %"),
        ("grs", "Yarn gain / (loss) (Rs)"), ("rjlb", "Yarn in rejection - mill's (lb)"), ("rjrs", "Rejection yarn value (Rs)"),
        ("fout", "Fabric dispatched (m)"), ("fin", "Fabric returned (m)"), ("fstk", "Fabric in stock (m)"),
        ("invrs", "Invoiced (Rs, before GST)"), ("invm", "Invoiced meters"), ("avg", "Avg rate / m"),
        ("conv", "Contract conversion / m"), ("nowt", "Yarn lines without weight (kg from bags)"), ("flags", "Flags / questions")]
C = {k: get_column_letter(i + 1) for i, (k, _) in enumerate(COLS)}
IDX = {k: i + 1 for i, (k, _) in enumerate(COLS)}


def header(ws, row, labels, widths=None):
    for i, h in enumerate(labels, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font, c.fill = WHITE, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 42


def main():
    cur = dash.connect(dash.load_config()).cursor()
    rows = dash.rows
    contracts = sorted(rows(cur, CONTRACTS_SQL), key=lambda c: int(c[1]))
    ynames = {int(y): (d or "").strip() for y, d in rows(cur, YARN_NAMES_SQL)}
    yarn, bags = {}, {}
    for cid, tt, kg, nowt, bg, cn in rows(cur, YARN_SQL):
        yarn[(cid, tt)] = (float(kg or 0), int(nowt or 0))
        bags[(cid, tt)] = (float(bg or 0), float(cn or 0))
    ydet = {}
    for r in rows(cur, YARN_DETAIL_SQL):
        ydet.setdefault(r[0], []).append(r[1:])
    fab = {(cid, tt): (float(m or 0), float(a or 0), float(rm or 0)) for cid, tt, m, a, rm in rows(cur, FAB_SQL)}
    fdet = {}
    for r in rows(cur, FAB_DETAIL_SQL):
        fdet.setdefault(r[0], []).append(r[1:])
    stock = {cid: float(v or 0) for cid, v in rows(cur, STOCK_SQL)}
    bal = {an: float(v or 0) for an, v in rows(cur, PARTY_BAL_SQL)}
    # sized beams: actual kg from the description, split across contracts of one gate pass by ERP weight
    sized_rows = rows(cur, SIZED_SQL)
    trans_tot = {}
    for tr, cid, tno, gp, td, desc, ywt, skg in sized_rows:
        trans_tot[tr] = trans_tot.get(tr, 0.0) + float(ywt or 0)
    szact, szerp, sdet = {}, {}, {}
    for tr, cid, tno, gp, td, desc, ywt, skg in sized_rows:
        ywt = float(ywt or 0)
        dk = desc_kg(desc)
        share = ywt / trans_tot[tr] if trans_tot[tr] else 1.0
        actual = dk * share if dk else ywt
        szact[cid] = szact.get(cid, 0.0) + actual
        szerp[cid] = szerp.get(cid, 0.0) + ywt
        sdet.setdefault(cid, []).append((tno, gp, td, desc, dk * share if dk else None, ywt, float(skg or 0),
                                         "description" if dk else "ERP (no kg in description)"))

    wb = Workbook()

    # ---------------- Inputs
    inp = wb.active
    inp.title = "Inputs"
    inp["A1"] = "Contract closing - inputs (edit yellow cells; every contract recalculates)"
    inp["A1"].font = Font(bold=True, size=14, color="1F3B57")
    inp["A2"] = "Allowance % added to the calculated weight (0 = the closing counts already include wastage):"
    inp["H2"] = 0
    inp["H2"].fill, inp["H2"].number_format = INPUT, "0.0%"
    inp["A3"] = "Weight: lb per meter = threads per inch x width (inches) x 1.0936 / (840 x count), warp and weft separately."
    inp["A3"].font = Font(italic=True, size=9)
    header(inp, 5, ["Key", "Yarn", "Party (blank = all)", "ERP yarn code", "Warp closing count", "Weft closing count",
                    "Rate Rs per lb", "Note"], [14, 24, 18, 10, 12, 12, 12, 46])
    used = {c[12] for c in contracts} | {c[15] for c in contracts}
    entries = [(yc, "", w, f, rate, "") for yc, (_, w, f, rate) in YARNS.items()]
    entries += [(yc, p, w, f, YARNS[yc][3], "party-specific") for (yc, p), (w, f) in OVERRIDES.items()]
    entries += [(yc, "", None, None, None, "NEEDED - count and rate not yet given") for yc in sorted(used - set(YARNS)) if yc]
    r = 6
    for yc, p, w, f, rate, note in entries:
        inp.cell(row=r, column=1, value=f"{yc}|{p}")
        inp.cell(row=r, column=2, value=YARNS[yc][0] if yc in YARNS else ynames.get(int(yc), f"code {yc}"))
        inp.cell(row=r, column=3, value=p)
        inp.cell(row=r, column=4, value=yc)
        for col, v in ((5, w), (6, f), (7, rate)):
            c = inp.cell(row=r, column=col, value=v)
            c.fill = INPUT
        inp.cell(row=r, column=8, value=note)
        r += 1
    last_inp = r - 1
    inp.freeze_panes = "A6"

    def key_for(yc, party):
        for (y, p) in OVERRIDES:
            if y == yc and p in (party or ""):
                return f"{yc}|{p}"
        return f"{yc}|"

    def counts_for(yc, party):
        k = key_for(yc, party)
        for (y, p), (w, f) in OVERRIDES.items():
            if k == f"{y}|{p}":
                return w, f, YARNS[y][3]
        if yc in YARNS:
            _, w, f, rate = YARNS[yc]
            return w, f, rate
        return None, None, None

    calc = []

    def yname(yc):
        return YARNS[yc][0] if yc in YARNS else ynames.get(int(yc or 0), f"code {yc}")

    # ---------------- All contracts
    ws = wb.create_sheet("All contracts", 0)
    ws["A1"] = f"All contracts - closing figures (finished = no production after {CUTOFF:%d-%b-%Y}); prepared {date.today():%d-%b-%Y}"
    ws["A1"].font = Font(bold=True, size=14, color="1F3B57")
    ws["A2"] = ("Gain / (loss): + = more yarn received than needed at the closing counts (surplus); - = less (shortage). "
                "Valued at the Inputs rate per lb. ERP data read only; the closing is in this sheet.")
    ws["A2"].font = Font(italic=True, size=9)
    widths = {"party": 22, "quality": 26, "status": 16, "flags": 70, "wyarn": 16, "fyarn": 16}
    header(ws, 4, [h for _, h in COLS], [widths.get(k, 11) for k, _ in COLS])
    r0 = r = 5
    rowmap = {}
    for (cid, no, party, cdate, exp, cqty, f, l, mtr, ag, rj, q, wy, ends, cww, fy, picks, cfw, width, conv) in contracts:
        party = party or ""
        if int(no) in WEFT_MIX:
            picks = sum(p for *_, p in WEFT_MIX[int(no)])
        fy = WEFT_CODE_FOR.get(int(no), fy)
        finished = l.date() <= CUTOFF
        wk, fk = key_for(wy, party), key_for(fy, party)
        igpkg, nw1 = yarn.get((cid, "IGP_YARN"), (0, 0))
        opnkg, _ = yarn.get((cid, "OPN_SIZED_BEAMS"), (0, 0))
        retkg, nw2 = yarn.get((cid, "OGP_YARN"), (0, 0))
        out_m, amt, rated_m = fab.get((cid, "GPOUT"), (0, 0, 0))
        in_m, in_amt, _ = fab.get((cid, "GPIN"), (0, 0, 0))
        flags = []
        if not finished:
            flags.append(f"PENDING CLOSING - still running (last production {l:%d-%b-%Y})")
        if cdate and cdate.date() <= date(2023, 10, 1):
            flags.append("Started before the new ERP: yarn received in the old ERP carries no contract number - yarn balance incomplete, judge at party level")
        if wy not in YARNS or fy not in YARNS:
            flags.append("Closing count / rate missing for a yarn - fill it in on Inputs")
        if stock.get(cid, 0) > 50 and finished:
            flags.append(f"{stock.get(cid, 0):,.0f} m still in fabric stock")
        vals = {"no": int(no), "party": party, "status": "Finished" if finished else "PENDING CLOSING", "quality": q,
                "cdate": cdate, "first": f, "last": l, "cqty": cqty, "mtr": mtr, "ag": ag, "rj": rj,
                "wyarn": yname(wy), "ends": ends, "fyarn": yname(fy), "picks": picks, "width": width,
                "clbm": ((cww or 0) + (cfw or 0)) or None,
                "igpb": bags.get((cid, "IGP_YARN"), (0, 0))[0], "igpc": bags.get((cid, "IGP_YARN"), (0, 0))[1],
                "igpkg": igpkg, "opnkg": opnkg, "retb": bags.get((cid, "OGP_YARN"), (0, 0))[0], "retkg": retkg,
                "fout": out_m, "fin": in_m, "fstk": stock.get(cid, 0), "invrs": (amt or 0) - (in_amt or 0),
                "invm": rated_m, "conv": conv, "nowt": nw1 + nw2, "flags": "; ".join(flags),
                "sizkg": yarn.get((cid, "TO_SIZING"), (0, 0))[0] - yarn.get((cid, "FROM_SIZING"), (0, 0))[0],
                "weftkg": yarn.get((cid, "YARN_TO_LOOMS"), (0, 0))[0] - yarn.get((cid, "YARN_FROM_LOOMS"), (0, 0))[0],
                "szact": szact.get(cid, 0.0), "szerp": szerp.get(cid, 0.0)}
        for k, v in vals.items():
            ws.cell(row=r, column=IDX[k], value=v)
        lk = lambda k, col: f"IFERROR(INDEX(Inputs!${col}$6:${col}${last_inp},MATCH(\"{k}\",Inputs!$A$6:$A${last_inp},0)),\"\")"  # noqa: E731
        c = lambda k: f"{C[k]}{r}"  # noqa: E731
        allow = "(1+Inputs!$H$2)"
        F = {
            "rjpct": f"=IF({c('mtr')}>0,{c('rj')}/{c('mtr')},\"\")",
            "wcnt": "=" + lk(wk, "E"), "wrate": "=" + lk(wk, "G"), "fcnt": "=" + lk(fk, "F"), "frate": "=" + lk(fk, "G"),
            "wlbm": f"=IF(N({c('wcnt')})>0,{c('ends')}*{c('width')}*1.0936/(840*{c('wcnt')})*{allow},\"\")",
            "flbm": f"=IF(N({c('fcnt')})>0,{c('picks')}*{c('width')}*1.0936/(840*{c('fcnt')})*{allow},\"\")",
            "lbm": f"=IF(AND(ISNUMBER({c('wlbm')}),ISNUMBER({c('flbm')})),{c('wlbm')}+{c('flbm')},\"\")",
            "wdiff": f"=IFERROR({c('lbm')}/{c('clbm')}-1,\"\")",
            "wneed": f"=IFERROR({c('mtr')}*{c('wlbm')},\"\")", "fneed": f"=IFERROR({c('mtr')}*{c('flbm')},\"\")",
            "need": f"=IFERROR({c('wneed')}+{c('fneed')},\"\")", "needkg": f"=IFERROR({c('need')}*{KG_PER_LB},\"\")",
            "netkg": f"={c('igpkg')}+{c('opnkg')}-{c('retkg')}", "netlb": f"={c('netkg')}/{KG_PER_LB}",
            "glb": f"=IFERROR({c('netlb')}-{c('need')},\"\")", "gkg": f"=IFERROR({c('glb')}*{KG_PER_LB},\"\")",
            "gpct": f"=IFERROR({c('glb')}/{c('need')},\"\")",
            "grs": f"=IFERROR({c('glb')}*({c('wneed')}*{c('wrate')}+{c('fneed')}*{c('frate')})/{c('need')},\"\")",
            "rjlb": f"=IFERROR({c('rj')}*{c('lbm')},\"\")",
            "rjrs": f"=IFERROR({c('rjlb')}*({c('wneed')}*{c('wrate')}+{c('fneed')}*{c('frate')})/{c('need')},\"\")",
            "avg": f"=IF({c('invm')}>0,{c('invrs')}/{c('invm')},\"\")",
            "usedkg": f"={c('szact')}+{c('weftkg')}", "leftkg": f"={c('netkg')}-{c('usedkg')}",
            "szloss": f"={c('sizkg')}-{c('szact')}", "szlpct": f"=IF({c('sizkg')}>0,{c('szloss')}/{c('sizkg')},\"\")",
        }
        for k, fx in F.items():
            ws.cell(row=r, column=IDX[k], value=fx)
        for k in ("cdate", "first", "last"):
            ws.cell(row=r, column=IDX[k]).number_format = "dd-mmm-yy"
        for k in ("cqty", "mtr", "ag", "rj", "wneed", "fneed", "need", "needkg", "igpb", "igpc", "igpkg", "opnkg", "retb",
                  "retkg", "netkg", "netlb", "glb", "gkg", "grs", "rjlb", "rjrs", "fout", "fin", "fstk", "invrs", "invm",
                  "sizkg", "weftkg", "usedkg", "leftkg", "szact", "szerp", "szloss"):
            ws.cell(row=r, column=IDX[k]).number_format = NUM0
        for k in ("wlbm", "flbm", "lbm", "clbm"):
            ws.cell(row=r, column=IDX[k]).number_format = NUM4
        for k in ("rjpct", "wdiff", "gpct"):
            ws.cell(row=r, column=IDX[k]).number_format = PCT
        ws.cell(row=r, column=IDX["status"]).fill = GREEN if finished else AMBER
        for k in ("wcnt", "wrate", "fcnt", "frate"):
            ws.cell(row=r, column=IDX[k]).fill = SEC
        rowmap[cid] = (r, int(no), party, finished, l)
        # same calculation in Python, for the Alarming items tab (static, as of today's counts/rates)
        wc_, _, wr_ = counts_for(wy, party)
        _, fc_, fr_ = counts_for(fy, party)
        wl = ends * width * 1.0936 / (840 * wc_) if wc_ else None
        fl = picks * width * 1.0936 / (840 * fc_) if fc_ else None
        need = mtr * (wl + fl) if wl and fl else None
        net_kg = igpkg + opnkg - retkg
        used_kg = vals["szact"] + vals["weftkg"]
        gain = net_kg / KG_PER_LB - need if need else None
        rate = (wl * wr_ + fl * fr_) / (wl + fl) if need and wr_ and fr_ else None
        calc.append(dict(no=int(no), party=party, finished=finished, pre=bool(cdate and cdate.date() <= date(2023, 10, 1)),
                         mtr=mtr or 0, rj=rj or 0, cqty=cqty, need=need, net_kg=net_kg, used_kg=used_kg, igpkg=igpkg,
                         gain=gain, grs=gain * rate if gain is not None and rate else None, fout=out_m, fstk=stock.get(cid, 0),
                         invm=rated_m, nowt=nw1 + nw2, last=l, sizkg=vals["sizkg"], szact=vals["szact"]))
        r += 1
    last = r - 1
    ws.cell(row=r, column=2, value="TOTAL - finished contracts").font = B
    for k in ("mtr", "ag", "rj", "need", "needkg", "igpb", "igpkg", "opnkg", "retb", "retkg", "netkg", "glb", "gkg", "grs",
              "rjlb", "rjrs", "fout", "fin", "fstk", "invrs"):
        L = C[k]
        cc = ws.cell(row=r, column=IDX[k],
                     value=f"=SUMIF(${C['status']}${r0}:${C['status']}${last},\"Finished\",{L}{r0}:{L}{last})")
        cc.font, cc.number_format = B, NUM0
    ws.conditional_formatting.add(f"{C['wdiff']}{r0}:{C['wdiff']}{last}",
                                  CellIsRule(operator="notBetween", formula=["-0.02", "0.02"], fill=AMBER))
    ws.freeze_panes = "E5"
    ws.auto_filter.ref = f"A4:{C['flags']}{last}"

    # ---------------- One tab per contract
    used_names = set()
    for cid, (rr, no, party, finished, lastprod) in rowmap.items():
        short = re.sub(r"[^A-Z0-9 ]", "", party.upper()).replace("TRADERSEMB", "").split(" ")[0] or "PARTY"
        name = f"{no} {short}"[:28]
        while name in used_names:
            name += "."
        used_names.add(name)
        cs = wb.create_sheet(name)
        ref = lambda k: f"='All contracts'!{C[k]}{rr}"  # noqa: E731
        title = (f"Contract {no} - {party} - CLOSING STATEMENT" if finished
                 else f"Contract {no} - {party} - PENDING CLOSING (still running, last production {lastprod:%d-%b-%Y})")
        cs["A1"] = title
        cs["A1"].font = Font(bold=True, size=14, color="1F3B57" if finished else "9A3412")
        cs["A2"] = ref("flags")
        cs["A2"].font = Font(italic=True, size=9, color="9A3412")
        cs.column_dimensions["A"].width = 34
        cs.column_dimensions["B"].width = 18
        cs.column_dimensions["C"].width = 34
        cs.column_dimensions["D"].width = 18
        sections = [
            ("CONTRACT", [("Quality", "quality", None), ("Contract date", "cdate", "d"), ("Contract qty (m)", "cqty", 0),
                          ("First production", "first", "d"), ("Last production", "last", "d"), ("Width (in)", "width", None),
                          ("Contract conversion / m", "conv", 1)],
             [("Warp yarn", "wyarn", None), ("Ends / inch", "ends", None), ("Warp closing count", "wcnt", None),
              ("Warp rate / lb", "wrate", 0), ("Weft yarn", "fyarn", None), ("Picks / inch", "picks", None),
              ("Weft closing count", "fcnt", None), ("Weft rate / lb", "frate", 0)]),
            ("PRODUCTION", [("Meters woven", "mtr", 0), ("A grade (m)", "ag", 0), ("Rejection (m) - mill's", "rj", 0),
                            ("Rejection %", "rjpct", "p")],
             [("Our weight (lb / m)", "lbm", 4), ("Contract weight (lb / m)", "clbm", 4), ("Difference", "wdiff", "p")]),
            ("YARN", [("Yarn IGP (bags)", "igpb", 0), ("Yarn IGP (loose cones)", "igpc", 0), ("Yarn IGP (kg)", "igpkg", 0),
                      ("Opening sized beams at go-live (kg)", "opnkg", 0), ("Yarn returned (bags)", "retb", 0),
                      ("Yarn returned (kg)", "retkg", 0), ("Net yarn received (kg)", "netkg", 0), ("Net yarn received (lb)", "netlb", 0),
                      ("Yarn sent to sizing, net (kg)", "sizkg", 0), ("Weft issued to looms, net (kg)", "weftkg", 0),
                      ("Sized beams received - ACTUAL kg", "szact", 0), ("Sized beams - ERP calculated kg", "szerp", 0),
                      ("Sizing loss kg (sent - received)", "szloss", 0), ("Sizing loss %", "szlpct", "p"),
                      ("Yarn used (sized actual + weft) kg", "usedkg", 0), ("Received minus used (kg)", "leftkg", 0)],
             [("Warp needed (lb)", "wneed", 0), ("Weft needed (lb)", "fneed", 0), ("Yarn needed (lb)", "need", 0),
              ("Yarn needed (kg)", "needkg", 0), ("YARN GAIN / (LOSS) (lb)", "glb", 0), ("Yarn gain / (loss) (kg)", "gkg", 0),
              ("Gain / (loss) %", "gpct", "p"), ("YARN GAIN / (LOSS) (Rs)", "grs", 0)]),
            ("FABRIC", [("Fabric dispatched (m)", "fout", 0), ("Fabric returned (m)", "fin", 0), ("Fabric in stock (m)", "fstk", 0)],
             [("Invoiced (Rs, before GST)", "invrs", 0), ("Invoiced meters", "invm", 0), ("Avg rate / m", "avg", 2),
              ("Yarn in rejection - mill's (lb)", "rjlb", 0), ("Rejection yarn value (Rs)", "rjrs", 0)]),
        ]
        rowp = 4
        fmt = {0: NUM0, 1: NUM1, 2: '#,##0.00', 4: NUM4, "p": PCT, "d": "dd-mmm-yy", None: "General"}
        for title_s, left, right in sections:
            for col in range(1, 5):
                cs.cell(row=rowp, column=col).fill = SEC
            cs.cell(row=rowp, column=1, value=title_s).font = Font(bold=True, color="1F3B57")
            rowp += 1
            for i in range(max(len(left), len(right))):
                for (lab, k, f_), col in ((left[i] if i < len(left) else (None, None, None), 1),
                                          (right[i] if i < len(right) else (None, None, None), 3)):
                    if not lab:
                        continue
                    cs.cell(row=rowp, column=col, value=lab)
                    v = cs.cell(row=rowp, column=col + 1, value=ref(k))
                    v.number_format = fmt[f_]
                    if lab.isupper() or lab.startswith("YARN GAIN"):
                        cs.cell(row=rowp, column=col).font = B
                        v.font = B
                rowp += 1
            rowp += 1
        # yarn gate passes
        cs.cell(row=rowp, column=1, value="YARN GATE PASSES").font = Font(bold=True, color="1F3B57")
        rowp += 1
        hdr = ["Type", "Trans #", "Gate pass #", "Date", "Party / from", "Yarn", "Bags", "Loose cones", "Kg", "Kg from bags?"]
        for i, h in enumerate(hdr, 1):
            c = cs.cell(row=rowp, column=i, value=h)
            c.font, c.fill = WHITE, NAVY
        rowp += 1
        lbl = {"IGP_YARN": "Yarn in (IGP)", "OGP_YARN": "Yarn returned (OGP)", "OPN_SIZED_BEAMS": "Opening sized beams"}
        for tt, tno, gp, td, pty, yd, bg, cn, kg, est in ydet.get(cid, []):
            for i, v in enumerate([lbl[tt], tno, gp, td, pty, yd, bg, cn, kg, est], 1):
                c = cs.cell(row=rowp, column=i, value=v)
                if i == 4:
                    c.number_format = "dd-mmm-yy"
                elif i in (7, 8, 9):
                    c.number_format = NUM1
            rowp += 1
        if not ydet.get(cid):
            cs.cell(row=rowp, column=1, value="No yarn gate passes booked to this contract in the new ERP.")
            rowp += 1
        rowp += 1
        # sized beam gate passes
        cs.cell(row=rowp, column=1, value="SIZED BEAM GATE PASSES (actual kg = weight written in the description)").font = Font(bold=True, color="1F3B57")
        rowp += 1
        for i, h in enumerate(["Trans #", "Gate pass #", "Date", "Description", "Actual kg (description)", "ERP yarn kg",
                               "ERP sized kg", "Weight used from"], 1):
            c = cs.cell(row=rowp, column=i, value=h)
            c.font, c.fill = WHITE, NAVY
        rowp += 1
        for tno, gp, td, desc, dk, ywt, skg, src in sdet.get(cid, []):
            for i, v in enumerate([tno, gp, td, desc, dk, ywt, skg, src], 1):
                c = cs.cell(row=rowp, column=i, value=v)
                if i == 3:
                    c.number_format = "dd-mmm-yy"
                elif i in (5, 6, 7):
                    c.number_format = NUM1
            rowp += 1
        if not sdet.get(cid):
            cs.cell(row=rowp, column=1, value="No sized beams received for this contract.")
            rowp += 1
        rowp += 1
        # fabric gate passes
        cs.cell(row=rowp, column=1, value="FABRIC GATE PASSES").font = Font(bold=True, color="1F3B57")
        rowp += 1
        hdr = ["Type", "Gate pass #", "Trans #", "Date", "Invoice #", "Party (bill to)", "Delivered to", "A grade (m)",
               "Rejection (m)", "Total meters", "Rate / m", "Amount (Rs)"]
        for i, h in enumerate(hdr, 1):
            c = cs.cell(row=rowp, column=i, value=h)
            c.font, c.fill = WHITE, NAVY
        rowp += 1
        for tt, gp, tno, td, inv, pty, dlv, ag2, rj2, mt, rate, amt2 in fdet.get(cid, []):
            for i, v in enumerate(["Dispatched" if tt == "GPOUT" else "Returned", gp, tno, td, inv, pty, dlv, ag2, rj2, mt, rate, amt2], 1):
                c = cs.cell(row=rowp, column=i, value=v)
                if i == 4:
                    c.number_format = "dd-mmm-yy"
                elif i in (8, 9, 10, 12):
                    c.number_format = NUM0
            rowp += 1
        if not fdet.get(cid):
            cs.cell(row=rowp, column=1, value="No fabric gate passes for this contract.")
        for col, w in zip("EFGHIJKL", (22, 24, 26, 11, 11, 12, 9, 13)):
            cs.column_dimensions[col].width = max(cs.column_dimensions[col].width or 0, w)
        cs.sheet_properties.tabColor = "2E8B57" if finished else "E07B00"

    # ---------------- Alarming items (static, computed today with the current counts and rates)
    al = wb.create_sheet("Alarming items", 1)
    al["A1"] = f"Alarming items - found while closing the contracts ({date.today():%d-%b-%Y}, with the counts and rates on Inputs)"
    al["A1"].font = Font(bold=True, size=14, color="9A3412")
    al["A2"] = ("Figures here are fixed as of today; the contract tabs recalculate if counts or rates change. "
                "Contract-level yarn for Qadri and Haroon is often booked to the wrong contract - see the party lines first.")
    al["A2"].font = Font(italic=True, size=9)
    header(al, 4, ["#", "Severity", "Contract(s)", "Party", "What was found", "Rs impact (approx.)", "Suggested action"],
           [5, 10, 12, 22, 90, 16, 60])
    items = []
    fin_new = [x for x in calc if x["finished"] and not x["pre"] and x["need"]]
    # party totals (finished, new-ERP contracts)
    for p in sorted({x["party"] for x in fin_new}):
        xs = [x for x in fin_new if x["party"] == p]
        need = sum(x["need"] for x in xs); gain = sum(x["gain"] for x in xs); grs = sum(x["grs"] or 0 for x in xs)
        if need and abs(gain / need) > 0.03:
            items.append(("HIGH" if abs(grs) > 1_000_000 else "MEDIUM", ", ".join(str(x["no"]) for x in xs), p,
                          f"Party total over its {len(xs)} finished contracts: yarn {'SURPLUS' if gain > 0 else 'SHORTAGE'} of "
                          f"{abs(gain):,.0f} lb ({abs(gain) * KG_PER_LB:,.0f} kg, {gain / need:+.1%} of yarn needed at the closing counts).",
                          grs, "Agree the party's yarn account; if shortage, find where the mill's or another party's yarn went; "
                               "if surplus, confirm whether it is to be returned or kept."))
        elif need:
            items.append(("OK", ", ".join(str(x["no"]) for x in xs), p,
                          f"Party total balances: {gain:+,.0f} lb ({gain / need:+.1%}) over {len(xs)} finished contracts - "
                          "contract-level differences are mostly yarn booked to the wrong contract.", grs,
                          "Close at party level; ask staff to book yarn to the right contract in future."))
    short_parties = set()
    for p in {x["party"] for x in fin_new}:
        xs = [x for x in fin_new if x["party"] == p]
        if sum(x["gain"] for x in xs) < -0.03 * sum(x["need"] for x in xs):
            short_parties.add(p)
    for x in calc:
        n, p = str(x["no"]), x["party"]
        if x["mtr"] > 0 and x["igpkg"] == 0 and not x["pre"]:
            items.append(("HIGH", n, p, f"{x['mtr']:,.0f} m woven but NO yarn received for this contract in the ERP "
                          f"(yarn needed {x['need'] or 0:,.0f} lb). Whose yarn was used?", x["grs"],
                          "Find the yarn gate passes for this job; if the mill's or another party's yarn was used, charge it."))
        if x["used_kg"] > x["net_kg"] * 1.05 and x["used_kg"] - x["net_kg"] > 500 and x["finished"] and not x["pre"]:
            items.append(("HIGH" if p in short_parties else "MEDIUM", n, p, f"Mill used {x['used_kg']:,.0f} kg of yarn (sizing + weft) but only {x['net_kg']:,.0f} kg was received "
                          f"for this contract - {x['used_kg'] - x['net_kg']:,.0f} kg more than received."
                          + ("" if p in short_parties else " The party total is not short, so this is most likely yarn booked to another of its contracts."), None,
                          "Check whether another contract's yarn was issued to this job (then re-book) or the mill's yarn was used (then charge)."))
        if x["finished"] and x["fstk"] > 1000:
            items.append(("MEDIUM", n, p, f"Contract finished but {x['fstk']:,.0f} m of fabric still shows in stock.", None,
                          "Physically check the godown; dispatch, or correct the stock entries."))
        if x["fstk"] < -1000:
            items.append(("MEDIUM", n, p, f"Fabric stock is NEGATIVE ({x['fstk']:,.0f} m): more dispatched than produced on this contract.", None,
                          "Dispatches were booked to the wrong contract - re-book to the contract that produced the fabric."))
        if x["cqty"] and x["mtr"] > x["cqty"] * 1.2:
            items.append(("LOW", n, p, f"Produced {x['mtr']:,.0f} m against a contract quantity of {x['cqty']:,.0f} m ({x['mtr'] / x['cqty']:.1f}x).",
                          None, "Update the contract quantity or open a new contract for the extra quantity."))
        if x["mtr"] and x["rj"] / x["mtr"] > 0.15:
            items.append(("MEDIUM", n, p, f"Rejection {x['rj'] / x['mtr']:.1%} of meters woven ({x['rj']:,.0f} m) - rejection belongs to the mill.",
                          None, "Review loom/beam quality on this quality; agree rejection terms with the party."))
        if x["fout"] - x["invm"] > 500:
            items.append(("MEDIUM", n, p, f"{x['fout'] - x['invm']:,.0f} m dispatched with no rate (not invoiced).", None,
                          "Enter the rate on the gate passes so the invoice is raised."))
        if x.get("sizkg", 0) > 1000 and (x["sizkg"] - x["szact"]) / x["sizkg"] > 0.06:
            items.append(("MEDIUM", n, p, f"Sizing loss {(x['sizkg'] - x['szact']) / x['sizkg']:.1%}: {x['sizkg']:,.0f} kg yarn sent to sizing, "
                          f"{x['szact']:,.0f} kg came back in sized beams (actual weight from gate pass descriptions).", None,
                          "Check the sizer's weighment and waste; agree the allowed sizing loss with the sizer."))
        if x["nowt"]:
            items.append(("LOW", n, p, f"{x['nowt']} yarn gate pass line(s) have no weight; kg taken from bags x 45.36.", None,
                          "Enter the weighed kg on those gate passes."))
        if not x["finished"] and x["need"] and x["net_kg"] / KG_PER_LB > x["need"] * 1.5 and x["net_kg"] > 2000:
            items.append(("INFO", n, p, f"Running contract: {x['net_kg']:,.0f} kg yarn received vs {x['need'] * KG_PER_LB:,.0f} kg needed so far - normal if production continues.",
                          None, "Watch at closing."))
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3, "OK": 4}
    items.sort(key=lambda i: (order[i[0]], -(abs(i[4]) if i[4] else 0)))
    for i, (sev, cons, p, what, rs, act) in enumerate(items, 1):
        rr = i + 4
        for col, v in enumerate([i, sev, cons, p, what, round(rs) if rs else None, act], 1):
            c = al.cell(row=rr, column=col, value=v)
            c.alignment = Alignment(wrap_text=True, vertical="top")
        al.cell(row=rr, column=6).number_format = NUM0
        al.cell(row=rr, column=2).fill = {"HIGH": PatternFill("solid", fgColor="F8C9C9"), "MEDIUM": AMBER,
                                           "LOW": INPUT, "INFO": SEC, "OK": GREEN}[sev]
    al.freeze_panes = "A5"
    print("alarming items:", len(items), {s: sum(1 for i in items if i[0] == s) for s in order})

    # ---------------- Weight check
    wc = wb.create_sheet("Weight check")
    wc["A1"] = "Our weight per meter vs the contract's weight - differences over 2% highlighted; tell us what to do"
    wc["A1"].font = Font(bold=True, size=13, color="1F3B57")
    header(wc, 3, ["Contract", "Party", "Status", "Warp count", "Weft count", "Our lb / m", "Contract lb / m", "Difference",
                   "Owner's decision"], [9, 22, 16, 10, 10, 11, 12, 11, 40])
    for i in range(r0, last + 1):
        rr = i - r0 + 4
        for col, k in enumerate(("no", "party", "status", "wcnt", "fcnt", "lbm", "clbm", "wdiff"), 1):
            c = wc.cell(row=rr, column=col, value=f"='All contracts'!{C[k]}{i}")
            c.number_format = {"lbm": NUM4, "clbm": NUM4, "wdiff": PCT}.get(k, "General")
        wc.cell(row=rr, column=9).fill = INPUT
    wc.conditional_formatting.add(f"H4:H{last - r0 + 4}", CellIsRule(operator="notBetween", formula=["-0.02", "0.02"], fill=AMBER))
    wc.freeze_panes = "A4"

    # ---------------- Party summary
    ps = wb.create_sheet("Party summary")
    ps["A1"] = "By party - finished contracts only (Qadri's yarn is sometimes booked to the wrong contract, so party totals are the reliable level)"
    ps["A1"].font = Font(bold=True, size=13, color="1F3B57")
    header(ps, 3, ["Party", "Finished contracts", "Pending contracts", "Meters woven", "Yarn IGP (bags)", "Yarn IGP (kg)",
                   "Yarn needed (kg)", "Yarn gain / (loss) (kg)", "Yarn gain / (loss) (Rs)", "Rejection yarn value (Rs)",
                   "Fabric dispatched (m)", "Fabric in stock (m)", "Ledger balance today (Rs, + owes mill / - advance)"],
           [26, 10, 10, 13, 12, 13, 13, 13, 14, 14, 13, 12, 18])
    AC = "'All contracts'"
    st = f"{AC}!${C['status']}${r0}:${C['status']}${last}"
    pr = f"{AC}!${C['party']}${r0}:${C['party']}${last}"
    for i, p in enumerate(sorted({(c[2] or "") for c in contracts})):
        rr = i + 4
        ps.cell(row=rr, column=1, value=p)
        ps.cell(row=rr, column=2, value=f"=COUNTIFS({pr},A{rr},{st},\"Finished\")")
        ps.cell(row=rr, column=3, value=f"=COUNTIFS({pr},A{rr},{st},\"PENDING CLOSING\")")
        for col, k in ((4, "mtr"), (5, "igpb"), (6, "igpkg"), (7, "needkg"), (8, "gkg"), (9, "grs"), (10, "rjrs"),
                       (11, "fout"), (12, "fstk")):
            c = ps.cell(row=rr, column=col,
                        value=f"=SUMIFS({AC}!${C[k]}${r0}:${C[k]}${last},{pr},$A{rr},{st},\"Finished\")")
            c.number_format = NUM0
        ps.cell(row=rr, column=13, value=round(bal.get(p, 0))).number_format = NUM0
    ps.freeze_panes = "B4"

    # ---------------- Notes
    nt = wb.create_sheet("How this works")
    notes = [
        "HOW THE CLOSING IS CALCULATED",
        f"Finished = no production after {CUTOFF:%d-%b-%Y} (60+ days; contract 65 included as it is over its quantity). Others show PENDING CLOSING and orange tabs.",
        "Weight per meter uses the owner's closing counts (wastage built in): threads per inch x width x 1.0936 / (840 x count), warp + weft.",
        "Yarn needed = meters woven (all grades) x weight per meter. Net yarn received = yarn IGP + sized beams in the mill at go-live - yarn returned (OGP).",
        "Yarn is compared in total per contract; receipt warp/weft tags are ignored (almost everything is tagged warp).",
        "Receipts with no weight in the ERP: kg from bags/cones (45.36 kg per bag; cones per bag from the receipt, else 24 for count <= 40, 40 finer) - marked 'YES' in the gate pass lists.",
        "Gain / (loss) Rs = gain/(loss) lb x the owner's rate per lb (warp and weft rates weighted by their share of yarn needed).",
        "Rejection belongs to the mill: 'yarn in rejection' shows the yarn weight inside rejected fabric and its value.",
        "Counts (26-Sep-2026): per-yarn counts on Inputs; party-specific: 52/1 and 54/1 Qadri 51.5/52.5, 52/1 and 54/1 HM Fabrics 53/53, 54/1 Azam Puri 51.5/52.5.",
        "",
        "THINGS TO KNOW",
        "Contracts started before Oct-2023 (1-13): yarn received in the old ERP has no contract number, so their yarn balance is incomplete - judge them at party level.",
        "Qadri's yarn, beams and fabric are often booked to the wrong one of two running contracts; use the Party summary for Qadri.",
        "The contract's own weights are about 4.8% higher than ours on most contracts (a built-in 5% allowance); allowance on Inputs is 0% by default.",
        "Nothing is changed in the ERP. Staff close contracts in the ERP themselves after the figures here are agreed.",
    ]
    for i, t in enumerate(notes, 1):
        c = nt.cell(row=i, column=1, value=t)
        if t.isupper():
            c.font = Font(bold=True, color="1F3B57")
    nt.column_dimensions["A"].width = 150

    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    fin = sum(1 for v in rowmap.values() if v[3])
    print(f"contracts: {len(rowmap)} ({fin} finished, {len(rowmap) - fin} pending); sheets: {len(wb.sheetnames)}")


def upload(file_id):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_file(
        str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
    build("drive", "v3", credentials=creds).files().update(
        fileId=file_id, media_body=MediaFileUpload(
            str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
    print("uploaded ->", file_id)


if __name__ == "__main__":
    main()
    if len(sys.argv) > 2 and sys.argv[1] == "--upload":
        upload(sys.argv[2])




