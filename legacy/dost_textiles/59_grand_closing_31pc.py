"""
59_grand_closing_31pc.py - "31 PC Haroon grand closing": Haroon Fabric's 30/1 PC ("31 PC", ERP code 109) across all its
PC contracts, closed as at the run date in the owner's method (warp count 29, weft count 30, ÷800, 1 bag = 100 lb,
rejection belongs to the mill). The 122" contracts 1 and 28 (Oct-2023 to Mar-2024) are left out (owner); their yarn book
closed at ~0 bags, so the account starts clean from 1-Apr-2024 (contract 37 onward).
Upload:  python 59_grand_closing_31pc.py --upload <google sheet id>
"""
import importlib.util
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("cc", HERE / "48_contract_closing.py")
cc = importlib.util.module_from_spec(_s); _s.loader.exec_module(cc)
rows = cc.dash.rows
OUT = HERE / "financial_statements" / "31_pc_haroon_grand_closing.xlsx"
YC = 109
OLD = 1                                   # started in the old ERP (122", left out with 28)
START = "2024-04-01"                      # the 122" contracts 1 & 28 ran Oct-2023 to Mar-2024; their yarn book closed at ~0 bags
REJ_STOCK = 500                           # owner: ~500 m of the fabric in stock is rejection (mill's)
PHYSICAL = 10                             # owner's count of 31 PC bags in hand, 28-Sep-2026
TODAY = date.today()
NAVY = PatternFill("solid", fgColor="1F3B57"); INPUT = PatternFill("solid", fgColor="FFF4C2")
GREEN = PatternFill("solid", fgColor="D9F2E3"); REJ = PatternFill("solid", fgColor="F8DCDC")
W = Font(bold=True, color="FFFFFF"); B = Font(bold=True); T = Font(bold=True, size=15, color="1F3B57"); S = Font(bold=True, size=12, color="1F3B57")
NOTE = Font(italic=True, color="4B4F5A"); N0, N1, N4 = '#,##0;[Red]-#,##0', '#,##0.0;[Red]-#,##0.0', '0.0000'
BAGEQ = ("CASE WHEN NVL(d.bags,0) = 0 AND NVL(d.cones,0) = 0 THEN NVL(d.yarn_wt,0)/45.36 "
         "ELSE NVL(d.bags,0) + NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0), CASE WHEN y.count > 40 THEN 40 ELSE 24 END) END")
TT = "CASE WHEN m.tt = 'OGP_YARN' AND REGEXP_LIKE(NVL(m.tdesc,' '), '@ *[0-9]') THEN 'OGP_SALE' ELSE m.tt END"


def head(ws, r, labels):
    for i, h in enumerate(labels, 1):
        c = ws.cell(row=r, column=i, value=h); c.font, c.fill = W, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")


