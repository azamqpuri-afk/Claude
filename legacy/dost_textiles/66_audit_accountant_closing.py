"""
66_audit_accountant_closing.py - audit the accountant's contract closing Excel (e.g. 'contract no 38.xlsx') against the ERP.
Read only. Checks: every OGP line (date, meters, rejection, party, rate, value) against FAB_GP_V by invoice/OGP number;
OGPs in the ERP missing from the sheet; each line's arithmetic (value = fresh m x rate, rate = picks x per pick, warp/weft bags);
column totals; sized sets (IGP no, bags, ends, length, production) against WYBEAMS; yarn received / consumed / returnable.
python 66_audit_accountant_closing.py "<file.xlsx>" <contract no> [--upload <google sheet id>]
"""
import configparser
import importlib.util
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("d", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)
NAVY = PatternFill("solid", fgColor="1F3B57"); W = Font(bold=True, color="FFFFFF"); B = Font(bold=True)
T = Font(bold=True, size=15, color="1F3B57"); NOTE = Font(italic=True, color="4B4F5A")
SEV = {"ERROR": "F8C9C9", "CHECK": "FFE3B3", "INFO": "E8EDF2", "OK": "D9F2E3"}


def shiftcol(c, shift):
    """column letter moved left by `shift` (the later contract sheets have the sets block 2 columns further left)"""
    from openpyxl.utils import column_index_from_string, get_column_letter
    return get_column_letter(column_index_from_string(c) - shift)


