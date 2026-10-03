"""
55_grand_closing_52ctn.py - "52 ctn grand closing": Qadri Traders' 52/1 & 54/1 cotton (same yarn, ERP codes 6 and 18)
across contracts 38, 50, 60, 68, 70, closed as at the run date in the owner's method (÷800, closing counts, 1 bag = 100 lb,
rejection belongs to the mill), laid out like the owner's own closing sheets. ERP read only; owner inputs are yellow.
Upload:  python 55_grand_closing_52ctn.py --upload <google sheet id>
"""
import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("cc", HERE / "48_contract_closing.py")
cc = importlib.util.module_from_spec(_s); _s.loader.exec_module(cc)
rows = cc.dash.rows
OUT = HERE / "financial_statements" / "52_ctn_grand_closing.xlsx"
CONTRACTS = [38, 50, 60, 68, 70]
AZAM_PURI = 52
SIZING_MIN_KG = 3 * 45.36          # owner: only real lots at the sizer count (leftover cones don't)
SIZING_DAYS = 60                    # older balances are sizing loss on finished sets, not yarn lying at the sizer
TODAY = date.today()
# owner's physical count of yarn bags in hand; later receipts / issues are rolled forward from the ERP
# (the 130-bag IGP 595 of 29-Sep-2026 came after the 28-Sep count of 68)
COUNT_BAGS, COUNT_DATE = 68, date(2026, 9, 28)
# 60/1 cotton is a separate weft (owner, 30-Sep-2026): contract 38 OGPs 361-381 (Apr-Jun 2025) were woven with 60/1 weft
# from the 14-bag transfer of contract 40 - kept out of the 54/1 account, closed at count 59 (weft) / 58 (warp)
TRANSFER60_BAGS, WEFT60_COUNT, WARP60_COUNT = 14, 59, 58   # the fabric: ERP sub-contract quality '90X82 54/60 (CTN-38)'
NAVY = PatternFill("solid", fgColor="1F3B57"); INPUT = PatternFill("solid", fgColor="FFF4C2")
GREEN = PatternFill("solid", fgColor="D9F2E3"); SEC = PatternFill("solid", fgColor="E8EDF2"); REJ = PatternFill("solid", fgColor="F8DCDC")
W = Font(bold=True, color="FFFFFF"); B = Font(bold=True); T = Font(bold=True, size=15, color="1F3B57"); S = Font(bold=True, size=12, color="1F3B57")
N0, N1, N4 = '#,##0;[Red]-#,##0', '#,##0.0;[Red]-#,##0.0', '0.0000'
NOTE = Font(italic=True, color="4B4F5A")


def head(ws, r, labels, col0=1):
    for i, h in enumerate(labels, col0):
        c = ws.cell(row=r, column=i, value=h)
        c.font, c.fill = W, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center")


def since_count(cur):
    """net yarn-in-hand movement (bags + cones as part-bags) of the cotton contracts after the physical count date"""
    cl = ",".join(str(c) for c in CONTRACTS + [AZAM_PURI])
    v = rows(cur, f"""SELECT SUM(CASE WHEN m.tt IN ('IGP_YARN','FROM_SIZING','YARN_FROM_LOOMS') THEN 1 WHEN m.tt IN ('OGP_YARN','TO_SIZING','YARN_TO_LOOMS') THEN -1 ELSE 0 END
                  * (NVL(d.bags,0) + NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0),40)))
                  FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no JOIN dostex.contm c ON c.cont_id=d.cont_id
                  WHERE d.ycode IN (6,18) AND c.cont_no IN ({cl}) AND TRUNC(m.td) > DATE '{COUNT_DATE:%Y-%m-%d}'""")[0][0]
    return float(v or 0)