def main():
    cur = cc.dash.connect(cc.dash.load_config()).cursor()
    q = lambda s, **b: rows(cur, s, **b)  # noqa: E731
    con = {}                                   # PC contracts: cont_no -> (ends, picks, width, last woven)
    for r in q(cc.CONTRACTS_SQL):
        if r[15] == YC and float(r[18]) != 122:     # owner: leave out the 122" contracts (1 and 28)
            con[int(r[1])] = (float(r[13]), float(r[16]), float(r[18]), r[7])
    cl = ",".join(str(c) for c in sorted(con))
    yl = q(f"""SELECT TO_CHAR(m.td,'YYYY-MM-DD'), {TT}, m.tno, m.gp_no, c.cont_no, a.an, NVL(d.bags,0), NVL(d.cones,0), ROUND({cc.KG},1), {BAGEQ}
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE d.ycode = {YC} AND m.td >= DATE '{START}' ORDER BY m.td, m.tno""")
    fl = q(f"""SELECT TO_CHAR(v.trans_date,'YYYY-MM-DD'), v.trans_type, v.gp_no, v.inv_no, s.cont_no, v.deliver_to,
        NVL(v.a,0)+NVL(v.a2,0), NVL(v.b,0), NVL(v.c,0)+NVL(v.cp,0), v.total_mtr
        FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id
        WHERE s.cont_no IN ({cl}) AND s.cont_no <> {OLD} ORDER BY v.trans_date, v.trans_no""")
    stock = {int(n): float(t or 0) for n, t in q(f"""SELECT s.cont_no, SUM(v.total_mtr) FROM dostex.wv_fab_stock_v v
        JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no IN ({cl}) GROUP BY s.cont_no""")}
    onloom = q(f"""SELECT v.cont_no, l.lname, v.beam_code, TO_CHAR(v.td,'YYYY-MM-DD'), v.actual_length, v.beam_prod, v.blength
        FROM dostex.wv_beam_atloom_v v LEFT JOIN dostex.looms l ON l.lcode=v.lcode WHERE v.cont_no IN ({cl}) ORDER BY v.cont_no, l.lname""")
    waiting, seen = [], set()
    for r in q(f"SELECT cont_no, beam_code, TO_CHAR(td,'YYYY-MM-DD'), blength FROM dostex.beams_stock_v WHERE cont_no IN ({cl}) ORDER BY td"):
        if (r[0], r[1]) not in seen:
            seen.add((r[0], r[1])); waiting.append(r)
    woven = {int(n): float(m or 0) for n, m in q(f"SELECT cont_no, SUM(total_mtr) FROM dostex.wv_prod_v WHERE cont_no IN ({cl}) GROUP BY cont_no")}

    per = {c: dict(igp=0.0, ret=0.0, sale=0.0, siz=0.0, looms=0.0, out=0.0, rej_sold=0.0, opening=0.0) for c in con}
    other_iss = {}; igp, ret = [], []
    siz_bal = {}
    for d, tt, tno, gp, cno, an, bg, cn, kg, beq in yl:
        cno = int(cno); beq = float(beq or 0); an = (an or "").strip()
        if cno == OLD and tt in ("OPN_SIZED_BEAMS", "IGP_SIZED_BEAMS") and d <= "2023-10-01":
            per[cno]["opening"] += beq          # warp beams already made at go-live (Haroon's yarn, sized in the old ERP)
            continue
        if tt == "IGP_YARN":
            per.setdefault(cno, dict(igp=0.0, ret=0.0, sale=0.0, siz=0.0, looms=0.0, out=0.0, rej_sold=0.0, opening=0.0))["igp"] += beq
            igp.append([d, gp or tno, cno, an, float(bg), float(cn), float(kg), round(beq, 2)])
        elif tt in ("OGP_YARN", "OGP_SALE"):
            per.setdefault(cno, dict(igp=0.0, ret=0.0, sale=0.0, siz=0.0, looms=0.0, out=0.0, rej_sold=0.0, opening=0.0))["ret" if tt == "OGP_YARN" else "sale"] += beq
            ret.append([d, gp or tno, cno, an, float(bg), float(cn), float(kg), round(beq, 2), "sale" if tt == "OGP_SALE" else "return"])
        elif tt in ("TO_SIZING", "FROM_SIZING"):
            per[cno]["siz"] += beq if tt == "TO_SIZING" else -beq
            siz_bal[cno] = siz_bal.get(cno, 0.0) + (float(kg) if tt == "TO_SIZING" else -float(kg))
        elif tt == "IGP_SIZED_BEAMS":
            siz_bal[cno] = siz_bal.get(cno, 0.0) - float(kg)
        elif tt in ("YARN_TO_LOOMS", "YARN_FROM_LOOMS"):
            v = beq if tt == "YARN_TO_LOOMS" else -beq
            if cno in per:
                per[cno]["looms"] += v
            else:
                other_iss[cno] = other_iss.get(cno, 0.0) + v
    for d, tt, gp, inv, cno, dto, a, b, c, t in fl:
        cno = int(cno); sg = 1 if tt == "GPOUT" else -1; dto = (dto or "").upper()
        if "REJECTION" in dto or "ADJUS" in dto:    # stock adjustments reverse rejection sold but never produced (contract 58)
            per[cno]["rej_sold"] += sg * float(t or 0)
        else:
            per[cno]["out"] += sg * float(t or 0)
    at_sizer = sum(v for c, v in siz_bal.items() if v >= 3 * 45.36 and c != OLD)
    # shrinkage beam -> fabric on beams taken off COMPLETE (these contracts), for warp still in beams (counted as the fabric it will make)
    shr = q(f"""SELECT SUM(b.blength*NVL(mt.w,1)), SUM(pr.m) FROM dostex.wybeams b JOIN dostex.wytd d ON d.trans_no=b.trans_no AND d.sr_no=b.ref_sr
        JOIN (SELECT d2.ref_tno, d2.ref_sr FROM dostex.wprdtm m2 JOIN dostex.wprdtd d2 ON d2.trans_no=m2.trans_no WHERE m2.tt='BEAMS_FROM_LOOM' AND d2.bstatus='COMPLETE'
              GROUP BY d2.ref_tno, d2.ref_sr) o ON o.ref_tno=b.trans_no AND o.ref_sr=b.sr_no
        LEFT JOIN (SELECT d3.ref_tno, d3.ref_sr, MAX(NVL(d3.bwidth,1)) w FROM dostex.wprdtm m3 JOIN dostex.wprdtd d3 ON d3.trans_no=m3.trans_no WHERE m3.tt='BEAMS_TO_LOOM'
              GROUP BY d3.ref_tno, d3.ref_sr) mt ON mt.ref_tno=b.trans_no AND mt.ref_sr=b.sr_no
        LEFT JOIN (SELECT d4.ref_tno, d4.ref_sr, SUM(NVL(d4.a,0)+NVL(d4.a2,0)+NVL(d4.b,0)+NVL(d4.c,0)+NVL(d4.cp,0)) m FROM dostex.wprdtm m4 JOIN dostex.wprdtd d4
              ON d4.trans_no=m4.trans_no WHERE m4.tt='PRODUCTION' GROUP BY d4.ref_tno, d4.ref_sr) pr ON pr.ref_tno=b.trans_no AND pr.ref_sr=b.sr_no
        WHERE d.cont_id IN (SELECT cont_id FROM dostex.contm WHERE cont_no IN ({cl}))""")[0]
    shrink = 1 - float(shr[1]) / float(shr[0])
    # fabric woven on the looms but not yet entered: per loom, days since its last roll entry (or mounting) x its meters a day (30 days)
    unentered = sum(float(dd or 0) * float(rt or 0) for dd, rt in q(f"""SELECT TRUNC(SYSDATE) - NVL(MAX(p.td), MIN(v.td)),
            (SELECT SUM(total_mtr)/30 FROM dostex.wv_prod_v p2 WHERE p2.lcode=v.lcode AND p2.td > SYSDATE-30)
            FROM dostex.wv_beam_atloom_v v LEFT JOIN dostex.wv_prod_v p ON p.lcode=v.lcode AND p.cont_no=v.cont_no AND p.td >= v.td
            WHERE v.cont_no IN ({cl}) GROUP BY v.lcode"""))

    def wt(c, kind):                           # lb per fabric meter at the owner's counts
        ends, picks, width, _ = con[c]
        return (ends if kind == "warp" else picks) * width * 1.0936 / 800 / (29 if kind == "warp" else 30)

    book = sum(p["igp"] - p["ret"] - p["sale"] - p["siz"] - p["looms"] for p in per.values()) - sum(other_iss.values())
    wb = Workbook()
    cw = wb.active; cw.title = "Contract-wise"
    cw["A1"] = "31 PC (30/1 PC) · Haroon Fabric · contract-wise (ERP)"; cw["A1"].font = T
    head(cw, 3, ["Contract", "Width (in)", "Warp lb / m (count 29)", "Weft lb / m (count 30)", "Yarn received (bags)", "Yarn returned (bags)",
                 "Fabric woven (m)", "Fabric to Haroon, net (m)", "Rejection sold by mill (m)", "Fabric in stock (m)",
                 "Warp left on looms (m)", "Sized beams waiting (m)", "Yarn in fabric to Haroon (bags)", "Last woven",
                 "Set length of beams on looms (m)", "Fabric woven from beams on looms (m)"])
    for i, w in enumerate([9, 8, 11, 11, 12, 12, 12, 13, 12, 11, 12, 12, 13, 12, 14, 14], 1):
        cw.column_dimensions[cw.cell(row=3, column=i).column_letter].width = w
    r = 4
    order = sorted(set(per) | set(con))
    for c in order:
        p = per.get(c)
        if c not in con:
            continue
        lm = sum(float(x[6] or 0) for x in onloom if int(x[0]) == c)
        ls_ = sum(float(x[4] or 0) for x in onloom if int(x[0]) == c); lw_ = sum(float(x[5] or 0) for x in onloom if int(x[0]) == c)
        wm = sum(float(x[3] or 0) for x in waiting if int(x[0]) == c)
        vals = [c, con[c][2], round(wt(c, "warp"), 5), round(wt(c, "weft"), 5), round(p["igp"] + p["opening"], 2), round(p["ret"] + p["sale"], 2),
                round(woven.get(c, 0)), round(p["out"]), round(p["rej_sold"]),
                round(stock.get(c, 0)), round(lm), round(wm), f"=H{r}*(C{r}+D{r})/100", f"{con[c][3]:%Y-%m-%d}" if con[c][3] else "", round(ls_), round(lw_)]
        for i, v in enumerate(vals, 1):
            cw.cell(row=r, column=i, value=v).number_format = N4 if i in (3, 4) else (N1 if i in (5, 6, 13) else ("General" if i in (1, 2, 14) else N0))
        if c == OLD:
            cw.cell(row=r, column=14, value=f"go-live contract: received incl. {p['opening']:.1f} bags of opening warp beams; fabric = woven in this ERP less its rejection")
        r += 1
    tr = r
    cw.cell(row=tr, column=1, value="TOTAL").font = B
    for col in list(range(5, 14)) + [15, 16]:
        L = cw.cell(row=4, column=col).column_letter
        c_ = cw.cell(row=tr, column=col, value=f"=SUM({L}4:{L}{tr - 1})"); c_.font = B; c_.number_format = N1 if col in (5, 6, 13) else N0
    cw.cell(row=tr + 2, column=1, value="30/1 PC issued to looms under other contracts (bags): " +
            "; ".join(f"contract {k}: {v:.1f}" for k, v in sorted(other_iss.items())))
    CW = "'Contract-wise'!"

    g = wb.create_sheet("Closing", 0)
    g.column_dimensions["A"].width = 56
    for col, w in zip("BCDEFG", (15, 15, 15, 15, 16, 50)):
        g.column_dimensions[col].width = w
    g["A1"] = "31 PC GRAND CLOSING · HAROON FABRIC PVT LTD"; g["A1"].font = T
    g["A2"] = f"As at {TODAY:%d-%b-%Y} · 30/1 PC (ERP code 109) · PC contracts {', '.join(str(c) for c in sorted(con) if c != OLD)} "
    g["A2"].font = NOTE
    g["A4"] = "QUALITY"; g["B4"] = '76X66  31/31 PC  118" (122" contracts 1 & 28 left out)'; g["A4"].font = B; g["B4"].font = B
    inputs = [("Ends / inch", 76), ("Picks / inch", 66), ("Width (in)", 118), ("Warp closing count", 29),
              ("Weft closing count", 30), ("Divisor", 800), ("Lb per bag", 100), ("Warp rate (Rs / lb)", 380), ("Weft rate (Rs / lb)", 380),
              ("Rejection in fabric stock - mill's (m)", REJ_STOCK), ("Yarn in hand (bags) - physical count", PHYSICAL)]
    for i, (l, v) in enumerate(inputs, 5):
        g.cell(row=i, column=1, value=l); c_ = g.cell(row=i, column=2, value=v); c_.fill = INPUT
    # B5 ends B6 picks B7 width B8 warp cnt B9 weft cnt B10 div B11 lb/bag B12 warp rate B13 weft rate B14 rej m B15 in hand
    g["D5"] = "WARP WEIGHT (lb / m)"; g["E5"] = "=B5*B7*1.0936/B10/B8"
    g["D6"] = "WEFT WEIGHT (lb / m)"; g["E6"] = "=B6*B7*1.0936/B10/B9"
    g["D7"] = "TOTAL WEIGHT (lb / m)"; g["E7"] = "=E5+E6"
    g["D14"] = "Shrinkage beam → fabric (finished beams, ERP)"; g["E14"] = round(shrink, 4); g["E14"].fill = INPUT; g["E14"].number_format = "0.00%"
    g["A16"] = "Fabric woven on looms, not yet entered (m) - estimate"; g["B16"] = round(unentered); g["B16"].fill = INPUT; g["B16"].number_format = N0
    g["D9"] = "WARP RATE (Rs / lb)"; g["E9"] = "=B12"
    g["D10"] = "WEFT RATE (Rs / lb)"; g["E10"] = "=B13"
    for rr in (5, 6, 7):
        g[f"E{rr}"].number_format = N4; g[f"D{rr}"].font = B; g[f"E{rr}"].font = B
    g["D9"].font = B; g["D10"].font = B

    g["A17"] = "FABRIC (meters)"; g["A17"].font = S
    fab = [("Fabric delivered to Haroon (net)", f"={CW}H{tr}"),
           ("Rejection sold by the mill (Khalid, Laal Mill Chowk) - mill's", f"={CW}I{tr}"),
           ("Fabric in stock (ERP, all grades)", f"={CW}J{tr}"),
           ("  less rejection in stock - mill's", "=-B14"),
           ("Fresh fabric in stock (for Haroon)", "=B20+B21")]
    for i, (l, f_) in enumerate(fab, 18):
        g.cell(row=i, column=1, value=l); g.cell(row=i, column=2, value=f_).number_format = N0
    g["A19"].fill = REJ; g["A21"].fill = REJ
    # B18 delivered B19 rej sold B20 stock B21 -rej B22 fresh

    g["A24"] = "YARN ACCOUNT (bags of 100 lb)"; g["A24"].font = S
    head(g, 25, ["", "Warp (bags)", "Weft (bags)", "Total (bags)", "Total (lb)", "Value (Rs)", "How"])
    ya = [("Yarn received from Haroon (IGP)", None, None, f"={CW}E{tr}", "sheet 'Yarn IGP'"),
          ("Less: yarn returned / sold back", None, None, f"=-{CW}F{tr}", ""),
          ("NET YARN RECEIVED", None, None, "=D26+D27", ""),
          ("In fabric delivered to Haroon", f"=-{CW}M{tr}*E5/E7", f"=-{CW}M{tr}*E6/E7", "=B29+C29", "fabric delivered × weight"),
          ("In fresh fabric in stock", "=-$B$22*$E$5/$B$11", "=-$B$22*$E$6/$B$11", "=B30+C30", "fresh stock m × weight"),
          ("On looms: warp left on beams · weft in fabric woven but not yet entered", f"=-({CW}O{tr}*(1-$E$14)-{CW}P{tr})*$E$5/$B$11", "=-$B$16*$E$6/$B$11", "=B31+C31",
           "warp: fabric still to come = total set length × (1 - shrinkage E14) - fabric woven, × warp weight; weft: B16 (estimate) × weft weight"),
          ("Sized beams waiting", f"=-{CW}L{tr}*(1-$E$14)*$E$5/$B$11", None, "=B32", "beam meters less shrinkage × warp weight"),
          ("Yarn at sizer", f"=-{round(at_sizer / 45.36, 2)}", None, "=B33", "sent less beams back (lots of 3+ bags)"),
          ("Yarn in hand", None, None, "=-B15", "your physical count (ERP book says " + f"{book:.1f}" + " bags)"),
          ("BALANCE", None, None, "=SUM(D28:D34)", "+ = mill holds Haroon's yarn (return / pay Haroon) · − = Haroon owes the mill")]
    for i, (l, wv, fv, tv, how) in enumerate(ya, 26):
        g.cell(row=i, column=1, value=l)
        for col, v in ((2, wv), (3, fv), (4, tv)):
            if v is not None:
                g.cell(row=i, column=col, value=v).number_format = N1
        g.cell(row=i, column=5, value=f"=D{i}*$B$11").number_format = N0
        g.cell(row=i, column=6, value=f"=D{i}*$B$11*$B$12").number_format = N0
        g.cell(row=i, column=7, value=how).font = NOTE
        if l.isupper():
            for col in range(1, 7):
                g.cell(row=i, column=col).font = B
    g.cell(row=28, column=4).value = "=D26+D27"
    g["D35"] = "=D28+D29+D30+D31+D32+D33+D34"
    for col in range(1, 8):
        g.cell(row=35, column=col).fill = GREEN

    g["A37"] = "CLOSING"; g["A37"].font = S
    g["A38"] = "Balance (bags)"; g["B38"] = "=D35"
    g["A39"] = "Value (Rs)"; g["B39"] = "=ABS(F35)"
    g["A40"] = "RESULT"
    g["B40"] = ('=IF(D35>=0,"Give back to Haroon: "&TEXT(D35,"0.0")&" bags (mill holds their yarn)",'
                '"Gain - Haroon owes the mill "&TEXT(-D35,"0.0")&" bags (fabric used more than they supplied)")')
    g["A41"] = "As % of yarn received"; g["B41"] = "=D35/D28"
    for rr, fmt in ((38, N1), (39, N0), (41, "0.00%")):
        g[f"B{rr}"].number_format = fmt
    for rr in range(38, 42):
        g[f"A{rr}"].font = B; g[f"B{rr}"].font = B
    g["B40"].fill = GREEN
    g["A43"] = "WHERE ELSE THE YARN WENT (inside the balance above)"; g["A43"].font = S
    wl = [("Rejection sold by the mill (mill's)", "=B19*E7/B11"), ("Rejection in stock (mill's)", "=B14*E7/B11"),
          ("30/1 PC used on other parties' contracts (ERP)", round(sum(other_iss.values()), 2))]
    for i, (l, f_) in enumerate(wl, 44):
        g.cell(row=i, column=1, value=l); g.cell(row=i, column=2, value=f_).number_format = N1
    g["A48"] = "NOTES"; g["A48"].font = S
    for i, t in enumerate([
        "The 122\" contracts 1 and 28 (Oct-2023 to Mar-2024) are left out; their 31 PC yarn book closed at about 0 bags, so the account starts from contract 37.",
        "Warp on beams and waiting beams is counted as the fabric it will make: shrinkage (E14, from finished beams) applied to the TOTAL set length, less fabric already woven, × warp weight.",
        "Fabric on the looms not yet entered (B16) is estimated per loom: days since its last roll entry × its average meters a day; only its weft is added (its warp is in the beams). Replace with a floor count if taken.",
        "Rejection belongs to the mill: rejection sold (Khalid, Laal Mill Chowk) and ~500 m rejection in stock are not charged to Haroon.",
        "Yarn in hand is your physical count (10 bags); the ERP book shows about the same, so no yarn is missing.",
        "ERP data read only; closing made in this sheet.",
    ], 49):
        g.cell(row=i, column=1, value="• " + t).font = NOTE

    y = wb.create_sheet("Yarn IGP", 1)
    y["A1"] = "31 PC yarn received from Haroon, and returned"; y["A1"].font = T
    head(y, 3, ["Date", "IGP / GP", "Contract", "From", "Bags", "Cones", "Kg", "Bag equivalent"])
    for i, w in enumerate([12, 10, 10, 28, 9, 9, 11, 13, 10], 1):
        y.column_dimensions[y.cell(row=3, column=i).column_letter].width = w
    for x in igp:
        y.append(x)
    n1 = y.max_row
    y.append(["TOTAL RECEIVED", "", "", "", f"=SUM(E4:E{n1})", f"=SUM(F4:F{n1})", f"=SUM(G4:G{n1})", f"=SUM(H4:H{n1})"])
    y.append([]); y.append(["Returned / sold back"])
    r0 = y.max_row + 1
    for x in ret:
        y.append(x)
    y.append(["TOTAL RETURNED", "", "", "", f"=SUM(E{r0}:E{y.max_row})", "", f"=SUM(G{r0}:G{y.max_row})", f"=SUM(H{r0}:H{y.max_row})"])

    f = wb.create_sheet("Fabric OGP", 2)
    f["A1"] = "31 PC fabric gate passes (contracts 28 onward)"; f["A1"].font = T
    head(f, 3, ["Date", "Type", "GP", "Invoice", "Contract", "Delivered to", "A", "B", "Rejection C/CP", "Total meters", "Whose"])
    for i, w in enumerate([12, 8, 7, 9, 9, 36, 10, 9, 12, 12, 16], 1):
        f.column_dimensions[f.cell(row=3, column=i).column_letter].width = w
    for d, tt, gp, inv, cno, dto, a, b, c, t in fl:
        sg = 1 if tt == "GPOUT" else -1; u = (dto or "").upper()
        whose = "Stock adjustment" if "ADJUS" in u else ("Mill (rejection sold)" if "REJECTION" in u else "Haroon")
        f.append([d, "OGP" if sg == 1 else "Return", gp, inv, int(cno), (dto or "").strip(), sg * float(a), sg * float(b), sg * float(c), sg * float(t or 0), whose])
        if whose != "Haroon":
            for col in range(1, 12):
                f.cell(row=f.max_row, column=col).fill = REJ
    n2 = f.max_row
    f.append(["TOTAL", "", "", "", "", "", f"=SUM(G4:G{n2})", f"=SUM(H4:H{n2})", f"=SUM(I4:I{n2})", f"=SUM(J4:J{n2})"])
    f.append(["  to Haroon", "", "", "", "", "", "", "", "", f'=SUMIF(K4:K{n2},"Haroon",J4:J{n2})'])
    f.append(["  rejection sold by mill", "", "", "", "", "", "", "", "", f'=SUMIF(K4:K{n2},"Mill*",J4:J{n2})'])

    bm = wb.create_sheet("On looms", 3)
    bm["A1"] = "Warp on looms and sized beams waiting"; bm["A1"].font = T
    head(bm, 3, ["Contract", "Loom", "Beam", "Mounted", "Beam length (m)", "Woven (m)", "Left (m)", "Warp yarn left (bags)"])
    for rr_ in onloom:
        bm.append([int(rr_[0]), (rr_[1] or "").strip(), rr_[2], rr_[3], float(rr_[4] or 0), float(rr_[5] or 0), float(rr_[6] or 0)])
        bm.cell(row=bm.max_row, column=8, value=f"=MAX(0,E{bm.max_row}*(1-Closing!$E$14)-F{bm.max_row})*Closing!$E$5/Closing!$B$11").number_format = N1
    n3 = bm.max_row
    bm.append(["TOTAL ON LOOMS", "", "", "", f"=SUM(E4:E{n3})", f"=SUM(F4:F{n3})", f"=SUM(G4:G{n3})", f"=SUM(H4:H{n3})"])
    bm.append([]); bm.append(["Sized beams waiting"])
    r0 = bm.max_row + 1
    for w_ in waiting:
        bm.append([int(w_[0]), "", w_[1], w_[2], float(w_[3] or 0), "", float(w_[3] or 0)])
        bm.cell(row=bm.max_row, column=8, value=f"=G{bm.max_row}*(1-Closing!$E$14)*Closing!$E$5/Closing!$B$11").number_format = N1
    bm.append(["TOTAL WAITING", "", "", "", "", "", f"=SUM(G{r0}:G{bm.max_row})", f"=SUM(H{r0}:H{bm.max_row})"])

    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    print("book yarn in hand", round(book, 1), "at sizer kg", round(at_sizer), "other", other_iss)
    for c in sorted(per):
        print(c, {k: round(v, 1) for k, v in per[c].items()})


def upload(fid):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
    build("drive", "v3", credentials=creds).files().update(fileId=fid, media_body=MediaFileUpload(
        str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
    print("uploaded", fid)


if __name__ == "__main__":
    main()
    if len(sys.argv) > 2 and sys.argv[1] == "--upload":
        upload(sys.argv[2])