def main(path, cno, upload_id=None, sheet=None, quiet=False):
    wb_in = load_workbook(path, data_only=True); ws = wb_in[sheet] if sheet else wb_in.active
    # sets block: contract 38's sheet has it at AJ..AT (AH = 60 CTN weft); the others at AH..AR
    sh = 0 if str(ws["AJ4"].value or "").strip().upper().startswith("SIZING") else 2
    cur = dash.connect(dash.load_config()).cursor()
    q = lambda s: cur.execute(s).fetchall()  # noqa: E731
    F = []                       # findings: (severity, area, item, sheet says, ERP says, note)

    def add(sev, area, item, sheet, erp, note=""):
        F.append((sev, area, item, sheet, erp, note))

    num = lambda v: float(v) if isinstance(v, (int, float)) else 0.0  # noqa: E731
    # ---- read the sheet (layout of the accountant's closing: header row 4, data from row 5, totals on the first row with P/Q sums)
    ogp, sets, section = [], [], ""
    total_row = None
    for r in range(5, ws.max_row + 1):
        if ws[f"A{r}"].value:
            section = str(ws[f"A{r}"].value).strip()
        if ws[f"M{r}"].value is None and ws[f"Q{r}"].value is not None and ws[f"AC{r}"].value is not None and not ws[f"B{r}"].value:
            total_row = r; break
        if ws[f"N{r}"].value and isinstance(ws[f"M{r}"].value, datetime):
            ogp.append(dict(r=r, sec=section, date=ws[f"M{r}"].value, no=int(ws[f"N{r}"].value), lot=ws[f"O{r}"].value, than=num(ws[f"P{r}"].value),
                            mtr=num(ws[f"Q{r}"].value), rej=num(ws[f"S{r}"].value), fresh=num(ws[f"U{r}"].value), to=str(ws[f"V{r}"].value or "").strip(),
                            rejigp=ws[f"X{r}"].value, pick=num(ws[f"AA{r}"].value), rate=num(ws[f"AB{r}"].value), value=num(ws[f"AC{r}"].value),
                            warp=num(ws[f"AF{r}"].value), weft=num(ws[f"AG{r}"].value), weft2=num(ws[f"AH{r}"].value), wcount=ws[f"K{r}"].value,
                            fcount=ws[f"L{r}"].value, ends=ws[f"B{r}"].value, picks=ws[f"D{r}"].value, width=ws[f"J{r}"].value))
        K = lambda c: ws[f"{shiftcol(c, sh)}{r}"].value  # noqa: E731
        if isinstance(K("AJ"), (int, float)) and r != total_row:
            sets.append(dict(r=r, igp=int(K("AJ")), date=K("AK"), bags=num(K("AL")), cones=num(K("AM")),
                             ends=num(K("AN")), length=num(K("AO")), width=num(K("AP")), tlen=num(K("AQ")),
                             prod=num(K("AR")), sizer=str(K("AS") or "").strip(), shr=num(K("AT"))))
    summ = {}
    for r in range((total_row or 80) + 1, ws.max_row + 1):
        if ws[f"M{r}"].value and ws[f"N{r}"].value is not None:
            summ[str(ws[f"M{r}"].value).strip()] = (r, num(ws[f"N{r}"].value))

    # ---- 1. arithmetic on each line
    for o in ogp:
        tag = f"OGP {o['no']} ({o['date']:%d-%b-%y})" if isinstance(o["date"], datetime) else f"OGP {o['no']}"
        if abs(o["mtr"] - o["rej"] - o["fresh"]) > 0.5:
            add("ERROR", "Arithmetic", tag, f"mtr {o['mtr']:,.0f} - rejected {o['rej']:,.0f} = {o['fresh']:,.0f}", "", "meters minus rejected does not equal fresh meters")
        if o["picks"] and abs(o["picks"] * o["pick"] - o["rate"]) > 0.01 and o["rate"]:
            add("ERROR", "Arithmetic", tag, f"rate {o['rate']}", f"{o['picks']} picks x {o['pick']} = {o['picks'] * o['pick']:.2f}", "rate does not equal picks x rate per pick")
        if abs(o["fresh"] * o["rate"] - o["value"]) > 1:
            add("ERROR", "Arithmetic", tag, f"value {o['value']:,.2f}", f"{o['fresh']:,.0f} x {o['rate']} = {o['fresh'] * o['rate']:,.2f}", "value is not fresh meters x rate")
        if o["ends"] and o["width"] and o["wcount"]:
            wb_ = o["fresh"] * o["ends"] * o["width"] * 1.0936 / 800 / 51.5 / 100
            wf_ = o["fresh"] * o["picks"] * o["width"] * 1.0936 / 800 / 52.5 / 100
            if abs(o["warp"] - wb_) > 0.01:
                add("CHECK", "Arithmetic", tag, f"warp bags {o['warp']:.3f}", f"{wb_:.3f} (ends {o['ends']}, count 51.5)", "warp bags differ from fresh m x warp weight")
            got_wf = o["weft"] or o["weft2"]
            if o["weft2"]:     # the 60 CTN weft rows use count 60 x ... - check against the sheet's own ratio
                pass
            elif abs(got_wf - wf_) > 0.01:
                add("CHECK", "Arithmetic", tag, f"weft bags {got_wf:.3f}", f"{wf_:.3f} (picks {o['picks']}, count 52.5)", "weft bags differ from fresh m x weft weight")
    # ---- 2. totals row
    if total_row:
        for col, key in (("Q", "mtr"), ("S", "rej"), ("U", "fresh"), ("AC", "value")):
            sheet_t = num(ws[f"{col}{total_row}"].value); calc = sum(o[key] for o in ogp)
            add("OK" if abs(sheet_t - calc) < 1 else "ERROR", "Totals", f"total {key} (row {total_row})", f"{sheet_t:,.2f}", f"sum of lines {calc:,.2f}",
                "" if abs(sheet_t - calc) < 1 else "the total does not equal the sum of its lines")
    # ---- 3. OGPs against the ERP (by invoice / OGP number)
    erp = defaultdict(list)
    for tt, inv, gp, td, dto, than, a, a2, b, c, cp, mtr, rate, amt, cn in q(f"""SELECT v.trans_type, v.inv_no, v.gp_no, v.trans_date, v.deliver_to, v.than,
            v.a, v.a2, v.b, v.c, v.cp, v.total_mtr, v.rate, v.amt, s.cont_no FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id
            WHERE s.cont_no = {cno} OR v.inv_no IN ({",".join(str(o['no']) for o in ogp) or "0"})"""):
        erp[(tt, inv if tt == "GPOUT" else f"IGP-{gp}")].append(dict(gp=gp, date=td, to=(dto or "").strip(), than=float(than or 0), mtr=float(mtr or 0), rej=float(c or 0) + float(cp or 0),
                                   rate=float(rate or 0), amt=float(amt or 0), cno=int(cn)))
    seen = set()
    by_no = defaultdict(list)
    for o in ogp:
        by_no[o["no"]].append(o)
    for no, L in by_no.items():
        E = erp.get(("GPOUT", no), [])
        seen.add(("GPOUT", no))
        tag = f"OGP {no}"
        if not E:
            add("ERROR", "OGP vs ERP", tag, f"{sum(o['mtr'] for o in L):,.0f} m to {L[0]['to']}", "not found in the ERP", "OGP number not in the ERP fabric gate passes")
            continue
        sm, em = sum(o["mtr"] for o in L), sum(e["mtr"] for e in E if e["cno"] == int(cno))
        eo = sum(e["mtr"] for e in E if e["cno"] != int(cno))
        if abs(sm - em) > 0.5:
            add("ERROR", "OGP vs ERP", tag, f"{sm:,.2f} m ({', '.join(o['sec'] for o in L)})", f"{em:,.2f} m on contract {cno}", "meters differ from the ERP")
        else:
            add("OK", "OGP vs ERP", tag, f"{sm:,.2f} m", f"{em:,.2f} m on contract {cno}" + (f" (the same OGP also carried {eo:,.0f} m of contract(s) "
                f"{sorted({e['cno'] for e in E if e['cno'] != int(cno)})})" if eo else ""), "")
        d0 = L[0]["date"].date() if isinstance(L[0]["date"], datetime) else None
        if d0 and E[0]["date"].date() != d0:
            add("CHECK", "OGP vs ERP", tag, f"date {d0:%d-%b-%y}", f"{E[0]['date']:%d-%b-%y}", "date differs")
        rates = {round(e["rate"], 2) for e in E if e["rate"]}
        srates = {round(o["rate"], 2) for o in L if o["rate"]}
        if rates and srates and not (srates <= rates or rates <= srates):
            add("CHECK", "OGP vs ERP", tag, f"rate {sorted(srates)}", f"ERP rate {sorted(rates)}", "rate differs from the ERP gate pass")
        if not rates and srates:
            add("INFO", "OGP vs ERP", tag, f"rate {sorted(srates)}", "no rate in the ERP", "dispatch not invoiced in the ERP")
        srej, erej = sum(o["rej"] for o in L), sum(e["rej"] for e in E)
        if abs(srej - erej) > 0.5:
            add("INFO", "OGP vs ERP", tag, f"rejected {srej:,.0f} m", f"C/CP on the gate pass {erej:,.0f} m", "rejection: sheet = rejected later by the party; ERP = graded C/CP when dispatched")
    sheet_rej = sum(o["rej"] for o in ogp)
    for (tt, inv), E in erp.items():
        if (tt, inv) in seen or not any(e["cno"] == int(cno) for e in E):
            continue
        m = sum(e["mtr"] for e in E if e["cno"] == int(cno))
        if tt == "GPOUT":
            add("INFO" if "REJECTION" in E[0]["to"].upper() else "ERROR", "ERP not in sheet", f"OGP {inv} ({E[0]['date']:%d-%b-%y})", "not in the sheet",
                f"{m:,.2f} m to {E[0]['to']}", "mill's own rejection sale - rightly not billed to the party" if "REJECTION" in E[0]["to"].upper()
                else "dispatch missing from the closing - not billed")
        else:
            add("INFO", "Fabric returned (ERP)", f"Return {inv} ({E[0]['date']:%d-%b-%y})", "", f"{m:,.2f} m back: {E[0]['to']}", "compare with the sheet's rejected meters")
    erp_ret = sum(e["mtr"] for (tt, k), E in erp.items() if tt != "GPOUT" for e in E if e["cno"] == int(cno))
    add("OK" if abs(erp_ret - sheet_rej) < 1 else "CHECK", "Fabric returned (ERP)", "rejected / returned fabric - total", f"{sheet_rej:,.2f} m rejected (not billed)",
        f"{erp_ret:,.2f} m returned in the ERP", "" if abs(erp_ret - sheet_rej) < 1 else
        f"{erp_ret - sheet_rej:,.2f} m returned in the ERP but still billed in the sheet (x rate {ogp[0]['rate']} = Rs {(erp_ret - sheet_rej) * ogp[0]['rate']:,.0f})")
    # ---- 4. ERP fabric position of the contract
    woven = q(f"SELECT SUM(total_mtr) FROM dostex.wv_prod_v WHERE cont_no={cno}")[0][0] or 0
    stock = q(f"""SELECT SUM(NVL(v.a,0)+NVL(v.a2,0)), SUM(NVL(v.b,0)), SUM(NVL(v.c,0)+NVL(v.cp,0)) FROM dostex.wv_fab_stock_v v
                  JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no={cno}""")[0]
    if any(float(x or 0) for x in stock):
        add("CHECK", "ERP position", f"contract {cno} fabric still in ERP stock", "contract closed", f"A {float(stock[0] or 0):,.0f} · B {float(stock[1] or 0):,.0f} · C/CP {float(stock[2] or 0):,.0f} m",
            "ERP still shows fabric of this contract in stock - dispatch or adjust it, or it is missing from the closing")
    add("OK" if abs(float(woven) - sum(s_["prod"] for s_ in sets)) < 1 else "CHECK", "ERP position", "fabric woven", f"{sum(s_['prod'] for s_ in sets):,.0f} m (sets production)", f"{float(woven):,.0f} m", "")
    # ---- 5. sized sets against WYBEAMS / IGP
    eset = {}
    for tno, td, bags, cones, ends, bl, n_, sizer in q(f"""SELECT m.tno, m.td, SUM(d.bags)/COUNT(DISTINCT b.sr_no), SUM(d.cones)/COUNT(DISTINCT b.sr_no), MAX(d.ends), SUM(b.blength), COUNT(*), MAX(a.an)
            FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no JOIN dostex.wybeams b ON b.trans_no=d.trans_no AND b.ref_sr=d.sr_no
            JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
            WHERE m.tt='IGP_SIZED_BEAMS' AND c.cont_no={cno} GROUP BY m.tno, m.td"""):
        eset[int(tno)] = dict(date=td, bags=float(bags or 0), cones=float(cones or 0), ends=int(ends or 0), blen=float(bl or 0), beams=n_, sizer=(sizer or "").strip())
    for s_ in sets:
        e = eset.get(s_["igp"])
        tag = f"Set IGP {s_['igp']}"
        if not e:
            add("CHECK", "Sets vs ERP", tag, f"{s_['bags']:.0f} bags {s_['cones']:.0f} cones, {s_['tlen']:,.0f} m, {s_['sizer']}", "no sized-beam IGP with this number on the contract",
                "IGP number differs or set booked to another contract")
            continue
        diffs = []
        if abs(e["bags"] - s_["bags"]) > 0.5 or abs(e["cones"] - s_["cones"]) > 0.5:
            diffs.append(f"bags/cones ERP {e['bags']:.0f}/{e['cones']:.0f}")
        if e["ends"] and abs(e["ends"] - s_["ends"]) > 0.5:
            diffs.append(f"ends ERP {e['ends']}")
        if abs(e["blen"] - s_["length"]) > 5:          # a few meters is rounding of the beam lengths
            diffs.append(f"length ERP {e['blen']:,.0f} ({e['blen'] - s_['length']:+,.0f} m - only the shrinkage column uses it)")
        sz = {"HTBS": "HAJI TAJ BABU"}.get(s_["sizer"].upper(), s_["sizer"].split()[0].upper() if s_["sizer"] else "")
        if sz not in e["sizer"].upper():
            diffs.append(f"sizer ERP {e['sizer']}")
        if abs(s_["length"] * s_["width"] - s_["tlen"]) > 1:
            diffs.append(f"T length {s_['tlen']:,.0f} is not length x width {s_['length'] * s_['width']:,.0f}")
        if s_["tlen"] and abs((s_["tlen"] - s_["prod"]) / s_["tlen"] * 100 - s_["shr"]) > 0.01:
            diffs.append(f"shrinkage {s_['shr']:.2f}% is not (T length - production) / T length")
        add("CHECK" if diffs else "OK", "Sets vs ERP", tag, f"{s_['bags']:.0f}/{s_['cones']:.0f}, ends {s_['ends']:.0f}, {s_['length']:,.0f} m, {s_['sizer']}",
            "; ".join(diffs) or "matches", "")
    for tno, e in eset.items():
        if tno not in {s_["igp"] for s_ in sets}:
            add("CHECK", "Sets vs ERP", f"Set IGP {tno} ({e['date']:%d-%b-%y})", "not in the sheet", f"{e['bags']:.0f} bags, {e['beams']} beams, {e['blen']:,.0f} m, {e['sizer']}", "sized set missing from the closing")
    # ---- 6. yarn
    yr = q(f"""SELECT y.yarn_desc, m.tt, SUM(NVL(d.bags,0)), SUM(NVL(d.cones,0)) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no
               JOIN dostex.yarn y ON y.ycode=d.ycode JOIN dostex.contm c ON c.cont_id=d.cont_id WHERE c.cont_no={cno} GROUP BY y.yarn_desc, m.tt ORDER BY 1,2""")
    rec = {}
    for yd, tt, bg, cn in yr:
        rec.setdefault(yd.strip(), {})[tt] = (float(bg or 0), float(cn or 0))
    for k, (r, v) in summ.items():
        if "YARN" in k.upper() and ("RECEIVED" in k.upper() or "RCD" in k.upper()):
            erp_txt = "; ".join(f"{yd}: IGP {v_.get('IGP_YARN', (0, 0))[0]:.0f} bags, returned {v_.get('OGP_YARN', (0, 0))[0]:.0f}" for yd, v_ in rec.items() if "IGP_YARN" in v_ or "OGP_YARN" in v_)
            add("INFO", "Yarn", k, f"{v:,.2f} lb = {abs(v) / 100:,.2f} bags", erp_txt, "")
    # yarn transferred in from another contract (e.g. "YARN RCD 60 CTN (TRANSFER CONT#40)") - is it in the ERP on this contract?
    for k, (r, v) in summ.items():
        if "TRANSFER" in k.upper():
            cnt_ = "".join(ch for ch in k.split("CTN")[0] if ch.isdigit())[-2:]
            iss = q(f"""SELECT SUM(NVL(d.bags,0)+NVL(d.cones,0)/40) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no JOIN dostex.yarn y ON y.ycode=d.ycode
                        JOIN dostex.contm c ON c.cont_id=d.cont_id WHERE c.cont_no={cno} AND y.yarn_desc LIKE '{cnt_}/%' AND m.tt IN ('YARN_TO_LOOMS','IGP_YARN')""")[0][0] or 0
            add("OK" if abs(float(iss) - abs(v) / 100) < 0.5 else "CHECK", "Yarn", k, f"{abs(v) / 100:,.2f} bags", f"{float(iss):,.2f} bags of {cnt_}/1 on contract {cno} in the ERP",
                "" if abs(float(iss) - abs(v) / 100) < 0.5 else "the transfer is not booked to this contract in the ERP - the rest was issued under the other contract's number")
    # consumption re-computed from the sheet's own lines
    warp_lb = sum(o["warp"] for o in ogp) * 100
    weft_lb = sum(o["weft"] for o in ogp) * 100
    weft2_lb = sum(o["weft2"] for o in ogp) * 100
    for k, (r, v) in summ.items():
        if k.upper().startswith("TOTAL YARN CONSUMED"):
            first = r < min((rr for kk, (rr, vv) in summ.items() if "60" in kk), default=10 ** 6)
            calc = warp_lb + weft_lb if first else weft2_lb
            add("OK" if abs(calc - v) < 1 else "ERROR", "Yarn", f"{k} (row {r})", f"{v:,.3f} lb", f"sum of line bags {calc:,.3f} lb", "")
    OUT = HERE / "financial_statements" / f"audit_accountant_contract_{cno}.xlsx"
    wb = Workbook(); o_ = wb.active; o_.title = "Findings"
    o_["A1"] = f"AUDIT - accountant's closing of contract {cno} ({Path(path).name}) against the ERP · {date.today():%d-%b-%Y}"; o_["A1"].font = T
    o_["A2"] = "ERP read only. ERROR = wrong in the sheet · CHECK = differs from the ERP, needs a look · INFO = explained difference · OK = verified."; o_["A2"].font = NOTE
    hd = ["#", "Severity", "Area", "Item", "Sheet says", "ERP / recomputed says", "Note"]
    for i, (h, w) in enumerate(zip(hd, [5, 9, 16, 30, 40, 50, 60]), 1):
        c = o_.cell(row=4, column=i, value=h); c.font, c.fill = W, NAVY; o_.column_dimensions[c.column_letter].width = w
    order = {"ERROR": 0, "CHECK": 1, "INFO": 2, "OK": 3}
    for i, f_ in enumerate(sorted(F, key=lambda x: (order[x[0]], x[1])), 1):
        o_.append([i] + list(f_))
        o_.cell(row=o_.max_row, column=2).fill = PatternFill("solid", fgColor=SEV[f_[0]])
        for col in range(1, 8):
            o_.cell(row=o_.max_row, column=col).alignment = Alignment(wrap_text=True, vertical="top")
    o_.freeze_panes = "A5"; o_.auto_filter.ref = f"A4:G{o_.max_row}"
    OUT.parent.mkdir(exist_ok=True); wb.save(OUT)
    cnt = defaultdict(int)
    for f_ in F:
        cnt[f_[0]] += 1
    print(dict(cnt), "ogp lines", len(ogp), "sets", len(sets), "total row", total_row)
    for f_ in sorted(F, key=lambda x: (order[x[0]], x[1])):
        if f_[0] != "OK":
            print(" | ".join(str(x) for x in f_))
    if upload_id:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        creds = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
        build("drive", "v3", credentials=creds).files().update(fileId=upload_id, media_body=MediaFileUpload(
            str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
        print("uploaded", upload_id)


if __name__ == "__main__":
    a = sys.argv[1:]
    up = a[a.index("--upload") + 1] if "--upload" in a else None
    sht = a[a.index("--sheet") + 1] if "--sheet" in a else None
    main(a[0], a[1], up, sht)