def main():
    cur = cc.dash.connect(cc.dash.load_config()).cursor()
    cl = ",".join(str(c) for c in CONTRACTS + [AZAM_PURI])
    q = lambda s, **b: rows(cur, s, **b)  # noqa: E731
    TT = "CASE WHEN m.tt = 'OGP_YARN' AND REGEXP_LIKE(m.tdesc, '@ *[0-9]') THEN 'OGP_SALE' ELSE m.tt END"
    yl = q(f"""SELECT TO_CHAR(m.td,'YYYY-MM-DD'), {TT}, m.tno, m.gp_no, c.cont_no, a.an, y.yarn_desc, NVL(d.bags,0), NVL(d.cones,0), ROUND({cc.KG},1), m.tdesc
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE m.tt IN ('IGP_YARN','OGP_YARN','TO_SIZING','FROM_SIZING','IGP_SIZED_BEAMS') AND d.ycode IN (6,18) AND c.cont_no IN ({cl})
        ORDER BY m.td, m.tno""")
    fl = q(f"""SELECT TO_CHAR(v.trans_date,'YYYY-MM-DD'), v.trans_type, v.gp_no, v.inv_no, s.cont_no, v.deliver_to,
        NVL(v.a,0)+NVL(v.a2,0), NVL(v.b,0), NVL(v.c,0)+NVL(v.cp,0), v.total_mtr
        FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no IN ({cl}) ORDER BY v.trans_date, v.trans_no""")
    stock = {str(n): (float(a or 0), float(b or 0), float(c or 0), float(t or 0)) for n, a, b, c, t in q(f"""
        SELECT s.cont_no, SUM(NVL(v.a,0)+NVL(v.a2,0)), SUM(NVL(v.b,0)), SUM(NVL(v.c,0)+NVL(v.cp,0)), SUM(v.total_mtr)
        FROM dostex.wv_fab_stock_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no IN ({cl}) GROUP BY s.cont_no""")}
    onloom = q(f"""SELECT v.cont_no, l.lname, v.beam_code, TO_CHAR(v.td,'YYYY-MM-DD'), v.set_no, v.actual_length, v.beam_prod, v.blength
        FROM dostex.wv_beam_atloom_v v LEFT JOIN dostex.looms l ON l.lcode=v.lcode WHERE v.cont_no IN ({cl}) ORDER BY v.cont_no, l.lname""")
    waiting, seen = [], set()
    for r in q(f"SELECT cont_no, beam_code, TO_CHAR(td,'YYYY-MM-DD'), setno, blength FROM dostex.beams_stock_v WHERE cont_no IN ({cl}) ORDER BY td"):
        if r[1] not in seen:                     # the ERP view repeats a beam; count each beam once
            seen.add(r[1]); waiting.append(r)
    woven = {str(n): float(m or 0) for n, m in q(f"SELECT cont_no, SUM(total_mtr) FROM dostex.wv_prod_v WHERE cont_no IN ({cl}) GROUP BY cont_no")}
    last = {str(n): d for n, d in q(f"SELECT cont_no, TO_CHAR(MAX(td),'YYYY-MM-DD') FROM dostex.wv_prod_v WHERE cont_no IN ({cl}) GROUP BY cont_no")}

    sz = {}                              # (contract, sizer) -> [kg sent less back, last sent]
    per = {str(c): dict(igp_b=0.0, ret_b=0.0, out=0.0, back=0.0, rej_out=0.0, rej_sold=0.0, siz=0.0, sizer="") for c in CONTRACTS + [AZAM_PURI]}
    bagq = lambda b, kg: b if b else kg / 45.36  # noqa: E731
    igp, ret = [], []
    for d, tt, tno, gp, cno, an, yd, bg, cn, kg, desc in yl:
        p = per[str(cno)]
        if tt == "IGP_YARN":
            p["igp_b"] += bagq(float(bg), float(kg)); igp.append([d, gp or tno, int(cno), (yd or "").strip(), float(bg), float(cn), float(kg)])
        elif tt == "OGP_YARN":
            p["ret_b"] += bagq(float(bg), float(kg)); ret.append([d, gp or tno, int(cno), (yd or "").strip(), float(bg), float(cn), float(kg)])
        elif tt in ("TO_SIZING", "FROM_SIZING", "IGP_SIZED_BEAMS"):   # beams back at ERP weight, same basis as yarn sent
            k = sz.setdefault((str(cno), (an or "").strip()), [0.0, ""])
            bkg = (float(bg) + float(cn) / 40) * 45.36 if (float(bg) or float(cn)) else float(kg)   # bags + cones as part-bags (owner counts bags)
            k[0] += bkg if tt == "TO_SIZING" else -bkg
            if tt == "TO_SIZING":
                k[1] = max(k[1], d)
    for d, tt, gp, inv, cno, dto, a, b, c, t in fl:
        p = per[str(cno)]
        sgn = 1 if tt == "GPOUT" else -1
        if tt == "GPOUT" and "REJECTION" in (dto or "").upper():      # the mill's rejection sale (Khalid) - mill's, not Qadri's
            p["rej_sold"] += float(t or 0)
            continue
        p["out" if tt == "GPOUT" else "back"] += float(t or 0)
        p["rej_out"] += sgn * float(c or 0)
    loom_m = {str(c): sum(float(r[7] or 0) for r in onloom if str(r[0]) == str(c)) for c in CONTRACTS}
    # owner: shrinkage applies to the TOTAL set length - fabric still to come = set length x (1 - shrinkage) - fabric woven from the beam
    loom_set = {str(c): sum(float(r[5] or 0) for r in onloom if str(r[0]) == str(c)) for c in CONTRACTS}      # total set length of beams on looms
    loom_wov = {str(c): sum(float(r[6] or 0) for r in onloom if str(r[0]) == str(c)) for c in CONTRACTS}      # fabric already woven from them
    wait_m = {str(c): sum(float(r[4] or 0) * 3 for r in waiting if str(r[0]) == str(c)) for c in CONTRACTS}
    cutoff = (TODAY - timedelta(days=SIZING_DAYS)).isoformat()
    for (cno, an), (kg, lastsent) in sz.items():
        if kg >= SIZING_MIN_KG and lastsent >= cutoff:
            per[cno]["siz"] += kg; per[cno]["sizer"] = an
    at_siz = {str(c): per[str(c)]["siz"] for c in CONTRACTS}

    # ---- set-length angle: every sized beam of this yarn, followed to the looms (same logic as 49_contract_closing_by_sets)
    s49 = importlib.util.spec_from_file_location("s49", HERE / "49_contract_closing_by_sets.py")
    s49 = (lambda m: (s49.loader.exec_module(m), m)[1])(importlib.util.module_from_spec(s49))
    cmap = {cid: int(no) for cid, no in q(f"SELECT cont_id, cont_no FROM dostex.contm WHERE cont_no IN ({cl})")}
    mount = {(t, s_): (float(w or 3), ln) for t, s_, w, td, ln in q(s49.MOUNT_SQL)}
    offl = {(t, s_): st for t, s_, st, td in q(s49.OFF_SQL)}
    bprod = {}
    for t, s_, cid, mtr, f1, l1 in q(s49.PROD_SQL):
        bprod[(t, s_)] = bprod.get((t, s_), 0.0) + float(mtr or 0)
    beams, done_len, done_woven = [], 0.0, 0.0
    for tr, sr, setno, bcode, blen, cid, tt, tno, gp, td, desc, ends, slen, sizer in q(s49.BEAMS_SQL):
        if cid not in cmap:
            continue
        mw, ln = mount.get((tr, sr), (3.0, None))
        st = offl.get((tr, sr))
        alen, wv = float(blen or 0) * mw, bprod.get((tr, sr), 0.0)
        if st == "COMPLETE":
            done_len += alen; done_woven += wv
        elif not st:
            beams.append([cmap[cid], setno, bcode, (ln or "").strip() or "Not mounted (waiting)", f"{td:%Y-%m-%d}", float(blen or 0), mw, alen, wv])
    beams.sort(key=lambda b: (b[0], b[4], str(b[1]), str(b[2])))
    done_woven_ratio = 1 - done_woven / done_len        # shrinkage on finished beams
    all_sets_len = sum(float(b or 0) * mount.get((t, s_), (3.0, None))[0]
                       for t, s_, _, _, b, cid, *_ in q(s49.BEAMS_SQL) if cid in cmap)
    siz_bags = {tt: float(b or 0) for tt, b in q(f"""SELECT m.tt, SUM(NVL(d.bags,0) + NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0), CASE WHEN y.count > 40 THEN 40 ELSE 24 END))
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        JOIN dostex.contm c ON c.cont_id=d.cont_id WHERE m.tt IN ('TO_SIZING','FROM_SIZING','YARN_TO_LOOMS','YARN_FROM_LOOMS')
        AND d.ycode IN (6,18) AND c.cont_no IN ({cl}) GROUP BY m.tt""")}
    warp_sent_b = siz_bags.get("TO_SIZING", 0) - siz_bags.get("FROM_SIZING", 0)
    weft_issued_b = siz_bags.get("YARN_TO_LOOMS", 0) - siz_bags.get("YARN_FROM_LOOMS", 0)
    kg_per_set_m = (warp_sent_b * 45.36 - sum(at_siz.values())) / all_sets_len   # sized warp per meter of set (x pieces)
    print(f"set angle: {len(beams)} running beams, warp sent {warp_sent_b:,.1f} bags, weft issued {weft_issued_b:,.1f} bags, "
          f"complete-beam shrinkage {1 - done_woven / done_len:.2%}, {kg_per_set_m:.5f} kg/set-m")

    # fabric already woven on the looms but not yet entered (rolls are entered every few days): per loom, days since its
    # last production entry (or since the beam was mounted) × its average meters a day over the last 30 days
    # beams the ERP still shows on a loom although they were sent out EMPTY afterwards (take-off never entered) and the loom has no
    # production after that: the beam is finished - its remaining warp is in fabric woven but not yet entered (warp + weft)
    finished = {}
    for lc, lname, code, actual, bprod in q(f"""SELECT v.lcode, l.lname, v.beam_code, v.actual_length, v.beam_prod FROM dostex.wv_beam_atloom_v v
            JOIN dostex.beams bm ON bm.bcode=v.beam_code LEFT JOIN dostex.looms l ON l.lcode=v.lcode
            WHERE v.cont_no IN ({",".join(map(str, CONTRACTS))})
            AND EXISTS (SELECT 1 FROM dostex.wv_gp_beamsd d JOIN dostex.wv_gp_beamsm m ON m.trans_no=d.trans_no
                        WHERE d.beam_id=bm.beam_id AND m.tt='OGP_EMPTY_BEAMS' AND m.td > v.td
                        AND NOT EXISTS (SELECT 1 FROM dostex.wv_prod_v p WHERE p.lcode=v.lcode AND p.cont_no=v.cont_no AND p.td > m.td)
                        -- not explained by ANOTHER beam with the same number coming off a different loom in between (shared numbers)
                        AND NOT EXISTS (SELECT 1 FROM dostex.wprdtd d2 JOIN dostex.wprdtm m2 ON m2.trans_no=d2.trans_no
                                        WHERE m2.tt='BEAMS_FROM_LOOM' AND d2.beam_id=bm.beam_id AND d2.lcode<>v.lcode AND m2.td > v.td AND m2.td <= m.td))"""):
        finished[(lc, code)] = (lname, max(0.0, float(actual or 0) * (1 - done_woven_ratio) - float(bprod or 0)), float(actual or 0) - float(bprod or 0))
    print("beams off the loom but not entered:", finished)
    unent = {}
    for lc, cn, days, rate in q(f"""SELECT v.lcode, v.cont_no, TRUNC(SYSDATE) - NVL(MAX(p.td), MIN(v.td)),
            (SELECT SUM(total_mtr)/30 FROM dostex.wv_prod_v p2 WHERE p2.lcode=v.lcode AND p2.td > SYSDATE-30)
            FROM dostex.wv_beam_atloom_v v LEFT JOIN dostex.wv_prod_v p ON p.lcode=v.lcode AND p.cont_no=v.cont_no AND p.td >= v.td
            WHERE v.cont_no IN ({",".join(map(str, CONTRACTS))}) GROUP BY v.lcode, v.cont_no"""):
        unent[(lc, cn)] = float(days or 0) * float(rate or 0)
    # a loom that wove ANOTHER contract after this contract's last entry is not running this beam (take-off keyed late): no estimate;
    # and no loom can have more unentered fabric than its beams can still make (total set length x (1 - shrinkage) - woven)
    shr_ = 1 - done_woven / done_len
    capm = {(lc, cn): max(0.0, float(c_ or 0)) for lc, cn, c_ in q(f"""SELECT lcode, cont_no, SUM(actual_length * (1 - {shr_}) - beam_prod)
            FROM dostex.wv_beam_atloom_v WHERE cont_no IN ({",".join(map(str, CONTRACTS))}) GROUP BY lcode, cont_no""")}
    for lc, cn, other in q(f"""SELECT x.lcode, x.cont_no, (SELECT COUNT(*) FROM dostex.wv_prod_v o WHERE o.lcode=x.lcode AND o.cont_no<>x.cont_no AND o.td > x.lastd)
            FROM (SELECT v.lcode, v.cont_no, NVL(MAX(p.td), MIN(v.td)) lastd FROM dostex.wv_beam_atloom_v v
                  LEFT JOIN dostex.wv_prod_v p ON p.lcode=v.lcode AND p.cont_no=v.cont_no AND p.td >= v.td
                  WHERE v.cont_no IN ({",".join(map(str, CONTRACTS))}) GROUP BY v.lcode, v.cont_no) x"""):
        if (lc, cn) in unent:
            unent[(lc, cn)] = 0.0 if other else min(unent[(lc, cn)], capm.get((lc, cn), 0.0))
    fin_looms = {lc for lc, _ in finished}
    for k in list(unent):
        if k[0] in fin_looms:                  # the beam is finished: use its remaining fabric instead of a rate estimate
            unent[k] = 0.0
    for (lc, code), (lname, rem_fab, rem_len) in finished.items():
        unent[(lc, "fin")] = rem_fab
        cn_ = next(str(r[0]) for r in onloom if r[2] == code and (r[1] or "").strip() == (lname or "").strip())
        loom_m[cn_] += rem_fab / (1 - done_woven_ratio) - rem_len   # warp: what the beam made, not what the ERP says is left
    wb = Workbook()
    # ================= Contract-wise (data the closing reads)
    cw = wb.active; cw.title = "Contract-wise"
    cw["A1"] = "Contract-wise figures · Qadri 52/1 & 54/1 cotton (ERP)"; cw["A1"].font = T
    head(cw, 3, ["Contract", "Yarn received (bags)", "Yarn returned (bags)", "Fabric woven (m)", "Fabric OGP (m)", "Fabric IGP returned (m)",
                 "Fabric dispatched net (m)", "of which rejection C/CP dispatched (m)", "Fabric in stock, all grades (m)", "Stock A (m)", "Stock B (m)",
                 "Stock C/CP (m)", "Warp left on looms (m)", "Sized beams waiting (m)", "Yarn at sizing (kg)", "Last woven", "Rejection sold by the mill - Khalid (m)",
                 "Set length of beams on looms (m)", "Fabric woven from beams on looms (m)"])
    for i, w in enumerate([9, 12, 12, 13, 13, 12, 14, 14, 14, 11, 10, 11, 13, 13, 12, 11, 14, 14, 14], 1):
        cw.column_dimensions[cw.cell(row=3, column=i).column_letter].width = w
    for r, n in enumerate(CONTRACTS, 4):
        p, s = per[str(n)], stock.get(str(n), (0, 0, 0, 0))
        vals = [n, round(p["igp_b"], 1), round(p["ret_b"], 1), round(woven.get(str(n), 0)), round(p["out"]), round(p["back"]), f"=E{r}-F{r}",
                round(p["rej_out"]), round(s[3]), round(s[0]), round(s[1]), round(s[2]), round(loom_m[str(n)]), round(wait_m[str(n)]),
                round(at_siz[str(n)], 1), last.get(str(n)), round(p["rej_sold"]), round(loom_set[str(n)]), round(loom_wov[str(n)])]
        for i, v in enumerate(vals, 1):
            cw.cell(row=r, column=i, value=v).number_format = N0 if 1 < i < 16 or i >= 17 else "General"
    tr = 4 + len(CONTRACTS) + 1          # totals row (10)
    cw.cell(row=tr, column=1, value="TOTAL").font = B
    for col in list(range(2, 16)) + [17, 18, 19]:
        L = cw.cell(row=4, column=col).column_letter
        c = cw.cell(row=tr, column=col, value=f"=SUM({L}4:{L}{tr - 2})"); c.font = B; c.number_format = N0
    ap = per[str(AZAM_PURI)]
    cw.cell(row=tr + 2, column=1, value=f"Azam Puri {AZAM_PURI}").font = B
    for i, v in [(2, round(ap["igp_b"], 1)), (4, round(woven.get(str(AZAM_PURI), 0))), (5, round(ap["out"])), (6, round(ap["back"]))]:
        cw.cell(row=tr + 2, column=i, value=v).number_format = N0
    cw.cell(row=tr + 2, column=7, value=f"=E{tr + 2}-F{tr + 2}").number_format = N0
    cw.cell(row=tr + 2, column=16, value="no yarn of its own - woven from Qadri's yarn")
    cw.cell(row=tr + 4, column=1, value="Yarn is often booked to one contract and woven on another, so single-contract balances swing; the closing uses the totals.").font = NOTE
    CW = "'Contract-wise'!"

    # ================= CLOSING (owner's layout)
    g = wb.create_sheet("Closing", 0)
    g.column_dimensions["A"].width = 52
    for col, w in zip("BCDEFG", (15, 15, 15, 15, 15, 44)):
        g.column_dimensions[col].width = w
    g["A1"] = "52 CTN GRAND CLOSING · QADRI TRADERS (EMB)"; g["A1"].font = T
    g["A2"] = f"CLOSED as at {TODAY:%d-%b-%Y} · 52/1 & 54/1 cotton (same yarn) · contracts {', '.join(map(str, CONTRACTS))}"; g["A2"].font = NOTE
    # --- quality & weights
    g["A4"] = "QUALITY"; g["B4"] = '90X82  54/54  CTN  46"'; g["A4"].font = B; g["B4"].font = B
    inputs = [("Ends / inch (actual, incl. selvedge)", 90.43), ("Picks / inch", 82), ("Width (in)", 46), ("Warp closing count", 51.5),
              ("Weft closing count", 52.5), ("Divisor", 800), ("Lb per bag", 100), ("Warp rate (Rs / lb)", 610), ("Weft rate (Rs / lb)", 610),
              ("Rejection in stock - mill's (m)", 45000),
              (f"Yarn in hand - count {COUNT_BAGS} on {COUNT_DATE:%d-%b} + ERP movements since (bags)", f"={COUNT_BAGS}+{round(since_count(cur), 2)}")]
    for i, (l, v) in enumerate(inputs, 5):
        g.cell(row=i, column=1, value=l); g.cell(row=i, column=2, value=v).fill = INPUT
    # B5 ends B6 picks B7 width B8 warp cnt B9 weft cnt B10 div B11 lb/bag B12 warp rate B13 weft rate B14 rejection m B15 in hand
    g["D5"] = "WARP WEIGHT (lb / m)"; g["E5"] = "=B5*B7*1.0936/B10/B8"
    g["D6"] = "WEFT WEIGHT (lb / m)"; g["E6"] = "=B6*B7*1.0936/B10/B9"
    g["D7"] = "TOTAL WEIGHT (lb / m)"; g["E7"] = "=E5+E6"
    g["D8"] = "Fabric weight (g / m)"; g["E8"] = "=E7*453.59237"
    g["D10"] = "WARP RATE (Rs / lb)"; g["E10"] = "=B12"
    g["D11"] = "WEFT RATE (Rs / lb)"; g["E11"] = "=B13"
    g["D12"] = "Warp bag value (Rs)"; g["E12"] = "=B11*B12"
    g["D13"] = "Weft bag value (Rs)"; g["E13"] = "=B11*B13"
    for r in (5, 6, 7):
        g[f"E{r}"].number_format = N4; g[f"D{r}"].font = B; g[f"E{r}"].font = B
    for r in (8, 10, 11, 12, 13):
        g[f"E{r}"].number_format = N0
    g["D10"].font = B; g["D11"].font = B
    unentered = sum(q_ for q_ in unent.values())
    # warp still in beams / at the sizer is counted as the fabric it will make (owner, 28-Sep-2026)
    g["D14"] = "Shrinkage beam → fabric (finished beams, ERP)"; g["E14"] = round(1 - done_woven / done_len, 4)
    g["E14"].fill = INPUT; g["E14"].number_format = "0.00%"
    g["D15"] = "Sized warp kg per meter of set (all sets)"; g["E15"] = round(kg_per_set_m, 5); g["E15"].number_format = "0.00000"
    g["A16"] = "Fabric woven on looms, not yet entered (m) - estimate"; g["B16"] = round(unentered); g["B16"].fill = INPUT; g["B16"].number_format = N0
    m60 = float(rows(cur, f"""SELECT SUM(CASE WHEN v.trans_type = 'GPOUT' THEN v.total_mtr ELSE -v.total_mtr END) FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id
                              WHERE s.cont_no IN (38,50,60,68,70) AND s.quality LIKE '%54/60%'
                              AND NOT (v.trans_type = 'GPOUT' AND UPPER(NVL(v.deliver_to,' ')) LIKE '%REJECTION%')""")[0][0] or 0)
    g["D9"] = "60/1 weft closing count"; g["E9"] = WEFT60_COUNT; g["E9"].fill = INPUT
    g["D16"] = "Fabric woven with 60/1 weft (m) - contract 38 OGP 361-381"; g["E16"] = m60; g["E16"].number_format = N0
    # --- fabric
    g["A17"] = "FABRIC (meters)"; g["A17"].font = S
    fab = [("Fabric dispatched to Qadri (net of returns; excl. rejection sold by the mill)", f"={CW}G{tr}"),
           ("  of which rejection (C/CP) sent to Qadri's processors", f"={CW}H{tr}"),
           ("Fabric in stock (ERP, all grades)", f"={CW}I{tr}"),
           ("  less rejection in stock - mill's", "=-B14"),
           ("Fresh fabric in stock (for Qadri)", "=B20+B21"),
           ("TOTAL FABRIC FOR QADRI (dispatched + fresh stock)", "=B18+B22")]
    for i, (l, f) in enumerate(fab, 18):
        g.cell(row=i, column=1, value=l); g.cell(row=i, column=2, value=f).number_format = N0
    for col in (1, 2):
        g.cell(row=23, column=col).font = B
    g.cell(row=21, column=1).fill = REJ; g.cell(row=19, column=1).fill = REJ
    # --- yarn account (warp / weft / total in bags)
    g["A25"] = "YARN ACCOUNT (bags of 100 lb)"; g["A25"].font = S
    head(g, 26, ["", "Warp (bags)", "Weft (bags)", "Total (bags)", "Total (lb)", "Total (kg)", "How"])
    ya = [
        ("Yarn received from Qadri (IGP)", None, None, f"={CW}B{tr}", "sum of all IGPs (sheet 'Yarn IGP')"),
        ("Less: yarn returned to Qadri", None, None, f"=-{CW}C{tr}", "OGP returns"),
        ("NET YARN RECEIVED", None, None, "=D27+D28", ""),
        ("Consumed in fabric dispatched", "=-$B$18*$E$5/$B$11", "=-($B$18-$E$16)*$E$6/$B$11", "=B30+C30",
         "dispatched m × warp / weft weight; the weft of the 60/1-weft fabric (E16) is in the separate 60/1 account (I26)"),
        ("Consumed in fresh fabric stock", "=-$B$22*$E$5/$B$11", "=-$B$22*$E$6/$B$11", "=B31+C31", "fresh stock m × weight"),
        ("On loom: warp left on beams · weft in fabric woven but not yet entered", f"=-({CW}R{tr}*(1-$E$14)-{CW}S{tr})*$E$5/$B$11", "=-$B$16*$E$6/$B$11", "=B32+C32",
         "warp: fabric still to come = total set length × (1 - shrinkage E14) - fabric woven, × warp weight; weft: B16 (estimate) × weft weight"),
        ("On loom yarn: sized beams waiting", f"=-{CW}N{tr}*(1-$E$14)*$E$5/$B$11", None, "=B33", "sized length × 3 pieces less shrinkage × warp weight"),
        (f"Yarn at sizing (Haji Taj Babu) - {sum(at_siz.values()) / 45.36:.1f} bags physical, as fabric to come", f"=-{CW}O{tr}/$E$15*(1-$E$14)*$E$5/$B$11", None, "=B34",
         "physical bags x 45.36 kg ÷ warp kg per set meter (E15) = set length, less shrinkage, × warp weight (closing weight charges ~1.5% more)"),
        ("TOTAL CONSUMED + ON LOOM", "=SUM(B30:B34)", "=SUM(C30:C34)", "=SUM(D30:D34)", ""),
        ("Yarn in hand (count + ERP movements since)", None, None, "=-B15", "owner's count rolled forward"),
        ("BALANCE", None, None, "=D29+D35+D36", "+ = mill holds Qadri's yarn (payable) · − = receivable from Qadri"),
    ]
    for i, (l, wv, fv, tv, how) in enumerate(ya, 27):
        g.cell(row=i, column=1, value=l)
        for col, v in ((2, wv), (3, fv), (4, tv)):
            if v is not None:
                g.cell(row=i, column=col, value=v).number_format = N1
        g.cell(row=i, column=5, value=f"=D{i}*$B$11").number_format = N0
        g.cell(row=i, column=6, value=f"=D{i}*$B$11*0.45359237").number_format = N0
        g.cell(row=i, column=7, value=how).font = NOTE
        if l.isupper():
            for col in range(1, 7):
                g.cell(row=i, column=col).font = B
    for col in range(1, 8):
        g.cell(row=37, column=col).fill = GREEN
    # --- 60/1 cotton weft: a separate account
    g["I25"] = "60/1 CTN WEFT - SEPARATE ACCOUNT (bags)"; g["I25"].font = S
    head(g, 26, ["", "Bags", "How"], 9)
    for i, (l, v, how) in enumerate([
            ("Received: transfer from contract 40", TRANSFER60_BAGS, "accountant's closing (the ERP shows only 3.95 bags of 60/1 issued on contract 38)"),
            ("Fabric woven with 60/1 weft (m)", "=E16", "OGP 361, 363, 364, 365, 366, 375, 381 (contract 38)"),
            ("60/1 weft weight (lb / m) at count E9", "=B6*B7*1.0936/B10/E9", "picks × width × 1.0936 ÷ 800 ÷ 59"),
            ("Consumed in that fabric", "=-J28*J29/B11", ""),
            ("BALANCE 60/1", "=J27+J30", "+ = returnable to Qadri (contract 40's yarn)"),
            ("60/1 warp closing count (if used as warp)", WARP60_COUNT, "no 60/1 warp on these contracts")], 27):
        g.cell(row=i, column=9, value=l); c = g.cell(row=i, column=10, value=v)
        c.number_format = N4 if i == 29 else (N0 if i == 28 else N1)
        g.cell(row=i, column=11, value=how).font = NOTE
    g["J27"].fill = INPUT; g["J32"].fill = INPUT
    g["I31"].font = B; g["J31"].font = B; g["J31"].fill = GREEN
    g.column_dimensions["I"].width = 38; g.column_dimensions["J"].width = 12; g.column_dimensions["K"].width = 60
    # --- closing value
    g["A39"] = "CLOSING"; g["A39"].font = S
    g["A40"] = "Balance (bags)"; g["B40"] = "=D37"
    g["A41"] = "Balance (lb)"; g["B41"] = "=E37"
    g["A42"] = "Rate used (Rs / lb, warp/weft weighted)"; g["B42"] = "=(B35*B12+C35*B13)/(B35+C35)"
    g["A43"] = "CLOSING VALUE (Rs)"; g["B43"] = "=ABS(B41)*B42"
    g["A44"] = "RESULT"; g["B44"] = '=IF(B40>=0,"Payable to Qadri - mill holds "&TEXT(B40,"0.0")&" bags of their yarn","Receivable from Qadri - fabric used "&TEXT(-B40,"0.0")&" bags more than supplied")'
    g["A45"] = "As % of yarn received"; g["B45"] = "=B40/D29"
    for r, fmt in ((40, N1), (41, N0), (42, N0), (43, N0), (45, "0.00%")):
        g[f"B{r}"].number_format = fmt
    for r in range(40, 46):
        g[f"A{r}"].font = B; g[f"B{r}"].font = B
    g["B43"].fill = GREEN; g["B44"].fill = GREEN
    # --- where the yarn went
    g["A47"] = "WHERE ALL OF QADRI'S YARN WENT (at closing weight)"; g["A47"].font = S
    head(g, 48, ["", "Bags", "Value (Rs)", "", "", "", "Note"])
    wl = [("Qadri's fabric + on loom (consumed above)", "=-D35", "dispatched, fresh stock, beams, sized, at sizing"),
          ("Yarn in hand", "=B15", ""),
          ("Rejection kept or sold by the mill (mill's)", f"=(B14+{CW}Q{tr})*E7/B11", "45,000 m in stock + rejection sold to Khalid - belongs to the mill"),
          ("Azam Puri contract 52 (woven from Qadri's yarn)", f"={CW}G{tr + 2}*E7/B11", "Azam Puri supplied no yarn for contract 52 - recover if due"),
          ("TOTAL USED AT CLOSING WEIGHT", "=SUM(B49:B52)", ""),
          ("Received from Qadri (net)", "=D29", ""),
          ("COVERED BY THE ALLOWANCE IN THE CLOSING WEIGHTS", "=B53-B54", "÷800 and counts 51.5 / 52.5 charge more yarn per meter than the fabric holds")]
    for i, (l, f, note) in enumerate(wl, 49):
        g.cell(row=i, column=1, value=l)
        g.cell(row=i, column=2, value=f).number_format = N1
        g.cell(row=i, column=3, value=f"=B{i}*$B$11*$B$42").number_format = N0
        g.cell(row=i, column=7, value=note).font = NOTE
        if l.isupper():
            for col in (1, 2, 3):
                g.cell(row=i, column=col).font = B
    g["A57"] = "NOTES"; g["A57"].font = S
    for i, t in enumerate([
        "Contract 2 (90x82 52/52 47\") is excluded: it started in the old ERP, so its yarn received before Oct-2023 is not in this ERP.",
        "Contracts 68 and 70 were still weaving on the closing date: their fresh stock, beams on looms, sized beams and yarn at sizing are carried above.",
        "Day-by-day check (Claude ERP Audit folder): weft issued matches production (-2%) and every sizer is at normal loss - no sign of pilferage.",
        "Rejection sold by the mill to KHALID (REJECTION) on 25-May-2026 (18,442 m, contracts 38 & 50) is the mill's and not charged to Qadri.",
        "Warp on beams, waiting beams and at the sizer is counted as the fabric it will make: shrinkage (E14, from finished beams) is applied to the TOTAL set length, less the fabric already woven from the beam, × warp weight - what Qadri will be charged when it is woven.",
        "Beams the ERP shows on a loom but later sent out empty (take-off never entered) are treated as finished; none today. Beam numbers 00012, 00019 and 00035 are each painted on two physical beams - both are counted.",
        "Fabric on the looms not yet entered (B16) is estimated per loom: days since its last roll entry (or since the beam was mounted) × its  in the ERP (entry error); both rows are included in 'On loom yarn'.",
        "ERP data read only; closing made in this sheet.",
    ], 58):
        g.cell(row=i, column=1, value="• " + t).font = NOTE
    g.freeze_panes = "A4"

    # ================= Yarn IGP (dates and bags)
    y = wb.create_sheet("Yarn IGP", 1)
    y["A1"] = "Yarn received from Qadri - 52/1 & 54/1 cotton"; y["A1"].font = T
    head(y, 3, ["Date", "IGP / GP", "Contract", "Yarn", "Bags", "Cones", "Kg"])
    for i, w in enumerate([12, 10, 10, 16, 10, 10, 12], 1):
        y.column_dimensions[y.cell(row=3, column=i).column_letter].width = w
    for r in igp:
        y.append(r)
        y.cell(row=y.max_row, column=7).number_format = N0
    n1 = y.max_row
    y.append(["TOTAL RECEIVED", "", "", "", f"=SUM(E4:E{n1})", f"=SUM(F4:F{n1})", f"=SUM(G4:G{n1})"])
    for col in range(1, 8):
        y.cell(row=y.max_row, column=col).font = B
    y.cell(row=y.max_row, column=7).number_format = N0
    y.append([])
    y.append(["Yarn returned to Qadri (OGP)"]); y.cell(row=y.max_row, column=1).font = S
    head(y, y.max_row + 1, ["Date", "OGP / GP", "Contract", "Yarn", "Bags", "Cones", "Kg"])
    r0 = y.max_row + 1
    for r in ret:
        y.append(r)
    y.append(["TOTAL RETURNED", "", "", "", f"=SUM(E{r0}:E{y.max_row})", f"=SUM(F{r0}:F{y.max_row})", f"=SUM(G{r0}:G{y.max_row})"])
    for col in range(1, 8):
        y.cell(row=y.max_row, column=col).font = B
    y.freeze_panes = "A4"

    # ================= Fabric OGP (dates and rejection)
    f = wb.create_sheet("Fabric OGP", 2)
    f["A1"] = "Fabric dispatched to Qadri (OGP) and returned (IGP) - with grades"; f["A1"].font = T
    head(f, 3, ["Date", "Type", "GP", "Invoice", "Contract", "Delivered to", "A (fresh)", "B", "Rejection C/CP", "Total meters", "Whose"])
    for i, w in enumerate([12, 11, 8, 9, 9, 44, 11, 9, 12, 12, 20], 1):
        f.column_dimensions[f.cell(row=3, column=i).column_letter].width = w
    for d, tt, gp, inv, cno, dto, a, b, c, t in fl:
        if int(cno) == AZAM_PURI:
            continue
        sg = 1 if tt == "GPOUT" else -1
        mill = tt == "GPOUT" and "REJECTION" in (dto or "").upper()
        f.append([d, "OGP" if tt == "GPOUT" else "IGP return", gp, inv, int(cno), (dto or "").strip(), sg * float(a), sg * float(b), sg * float(c), sg * float(t or 0),
                  "Mill (rejection sold)" if mill else "Qadri"])
        if mill:
            for col in range(1, 12):
                f.cell(row=f.max_row, column=col).fill = REJ
        for col in (7, 8, 9, 10):
            f.cell(row=f.max_row, column=col).number_format = N0
        if float(c):
            f.cell(row=f.max_row, column=9).fill = REJ
    n2 = f.max_row
    f.append(["TOTAL (net)", "", "", "", "", "", f"=SUM(G4:G{n2})", f"=SUM(H4:H{n2})", f"=SUM(I4:I{n2})", f"=SUM(J4:J{n2})"])
    f.append(["  to Qadri", "", "", "", "", "", f'=SUMIF(K4:K{n2},"Qadri",G4:G{n2})', f'=SUMIF(K4:K{n2},"Qadri",H4:H{n2})', f'=SUMIF(K4:K{n2},"Qadri",I4:I{n2})', f'=SUMIF(K4:K{n2},"Qadri",J4:J{n2})'])
    f.append(["  rejection sold by the mill", "", "", "", "", "", f'=SUMIF(K4:K{n2},"Mill*",G4:G{n2})', f'=SUMIF(K4:K{n2},"Mill*",H4:H{n2})', f'=SUMIF(K4:K{n2},"Mill*",I4:I{n2})', f'=SUMIF(K4:K{n2},"Mill*",J4:J{n2})'])
    for col in range(1, 11):
        f.cell(row=f.max_row, column=col).font = B
        if col >= 7:
            f.cell(row=f.max_row, column=col).number_format = N0
    f.freeze_panes = "A4"; f.auto_filter.ref = f"A3:J{n2}"

    # ================= On loom yarn
    bm = wb.create_sheet("On loom yarn", 3)
    bm["A1"] = "On loom yarn - warp on beams not yet woven, sized beams waiting, yarn at the sizer"; bm["A1"].font = T
    head(bm, 3, ["Contract", "Loom", "Beam", "Mounted", "Set", "Beam length (m)", "Woven (m)", "Left (m)", "Warp yarn left - as fabric to come (bags)", "Note"])
    for i, w in enumerate([9, 12, 9, 12, 10, 14, 11, 11, 14], 1):
        bm.column_dimensions[bm.cell(row=3, column=i).column_letter].width = w
    fin_by = {(code, (ln or "").strip()): rem for (_, code), (ln, rem, _) in finished.items()}
    for rr in onloom:
        key = (rr[2], (rr[1] or "").strip())
        left_ = fin_by[key] / (1 - done_woven_ratio) if key in fin_by else float(rr[7] or 0)
        bm.append([int(rr[0]), (rr[1] or "").strip(), rr[2], rr[3], rr[4], float(rr[5] or 0), float(rr[6] or 0), round(left_, 1)])
        bm.cell(row=bm.max_row, column=9, value=f"=MAX(0,F{bm.max_row}*(1-Closing!$E$14)-G{bm.max_row})*Closing!$E$5/Closing!$B$11").number_format = N1
        if key in fin_by:
            bm.cell(row=bm.max_row, column=10, value=f"sent EMPTY to the sizer after mounting - take-off not entered; ERP shows {float(rr[7] or 0):,.0f} m left, "
                                                    f"counted as finished ({fin_by[key]:,.0f} m fabric woven, not yet entered)").font = NOTE
    n3 = bm.max_row
    bm.append(["TOTAL ON LOOMS", "", "", "", "", f"=SUM(F4:F{n3})", f"=SUM(G4:G{n3})", f"=SUM(H4:H{n3})", f"=SUM(I4:I{n3})"])
    for col in range(1, 10):
        bm.cell(row=bm.max_row, column=col).font = B
    bm.append([])
    bm.append(["Sized beams waiting (not yet on a loom)"]); bm.cell(row=bm.max_row, column=1).font = S
    head(bm, bm.max_row + 1, ["Contract", "Beam", "Received", "Set", "Length per beam (m)", "× 3 pieces (m)", "", "", "Warp yarn (bags)"])
    r0 = bm.max_row + 1
    for rr in waiting:
        bm.append([int(rr[0]), rr[1], rr[2], rr[3], float(rr[4] or 0), float(rr[4] or 0) * 3])
        bm.cell(row=bm.max_row, column=9, value=f"=F{bm.max_row}*(1-Closing!$E$14)*Closing!$E$5/Closing!$B$11").number_format = N1
    bm.append(["TOTAL WAITING", "", "", "", "", f"=SUM(F{r0}:F{bm.max_row})", "", "", f"=SUM(I{r0}:I{bm.max_row})"])
    for col in range(1, 10):
        bm.cell(row=bm.max_row, column=col).font = B
    bm.append([])
    bm.append(["Yarn at the sizer"]); bm.cell(row=bm.max_row, column=1).font = S
    for n in CONTRACTS:
        if at_siz[str(n)]:
            bm.append([n, per[str(n)]["sizer"], "", "", f"{at_siz[str(n)]:,.1f} kg", "", "", "", round(at_siz[str(n)] / 45.36, 1)])
    bm.append([]); bm.append(["Beam 00019 and 00035 appear on two looms at once in the ERP (entry error); both rows are included."])

    # ================= Set length angle
    sa = wb.create_sheet("Set length angle", 4)
    sa["A1"] = "SET LENGTH ANGLE · running sets → fabric still to come (net of shrinkage) → warp on beams → weft needed vs weft received"; sa["A1"].font = T
    sa["A2"] = "Only Qadri 52/1 & 54/1 cotton. Set length = beam length × pieces (3 on 46\" looms). Yellow = your input."; sa["A2"].font = NOTE
    for i, w in enumerate([44, 11, 9, 18, 12, 12, 8, 13, 14, 12, 13, 13, 14, 14], 1):
        sa.column_dimensions[sa.cell(row=1, column=i).column_letter].width = w
    ins = [("Shrinkage: set length → fabric (your estimate)", 0.06, "0.0%", True),
           ("Actual shrinkage on beams already finished (ERP)", round(1 - done_woven / done_len, 4), "0.0%", False),
           ("Sized warp per meter of set (kg, from all sets)", round(kg_per_set_m, 5), "0.00000", False),
           ("Warp weight (lb / m) - closing", "=Closing!E5", N4, False),
           ("Weft weight (lb / m) - closing (count 52.5, ÷800)", "=Closing!E6", N4, False),
           ("Weft actually used (lb / m) - ERP weft issued ÷ fabric woven", f"={round(weft_issued_b, 1)}*100/{round(sum(woven.values()))}", N4, False)]
    for i, (l, v, fmt, yel) in enumerate(ins, 4):
        sa.cell(row=i, column=1, value=l); c = sa.cell(row=i, column=2, value=v); c.number_format = fmt
        if yel:
            c.fill = INPUT
    # B4 shrink, B5 actual, B6 kg/set-m, B7 warp lb/m, B8 weft closing, B9 weft actual
    sa["A11"] = "RUNNING SETS (on looms + waiting) AND THE SET AT THE SIZER"; sa["A11"].font = S
    head(sa, 12, ["Contract", "Set", "Beam", "Loom", "Received", "Beam length (m)", "Pieces", "Set length (m)", "Fabric expected (m, less shrinkage)",
                  "Woven so far (m)", "Still to come (m)", "Warp on beam (bags)", "Weft needed - closing (bags)", "Weft needed - actual rate (bags)"])
    r = 13
    for b in beams:
        for i, v in enumerate(b[:7], 1):
            sa.cell(row=r, column=i, value=v)
        sa.cell(row=r, column=8, value=f"=F{r}*G{r}")
        sa.cell(row=r, column=9, value=f"=H{r}*(1-$B$4)")
        sa.cell(row=r, column=10, value=round(b[8]))
        sa.cell(row=r, column=11, value=f"=MAX(0,I{r}-J{r})")
        sa.cell(row=r, column=12, value=f"=K{r}*$B$7/Closing!$B$11")
        sa.cell(row=r, column=13, value=f"=K{r}*$B$8/Closing!$B$11")
        sa.cell(row=r, column=14, value=f"=K{r}*$B$9/Closing!$B$11")
        for col in range(6, 15):
            sa.cell(row=r, column=col).number_format = N1 if col >= 12 else N0
        r += 1
    last_beam = r - 1
    sz_kg = sum(at_siz.values())
    sa.cell(row=r, column=1, value=70); sa.cell(row=r, column=4, value="At sizer (Haji Taj Babu) - next set")
    sa.cell(row=r, column=6, value=f"{sz_kg:,.0f} kg yarn")
    sa.cell(row=r, column=8, value=f"={round(sz_kg, 1)}/$B$6")
    for col, f_ in ((9, f"=H{r}*(1-$B$4)"), (10, 0), (11, f"=MAX(0,I{r}-J{r})"), (12, f"=K{r}*$B$7/Closing!$B$11"),
                    (13, f"=K{r}*$B$8/Closing!$B$11"), (14, f"=K{r}*$B$9/Closing!$B$11")):
        sa.cell(row=r, column=col, value=f_)
    for col in range(8, 15):
        sa.cell(row=r, column=col).number_format = N1 if col >= 12 else N0
    sa.cell(row=r, column=1).fill = SEC; sa.cell(row=r, column=4).fill = SEC
    sizer_row = r
    r += 1
    sa.cell(row=r, column=1, value="TOTAL")
    for col in range(8, 15):
        L = sa.cell(row=r, column=col).column_letter
        c = sa.cell(row=r, column=col, value=f"=SUM({L}13:{L}{sizer_row})"); c.number_format = N1 if col >= 12 else N0
    for col in range(1, 15):
        sa.cell(row=r, column=col).font = B
    trow = r
    sa.cell(row=trow + 1, column=1, value=f"Warp on beams and looms (bags, excluding the sizer set): ").font = B
    sa.cell(row=trow + 1, column=12, value=f"=SUM(L13:L{last_beam})").number_format = N1
    sa.cell(row=trow + 1, column=12).font = B
    # weft account
    r = trow + 4
    sa.cell(row=r - 1, column=1, value="WEFT ACCOUNT (bags of 100 lb)").font = S
    head(sa, r, ["", "At closing weft weight", "At actual weft used (ERP)", "", "How"])
    wa = [("Yarn received from Qadri, net of returns", "=Closing!D29", "=Closing!D29", "all IGPs less returns"),
          ("Less: warp sent to sizing, net", f"=-{round(warp_sent_b, 1)}", f"=-{round(warp_sent_b, 1)}", "all sets incl. the one at the sizer"),
          ("YARN RECEIVED FOR WEFT", f"=B{r + 1}+B{r + 2}", f"=C{r + 1}+C{r + 2}", ""),
          ("Weft in fabric already woven", f"=-{round(sum(woven.values()))}*Closing!E6/Closing!B11", f"=-{round(weft_issued_b, 1)}",
           f"{sum(woven.values()):,.0f} m woven (all grades, incl. Azam Puri 52) · actual = weft issued to looms"),
          ("Weft for fabric still to come from running sets", f"=-SUM(M13:M{last_beam})", f"=-SUM(N13:N{last_beam})", "table above"),
          ("Weft for the set now at the sizer", f"=-M{sizer_row}", f"=-N{sizer_row}", "table above"),
          ("TOTAL WEFT NEEDED", f"=B{r + 4}+B{r + 5}+B{r + 6}", f"=C{r + 4}+C{r + 5}+C{r + 6}", ""),
          ("DIFFERENCE (received for weft − needed)", f"=B{r + 3}+B{r + 7}", f"=C{r + 3}+C{r + 7}",
           "+ = weft left over at the end · − = weft Qadri still has to send to finish the warp already sized"),
          ("", None, None, ""),
          ("Weft that should be in hand today (received for weft − woven)", f"=B{r + 3}+B{r + 4}", f"=C{r + 3}+C{r + 4}", ""),
          ("Yarn in hand - physical count", "=Closing!B15", "=Closing!B15", "owner's count"),
          ("Weft still needed for running sets + sizer set", f"=-(B{r + 5}+B{r + 6})", f"=-(C{r + 5}+C{r + 6})", ""),
          ("WEFT QADRI STILL HAS TO SEND (needed − in hand)", f"=B{r + 12}-B{r + 11}", f"=C{r + 12}-C{r + 11}", "to weave out all warp already sent to sizing"),
          ("Value at weft rate (Rs)", f"=B{r + 13}*Closing!B11*Closing!B13", f"=C{r + 13}*Closing!B11*Closing!B13", "")]
    for i, (l, bv, cv, how) in enumerate(wa, r + 1):
        sa.cell(row=i, column=1, value=l)
        for col, v in ((2, bv), (3, cv)):
            if v is not None:
                sa.cell(row=i, column=col, value=v).number_format = N0 if "Rs" in l else N1
        sa.cell(row=i, column=5, value=how).font = NOTE
        if l[:2] == "= " or l.split(" (")[0].isupper():
            for col in (1, 2, 3):
                sa.cell(row=i, column=col).font = B
    for col in (1, 2, 3):
        sa.cell(row=r + 8, column=col).fill = GREEN; sa.cell(row=r + 13, column=col).fill = GREEN
    sa.freeze_panes = "A13"

    # ================= Method
    m = wb.create_sheet("Method")
    for i, t in enumerate([
        "HOW THIS CLOSING WAS MADE",
        "Weight per meter = ends (or picks) per inch × width × 1.0936 ÷ 800 ÷ closing count (owner's method).",
        "Closing counts for Qadri 52/1 & 54/1: warp 51.5, weft 52.5 (includes the wastage allowance). Ends 90.43/inch incl. selvedge (12,480 ÷ 3 × 46\").",
        "52/1 CTN and 54/1 CTN are the same yarn in the same bags (owner, 28-Sep-2026), so both ERP codes are counted together.",
        "Yarn received counted in bags as on the gate pass (1 bag = 100 lb); returns to Qadri deducted.",
        "Fabric dispatched is net of fabric returned; rejection (C/CP) dispatched to Qadri is part of it. Stock split into fresh (Qadri's) and rejection (mill's).",
        "On loom yarn = warp left on beams on looms + sized beams waiting (× 3 pieces) + yarn at the sizer (only real lots, 3 bags or more).",
        "Yarn in hand = owner's physical count, rolled forward with ERP receipts / issues after the count date. Positive balance = payable to Qadri; negative = receivable from Qadri (same as the owner's closings).",
        "ERP data read only; nothing written to Oracle.",
    ], 1):
        m.cell(row=i, column=1, value=t).font = T if i == 1 else Font()
    m.column_dimensions["A"].width = 150

    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    print("saved", OUT, "| waiting beams:", len(waiting), "| at sizing:", {k: round(v) for k, v in at_siz.items() if v})


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
