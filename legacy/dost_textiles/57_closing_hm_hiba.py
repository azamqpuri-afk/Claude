"""
57_closing_hm_hiba.py - "HM + Hiba 52 ctn closing": HM Fabrics contracts 44 and 55 (84X80 52/52 CTN 61"), closed together
with Hiba Fabrics' 52/1 yarn that went into contract 55. Owner's method: count 53 warp and weft, ÷800, 1 bag = 100 lb,
rejection belongs to the mill. ERP read only; owner inputs are yellow.
Upload:  python 57_closing_hm_hiba.py --upload <google sheet id>
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
OUT = HERE / "financial_statements" / "hm_hiba_52_ctn_closing.xlsx"
CL = "44,55"
YC = "6,18"                       # 52/1 & 54/1 CTN (same yarn)
TODAY = date.today()
NAVY = PatternFill("solid", fgColor="1F3B57"); INPUT = PatternFill("solid", fgColor="FFF4C2")
GREEN = PatternFill("solid", fgColor="D9F2E3"); REJ = PatternFill("solid", fgColor="F8DCDC")
W = Font(bold=True, color="FFFFFF"); B = Font(bold=True); T = Font(bold=True, size=15, color="1F3B57"); S = Font(bold=True, size=12, color="1F3B57")
NOTE = Font(italic=True, color="4B4F5A"); N0, N1, N4 = '#,##0;[Red]-#,##0', '#,##0.0;[Red]-#,##0.0', '0.0000'
BAGEQ = ("CASE WHEN NVL(d.bags,0) = 0 AND NVL(d.cones,0) = 0 THEN NVL(d.yarn_wt,0)/45.36 "
         "ELSE NVL(d.bags,0) + NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0), CASE WHEN y.count > 40 THEN 40 ELSE 24 END) END")


def head(ws, r, labels):
    for i, h in enumerate(labels, 1):
        c = ws.cell(row=r, column=i, value=h); c.font, c.fill = W, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")


def main():
    cur = cc.dash.connect(cc.dash.load_config()).cursor()
    q = lambda s, **b: rows(cur, s, **b)  # noqa: E731
    yl = q(f"""SELECT TO_CHAR(m.td,'YYYY-MM-DD'), m.tt, m.tno, m.gp_no, c.cont_no, a.an, NVL(d.bags,0), NVL(d.cones,0), ROUND({cc.KG},1), {BAGEQ}
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE d.ycode IN ({YC}) AND c.cont_no IN ({CL}) ORDER BY m.td, m.tno""")
    fl = q(f"""SELECT TO_CHAR(v.trans_date,'YYYY-MM-DD'), v.trans_type, v.gp_no, v.inv_no, s.cont_no, v.deliver_to,
        NVL(v.a,0)+NVL(v.a2,0), NVL(v.b,0), NVL(v.c,0)+NVL(v.cp,0), v.total_mtr
        FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no IN ({CL}) ORDER BY v.trans_date, v.trans_no""")
    sets = q(f"""SELECT c.cont_no, b.setno, TO_CHAR(MAX(m.td),'YYYY-MM-DD'), MAX(a.an), COUNT(*), SUM(b.blength), MAX(d.ends)
        FROM dostex.wybeams b JOIN dostex.wytm m ON m.trans_no=b.trans_no JOIN dostex.wytd d ON d.trans_no=b.trans_no AND d.sr_no=b.ref_sr
        JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE c.cont_no IN ({CL}) GROUP BY c.cont_no, b.setno ORDER BY 1, 3""")
    woven = {str(n): float(m or 0) for n, m in q(f"SELECT cont_no, SUM(total_mtr) FROM dostex.wv_prod_v WHERE cont_no IN ({CL}) GROUP BY cont_no")}
    stock = sum(float(t or 0) for (t,) in q(f"""SELECT SUM(v.total_mtr) FROM dostex.wv_fab_stock_v v JOIN dostex.wc_cont_sub_v s
        ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no IN ({CL})"""))
    other = q(f"""SELECT c.cont_no, y.yarn_desc, m.tt, ROUND(SUM({cc.KG}),1) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no
        LEFT JOIN dostex.yarn y ON y.ycode=d.ycode JOIN dostex.contm c ON c.cont_id=d.cont_id
        WHERE d.ycode NOT IN ({YC}) AND c.cont_no IN ({CL}) GROUP BY c.cont_no, y.yarn_desc, m.tt""")
    hiba_other = q(f"""SELECT c.cont_no, MAX(p.an), y.yarn_desc, m.tt, SUM(NVL(d.bags,0)), ROUND(SUM({cc.KG}))
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach p ON p.ac=c.party_code
        WHERE NVL(m.ac1,m.ac2)='200106013' AND c.cont_no NOT IN ({CL}) GROUP BY c.cont_no, y.yarn_desc, m.tt ORDER BY 1""")

    # Hiba delivered ready sized beams; the ERP books them as a yarn IGP + same-day TO_SIZING of the same weight to Hiba
    hiba_siz = {(d, float(kg)) for d, tt, tno, gp, cno, an, bg, cn, kg, beq in yl if tt == "TO_SIZING" and "HIBA" in (an or "")}
    tot = dict(hm=0.0, hiba=0.0, hiba_beams=0.0, ret=0.0, siz=0.0, looms=0.0)
    igp, ret = [], []
    for d, tt, tno, gp, cno, an, bg, cn, kg, beq in yl:
        an = (an or "").strip(); beq = float(beq or 0)
        if tt == "IGP_YARN":
            if (d, float(kg)) in hiba_siz:
                tot["hiba_beams"] += beq
                an = f"HIBA FABRICS - sized beams (ERP name: {an})"
            else:
                tot["hiba" if "HIBA" in an else "hm"] += beq
            igp.append([d, gp or tno, int(cno), an, float(bg), float(cn), float(kg), round(beq, 2)])
        elif tt == "OGP_YARN":
            tot["ret"] += beq; ret.append([d, gp or tno, int(cno), an, float(bg), float(cn), float(kg), round(beq, 2)])
        elif tt in ("TO_SIZING", "FROM_SIZING"):
            tot["siz"] += beq if tt == "TO_SIZING" else -beq
        elif tt in ("YARN_TO_LOOMS", "YARN_FROM_LOOMS"):
            tot["looms"] += beq if tt == "YARN_TO_LOOMS" else -beq
    party_m = rej_m = rej_in_party = 0.0
    for d, tt, gp, inv, cno, dto, a, b, c, t in fl:
        sg = 1 if tt == "GPOUT" else -1
        if "REJECTION" in (dto or "").upper():
            rej_m += sg * float(t or 0)
        else:
            party_m += sg * float(t or 0); rej_in_party += sg * float(c or 0)
    set_len = sum(float(s[5] or 0) for s in sets)
    ends_w = sum(float(s[5] or 0) * float(s[6] or 0) for s in sets) / set_len

    wb = Workbook()
    g = wb.active; g.title = "Closing"
    g.column_dimensions["A"].width = 54
    for col, w in zip("BCDEFG", (15, 15, 15, 15, 15, 50)):
        g.column_dimensions[col].width = w
    g["A1"] = "HM FABRICS + HIBA FABRICS · 52/1 CTN · CONTRACTS 44 & 55 · CLOSING"; g["A1"].font = T
    g["A2"] = f"CLOSED as at {TODAY:%d-%b-%Y} · both contracts finished (last production 27-Nov-2025) · ERP read only"; g["A2"].font = NOTE
    g["A4"] = "QUALITY"; g["B4"] = '84X80  52/52  CTN  61"'; g["A4"].font = B; g["B4"].font = B
    inputs = [("Ends / inch (owner: 84, as on the contract)", 84), ("Picks / inch", 80), ("Width (in)", 61),
              ("Warp closing count", 53), ("Weft closing count", 53), ("Divisor", 800), ("Lb per bag", 100),
              ("Warp rate (Rs / lb)", 610), ("Weft rate (Rs / lb)", 610)]
    for i, (l, v) in enumerate(inputs, 5):
        g.cell(row=i, column=1, value=l); c = g.cell(row=i, column=2, value=v); c.fill = INPUT
    g["B5"].number_format = "0.00"
    # B5 ends B6 picks B7 width B8 warp cnt B9 weft cnt B10 div B11 lb/bag B12 warp rate B13 weft rate
    g["D5"] = "WARP WEIGHT (lb / m)"; g["E5"] = "=B5*B7*1.0936/B10/B8"
    g["D6"] = "WEFT WEIGHT (lb / m)"; g["E6"] = "=B6*B7*1.0936/B10/B9"
    g["D7"] = "TOTAL WEIGHT (lb / m)"; g["E7"] = "=E5+E6"
    g["D9"] = "WARP RATE (Rs / lb)"; g["E9"] = "=B12"
    g["D10"] = "WEFT RATE (Rs / lb)"; g["E10"] = "=B13"
    for r in (5, 6, 7):
        g[f"E{r}"].number_format = N4; g[f"D{r}"].font = B; g[f"E{r}"].font = B
    g["D9"].font = B; g["D10"].font = B

    g["A15"] = "FABRIC (meters)"; g["A15"].font = S
    fab = [("Fabric woven (ERP, all grades)", round(sum(woven.values()))),
           ("Delivered to HM's dyer / printer (Nafees, Ibrahim) - net", round(party_m)),
           ("  of which rejection (C/CP) sent with it", round(rej_in_party)),
           ("Rejection sold by the mill (Khalid Rejection) - mill's", round(rej_m)),
           ("Fabric still in stock", round(stock))]
    for i, (l, v) in enumerate(fab, 16):
        g.cell(row=i, column=1, value=l); g.cell(row=i, column=2, value=v).number_format = N0
    g["A19"].fill = REJ
    # B16 woven B17 party B18 rej in party B19 rej sold B20 stock

    g["A22"] = "YARN ACCOUNT (bags of 100 lb)"; g["A22"].font = S
    head(g, 23, ["", "Warp (bags)", "Weft (bags)", "Total (bags)", "Total (lb)", "Value (Rs)", "How"])
    ya = [("Yarn received from HM Fabrics (raw yarn, IGP)", None, None, round(tot["hm"] + tot["hiba"], 2), "sheet 'Yarn IGP'"),
          ("Received from Hiba Fabrics as ready sized beams (contract 55)", round(tot["hiba_beams"], 2), None, round(tot["hiba_beams"], 2),
           "4 sets; ERP books them as yarn IGP + same-day 'to sizing' of the same kg"),
          ("Less: yarn returned", None, None, -round(tot["ret"], 2), ""),
          ("NET YARN RECEIVED", None, None, "=D24+D25+D26", ""),
          ("Charged in fabric delivered to HM / Hiba", "=-$B$17*$E$5/$B$11", "=-$B$17*$E$6/$B$11", "=B28+C28", "delivered m × warp / weft weight at count 53"),
          ("Fabric / yarn still in stock", None, None, f"=-$B$20*$E$7/$B$11", "nothing left"),
          ("BALANCE", None, None, "=D27+D28+D29", "+ = mill holds their yarn (returnable to HM / Hiba) · − = HM / Hiba owe the mill")]
    for i, (l, wv, fv, tv, how) in enumerate(ya, 24):
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
    for col in range(1, 8):
        g.cell(row=30, column=col).fill = GREEN

    g["A32"] = "CLOSING"; g["A32"].font = S
    g["A33"] = "Balance (bags)"; g["B33"] = "=D30"
    g["A34"] = "Closing value (Rs)"; g["B34"] = "=ABS(F30)"
    g["A35"] = "RESULT"
    g["B35"] = ('=IF(D30>=0,"Returnable to HM / Hiba: "&TEXT(D30,"0.0")&" bags",'
                '"Nothing to return - HM / Hiba owe the mill: fabric used "&TEXT(-D30,"0.0")&" bags more than supplied")')
    g["A36"] = "As % of yarn received"; g["B36"] = "=D30/D27"
    for r, fmt in ((33, N1), (34, N0), (36, "0.00%")):
        g[f"B{r}"].number_format = fmt
    for r in range(33, 37):
        g[f"A{r}"].font = B; g[f"B{r}"].font = B
    g["B35"].fill = GREEN

    g["A38"] = "WHERE THE YARN ACTUALLY WENT (ERP, bags)"; g["A38"].font = S
    head(g, 39, ["", "Bags", "Lb per m woven", "", "", "", "Note"])
    wl = [("Warp sent to sizing (net)", round(tot["siz"], 2), "=B40*$B$11/$B$16", "sets on sheet 'Sets'"),
          ("Weft issued to looms (net)", round(tot["looms"], 2), "=B41*$B$11/$B$16", ""),
          ("Yarn left in ERP stock", "=D27-B40-B41", "", "≈ nothing left - both contracts finished"),
          ("TOTAL", "=SUM(B40:B42)", "=C40+C41", "actual yarn per meter woven, vs closing weight in E7"),
          ("Rejection sold by the mill (in the yarn above)", "=B19*$E$7/$B$11", "", "mill's - sold to Khalid Rejection"),
          ("Yarn used above the count-53 closing weight", "=(C43-E7)*B16/$B$11", "", "actual use over the allowance, on all fabric woven")]
    for i, (l, v, lbm, note) in enumerate(wl, 40):
        g.cell(row=i, column=1, value=l)
        g.cell(row=i, column=2, value=v).number_format = N1
        if lbm:
            g.cell(row=i, column=3, value=lbm).number_format = N4
        g.cell(row=i, column=7, value=note).font = NOTE
        if l.isupper():
            for col in (1, 2, 3):
                g.cell(row=i, column=col).font = B
    g["A47"] = "NOTES"; g["A47"].font = S
    notes = ["Hiba Fabrics also did the sizing for contract 55 (the 4 sets from 'HIBA FABRICS', 103 bags sent, 102 bags of beams back).",
             "Fabric delivered includes the rejection (C/CP) that went to HM's dyer with the fresh fabric; the rejection sold to Khalid is the mill's.",
             "Ends 84 per inch (owner, as on the contract); the sets carry 10,160-10,240 ends on 2 pieces × 61\".",
             "225D/72F polyester (selvedge) is not in the cotton account: " + "; ".join(f"contract {c}: {tt} {kg:,.0f} kg" for c, yd, tt, kg in other)]
    if hiba_other:
        notes.append("Hiba Fabrics' yarn on OTHER contracts (not in this closing): " +
                     "; ".join(f"contract {c} ({p}) {yd} {tt} {int(bg or 0)} bags" for c, p, yd, tt, bg, kg in hiba_other))
    for i, t in enumerate(notes, 48):
        g.cell(row=i, column=1, value="• " + t).font = NOTE
    g.freeze_panes = "A4"

    y = wb.create_sheet("Yarn IGP")
    y["A1"] = "Yarn received - 52/1 CTN - contracts 44 & 55"; y["A1"].font = T
    head(y, 3, ["Date", "IGP / GP", "Contract", "From", "Bags", "Cones", "Kg", "Bag equivalent"])
    for i, w in enumerate([12, 10, 10, 22, 9, 9, 11, 13], 1):
        y.column_dimensions[y.cell(row=3, column=i).column_letter].width = w
    for r in igp:
        y.append(r)
    n1 = y.max_row
    y.append(["TOTAL", "", "", "", f"=SUM(E4:E{n1})", f"=SUM(F4:F{n1})", f"=SUM(G4:G{n1})", f"=SUM(H4:H{n1})"])
    for col in range(1, 9):
        y.cell(row=y.max_row, column=col).font = B
    y.append([]); y.append(["HM total", "", "", "", "", "", "", f'=SUMIF(D4:D{n1},"HM*",H4:H{n1})'])
    y.append(["Hiba total", "", "", "", "", "", "", f'=SUMIF(D4:D{n1},"HIBA*",H4:H{n1})'])
    if ret:
        y.append([]); y.append(["Returned"])
        for r in ret:
            y.append(r)

    f = wb.create_sheet("Fabric OGP")
    f["A1"] = "Fabric gate passes - contracts 44 & 55 - with grades"; f["A1"].font = T
    head(f, 3, ["Date", "Type", "GP", "Invoice", "Contract", "Delivered to", "A (fresh)", "B", "Rejection C/CP", "Total meters", "Whose"])
    for i, w in enumerate([12, 8, 7, 9, 9, 36, 11, 9, 12, 12, 16], 1):
        f.column_dimensions[f.cell(row=3, column=i).column_letter].width = w
    for d, tt, gp, inv, cno, dto, a, b, c, t in fl:
        sg = 1 if tt == "GPOUT" else -1
        mine = "REJECTION" in (dto or "").upper()
        f.append([d, "OGP" if sg == 1 else "Return", gp, inv, int(cno), (dto or "").strip(), sg * float(a), sg * float(b), sg * float(c),
                  sg * float(t or 0), "Mill (rejection sold)" if mine else "HM"])
        if mine:
            for col in range(1, 12):
                f.cell(row=f.max_row, column=col).fill = REJ
    n2 = f.max_row
    f.append(["TOTAL", "", "", "", "", "", f"=SUM(G4:G{n2})", f"=SUM(H4:H{n2})", f"=SUM(I4:I{n2})", f"=SUM(J4:J{n2})"])
    f.append(["  to HM", "", "", "", "", "", "", "", "", f'=SUMIF(K4:K{n2},"HM",J4:J{n2})'])
    f.append(["  rejection sold by mill", "", "", "", "", "", "", "", "", f'=SUMIF(K4:K{n2},"Mill*",J4:J{n2})'])
    for rr in range(n2 + 1, f.max_row + 1):
        for col in range(1, 12):
            f.cell(row=rr, column=col).font = B
            if col >= 7:
                f.cell(row=rr, column=col).number_format = N0

    st = wb.create_sheet("Sets")
    st["A1"] = "Sized sets - contracts 44 & 55"; st["A1"].font = T
    head(st, 3, ["Contract", "Set", "Received", "Sizer", "Beams", "Beam length total (m)", "Ends"])
    for i, w in enumerate([10, 10, 12, 30, 8, 16, 9], 1):
        st.column_dimensions[st.cell(row=3, column=i).column_letter].width = w
    for s in sets:
        st.append([int(s[0]), s[1], s[2], (s[3] or "").strip(), int(s[4]), float(s[5] or 0), int(s[6] or 0)])
    st.append(["TOTAL", "", "", "", f"=SUM(E4:E{st.max_row})", f"=SUM(F4:F{st.max_row})", round(ends_w)])

    m = wb.create_sheet("Method")
    for i, t in enumerate([
        "HOW THIS CLOSING WAS MADE",
        "Weight per meter = ends (or picks) per inch × width × 1.0936 ÷ 800 ÷ count; count 53 for warp and weft (owner, 28-Sep-2026).",
        "Yarn received in bags as on the gate pass (cones counted as part-bags, 40 cones a bag); 1 bag = 100 lb.",
        "HM is charged for the fabric delivered to its dyer / printer (incl. rejection sent with it). Rejection sold by the mill is the mill's.",
        "Balance + = the mill still holds HM's yarn on paper (payable to HM); − = HM owes the mill.",
        "'Where the yarn actually went' uses the ERP: warp sent to sizing and weft issued to looms, to show real consumption per meter.",
        "ERP data read only; closing made in this sheet.",
    ], 1):
        m.cell(row=i, column=1, value=t).font = T if i == 1 else Font()
    m.column_dimensions["A"].width = 140
    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    print({k: round(v, 2) for k, v in tot.items()}, "party m", round(party_m), "rej sold", round(rej_m), "stock", round(stock),
          "ends", round(ends_w, 1), "woven", woven, "hiba other", hiba_other)


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
