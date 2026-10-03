"""
68_audit_52ctn_workbook.py - audit the accountant's combined Qadri 52/54 cotton closing workbook (sheets 38, 50, 60, 68, 70,
SUMMARY, FABRIC STOCK, REJECTION) against the ERP and against Claude's grand closing (55_). ERP read only.
Line checks per contract sheet come from 66_audit_accountant_closing.py; this adds the workbook-level stock / summary
checks and a bag-by-bag reconciliation of the two closings.
python 68_audit_52ctn_workbook.py "<52 CTN.xlsx>" [--upload <google sheet id>]
"""
import importlib.util
import io
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_a = importlib.util.spec_from_file_location("a66", HERE / "66_audit_accountant_closing.py"); a66 = importlib.util.module_from_spec(_a); _a.loader.exec_module(a66)
dash = a66.dash
CLOSING_55 = "1yQ388Fm1_cLDNOMk5J6yFyPra_3WZYPsOGZPj-sI59M"
OUT = HERE / "financial_statements" / "audit_accountant_52ctn_workbook.xlsx"
NAVY = PatternFill("solid", fgColor="1F3B57"); W = Font(bold=True, color="FFFFFF"); B = Font(bold=True)
T = Font(bold=True, size=14, color="1F3B57"); NOTE = Font(italic=True, color="4B4F5A")
SEV = a66.SEV; N1 = '#,##0.0;[Red]-#,##0.0'
WP = 90.43 * 46 * 1.0936 / 800 / 51.5; WF = 82 * 46 * 1.0936 / 800 / 52.5      # owner's closing weights (lb / m)
WP90 = 90 * 46 * 1.0936 / 800 / 51.5


def gsheet(fid):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    c = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
    return load_workbook(io.BytesIO(build("drive", "v3", credentials=c).files().export(
        fileId=fid, mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").execute()), data_only=True)


def main(path, upload_id=None):
    v = load_workbook(path, data_only=True); f = load_workbook(path)
    cur = dash.connect(dash.load_config()).cursor()
    q = lambda s: cur.execute(s).fetchall()  # noqa: E731
    F = []

    def add(sev, area, item, sheet, erp, note=""):
        F.append((sev, area, item, sheet, erp, note))

    # ---- 1. line-level audit of each contract sheet (66_)
    import contextlib
    for cno in ("38", "50", "60", "68", "70"):
        with contextlib.redirect_stdout(io.StringIO()):
            res = run66(path, cno)
        for sev, area, item, sh, erp, note in res:
            # in the combined workbook the fabric stock is pooled on the FABRIC STOCK sheet (checked below), and set
            # lengths only feed the shrinkage column - neither changes the closing
            if area == "ERP position" and "still in ERP stock" in str(item):
                sev, note = "INFO", "stock is pooled on the FABRIC STOCK / REJECTION sheets - checked there"
            if area == "Sets vs ERP" and "only the shrinkage column" in str(erp) and "sizer" not in str(erp) and "bags" not in str(erp):
                sev = "INFO"
            if sev != "OK":
                F.append((sev, f"Contract {cno} · {area}", item, sh, erp, note))

    # ---- 2. accountant's summary figures
    S = v["SUMMARY"]
    acc = {S[f"B{r}"].value: float(S[f"C{r}"].value or 0) for r in range(2, 10) if S[f"B{r}"].value}
    acc_total = float(S["C9"].value)
    s68, s70 = v["68"], v["70"]
    recv = {"38": -v["38"]["N74"].value, "50": -v["50"]["N65"].value, "60": -v["60"]["N56"].value, "68": -s68["N18"].value, "70": -s70["N14"].value}
    cons = {"38": v["38"]["N75"].value, "50": v["50"]["N66"].value, "60": v["60"]["N57"].value, "68": s68["N19"].value, "70": s70["N15"].value}
    fab_bags = float(s68["T27"].value) + float(s68["U27"].value)
    beams_acc = float(s68["U29"].value or 0) + float(s70["U25"].value or 0)
    hand_acc = float(s70["T26"].value or 0)
    siz_acc = float(S["C8"].value)
    recon_acc = sum(recv.values()) / 100 - sum(cons.values()) / 100 - fab_bags - beams_acc - hand_acc - siz_acc
    add("OK" if abs(recon_acc + acc_total) < 0.05 else "ERROR", "Summary", "TOTAL P/A (SUMMARY C9)", f"{acc_total:,.2f} bags",
        f"received {sum(recv.values()) / 100:,.2f} - consumed {sum(cons.values()) / 100:,.2f} - fabric {fab_bags:,.2f} - beams {beams_acc:,.2f} - in hand {hand_acc:,.2f} - sizing {siz_acc:,.2f} = {recon_acc:,.2f}",
        "the summary total re-adds from its parts")

    # ---- 3. ERP facts
    BQ = "NVL(d.bags,0)+NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0),40)"
    erp_recv = {str(c): float(b) for c, b in q(f"""SELECT c.cont_no, SUM(CASE m.tt WHEN 'IGP_YARN' THEN 1 ELSE -1 END*({BQ})) FROM dostex.wytm m
        JOIN dostex.wytd d ON d.trans_no=m.trans_no JOIN dostex.contm c ON c.cont_id=d.cont_id WHERE m.tt IN ('IGP_YARN','OGP_YARN') AND d.ycode IN (6,18)
        AND c.cont_no IN (38,50,60,68,70) AND NOT (m.tt='OGP_YARN' AND REGEXP_LIKE(NVL(m.tdesc,' '),'@ *[0-9]')) GROUP BY c.cont_no""")}
    for c_ in recv:
        add("OK" if abs(recv[c_] / 100 - erp_recv.get(c_, 0)) < 0.05 else "ERROR", "Yarn received", f"contract {c_}", f"{recv[c_] / 100:,.2f} bags",
            f"{erp_recv.get(c_, 0):,.2f} bags (ERP IGP - returns)", "")
    for sh, cell in (("68", "M18"), ("70", "M14")):
        if "36/2" in str(v[sh][cell].value):
            add("ERROR", "Labels", f"sheet {sh} {cell}", str(v[sh][cell].value).strip(), "52/1 CTN (ERP yarn on this contract)",
                "label copied from a 36/2 VP closing - the figure is the 54/1 cotton received")
    for sh in ("68", "70"):
        if "JULY" in str(v[sh]["B3"].value).upper():
            add("CHECK", "Labels", f"sheet {sh} B3", str(v[sh]["B3"].value), "OGPs and stock run to 29-Sep-2026", "'as on' date not updated")
    if not isinstance(f["68"]["AF5"].value, str):
        add("CHECK", "Formulas", "sheet 68", "all figures typed in as values", "the other sheets use formulas",
            "cannot be re-calculated if a line changes; the values themselves re-add correctly")
    add("CHECK", "Labels", "sheet 70 rows 25-26", f"'WEFT IN HAND' {hand_acc:.0f} typed in the WARP column, 'SIZED BEAM STOCK' {s70['U25'].value} in the WEFT column",
        "sized beams are warp; yarn in hand is weft", "columns swapped - the total is not affected")
    st = q("""SELECT SUM(NVL(v.a,0)+NVL(v.a2,0)), SUM(NVL(v.b,0)), SUM(NVL(v.c,0)+NVL(v.cp,0)) FROM dostex.wv_fab_stock_v v
              JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id WHERE s.cont_no IN (38,50,60,68,70)""")[0]
    erp_tot = sum(float(x or 0) for x in st)
    fs = v["FABRIC STOCK"]; rj = v["REJECTION"]
    acc_fresh = float(fs["M37"].value); acc_rej = float(rj["M37"].value); acc_tot = acc_fresh + acc_rej
    add("CHECK" if abs(acc_tot - erp_tot) > 1 else "OK", "Fabric stock", "total fabric in stock (FABRIC STOCK + REJECTION)", f"{acc_tot:,.0f} m (typed 148,413)",
        f"{erp_tot:,.0f} m (ERP: A {float(st[0]):,.0f} · B {float(st[1]):,.0f} · C/CP {float(st[2]):,.0f})",
        f"{erp_tot - acc_tot:,.0f} m more in the ERP = {(erp_tot - acc_tot) * (WP + WF) / 100:,.1f} bags")
    add("CHECK", "Fabric stock", "rejection in stock (mill's)", f"{acc_rej:,.0f} m (the balancing figure 148,413 - fresh)",
        f"owner's figure 45,000 m · ERP C/CP {float(st[2]):,.0f} m", f"with 45,000 m of rejection, fresh stock is {erp_tot - 45000:,.0f} m, not {acc_fresh:,.0f} m")
    add("INFO", "Fabric stock", "whole fabric stock booked to contract 68", f"{fab_bags:,.1f} bags on sheet 68 (T27/U27)",
        "ERP stock is spread over 38, 60, 68 and 70", "per-contract P/A figures in SUMMARY (68 +128.7, 70 -280.0) mean nothing on their own - only the total does")

    # ---- 4. Claude's closing (55_) for the same yarn, and the bag-by-bag reconciliation
    g = gsheet(CLOSING_55)["Closing"]
    val = lambda r, c="D": float(g[f"{c}{r}"].value or 0)  # noqa: E731
    mine = dict(disp=-val(30), fresh=-val(31), loom=-val(32, "B"), unent=-val(32, "C"), wait=-val(33), siz=-val(34), hand=-val(36), bal=val(37),
                disp_m=val(18, "B"), fresh_m=val(22, "B"))
    acc_disp_m = sum(float(v[s_][f"U{r}"].value or 0) for s_, tr in (("38", 69), ("50", 62), ("60", 53), ("68", 15)) for r in [tr])
    m60 = sum(float(v["38"][f"U{r}"].value or 0) for r in range(5, 69) if v["38"][f"AH{r}"].value)
    ends_eff = (WP - WP90) * mine["disp_m"] / 100
    w60 = m60 * WF / 100
    rows_ = [
        ("Yarn received (net)", sum(recv.values()) / 100, sum(recv.values()) / 100, "same - matches the ERP IGPs per contract"),
        ("Consumed in fabric dispatched", sum(cons.values()) / 100, mine["disp"],
         f"meters match ({acc_disp_m:,.0f} vs {mine['disp_m']:,.0f}); gap = ends 90 vs 90.43 ({ends_eff:,.1f}). Both keep the {m60:,.0f} m woven with 60/1 weft "
         f"out of the 54/1 account (60/1 closed separately at count 59: accountant {-float(v['38']['N82'].value):,.2f} bags returnable, Claude's closing I31)"),
        ("Fresh fabric in stock", fab_bags, mine["fresh"], f"{acc_fresh:,.0f} m vs {mine['fresh_m']:,.0f} m (ERP {erp_tot:,.0f} less the owner's 45,000 m rejection)"),
        ("Warp in beams on looms + sized beams waiting", beams_acc, mine["loom"] + mine["wait"],
         "accountant: 18 (contract 68) + 185 (70), no on-loom row; ERP beam by beam: 68 on looms 27.8 · 70 on looms 115.5 + 7 waiting beams 95.3"),
        ("Weft in fabric woven but not yet entered (estimate)", 0.0, mine["unent"], "the only estimate; the accountant has none"),
        ("Yarn at sizing (Haji, 67 bags)", siz_acc, mine["siz"], "same 67 bags; my closing counts it as the fabric it will make"),
        ("Yarn in hand", hand_acc, mine["hand"], "count 68 on 28-Sep + IGP 595 (130 bags, 29-Sep) - issued since; ERP book 190.9"),
    ]
    acc_bal = -acc_total
    OUTWB = Workbook(); R = OUTWB.active; R.title = "Reconciliation"
    R["A1"] = "QADRI 52/54 COTTON - ACCOUNTANT'S CLOSING vs CLAUDE'S CLOSING vs ERP"; R["A1"].font = T
    R["A2"] = f"Accountant's workbook '{Path(path).name}' (to 29-Sep-2026) · Claude's grand closing refreshed {date.today():%d-%b-%Y} · ERP read only. Bags of 100 lb."; R["A2"].font = NOTE
    for i, h in enumerate(["Item", "Accountant (bags)", "Claude (bags)", "Difference (Claude - accountant)", "Why"], 1):
        c = R.cell(row=4, column=i, value=h); c.font, c.fill = W, NAVY
    r = 5
    for lab, a, m, why in rows_:
        R.cell(row=r, column=1, value=lab); R.cell(row=r, column=2, value=round(a, 2)); R.cell(row=r, column=3, value=round(m, 2))
        R.cell(row=r, column=4, value=f"=C{r}-B{r}" if r > 5 else 0); R.cell(row=r, column=5, value=why); r += 1
    R.cell(row=r, column=1, value="PAYABLE TO QADRI (bags) = received - everything above").font = B
    R.cell(row=r, column=2, value=f"=B5-SUM(B6:B{r - 1})"); R.cell(row=r, column=3, value=f"=C5-SUM(C6:C{r - 1})")
    R.cell(row=r, column=4, value=f"=C{r}-B{r}"); R.cell(row=r, column=5, value=f"accountant's SUMMARY says {acc_total:,.2f} (P/A) · Claude's closing balance {mine['bal']:,.2f}")
    for row in R.iter_rows(min_row=5, max_row=r):
        for c in row[1:4]:
            c.number_format = N1
    for col, w in zip("ABCDE", [48, 16, 14, 18, 110]):
        R.column_dimensions[col].width = w
    r += 2
    for t in ["WHO IS RIGHT ON EACH DIFFERENCE",
              f"1. Ends: the owner chose 90.43 ends/inch (actual sets, 12,480 ends); the accountant uses 90 -> {ends_eff:,.1f} bags less consumed (in Qadri's favour).",
              f"2. 60/1 weft: sheet 38 rows 62-68 (OGP 361-381, Apr-Jun 2025, {m60:,.0f} m) were woven with 60/1 weft from the 14-bag transfer of contract 40 - a separate account "
              f"(owner, 30-Sep-2026: count 59 weft / 58 warp). Claude's closing now does the same ({w60:,.1f} bags of 54/1 no longer charged). The ERP shows only 3.95 bags of 60/1 issued on contract 38.",
              f"3. Fabric stock: the accountant typed 148,413 m total (ERP {erp_tot:,.0f}) and took rejection as the balance, 38,550 m (owner: 45,000). Net {acc_fresh - mine['fresh_m']:,.0f} m more fresh stock than the owner's basis.",
              "4. Beams: the accountant's 18 + 185 bags are not supported by the ERP; beam by beam there are 238.9 bags of warp still to weave (the on-loom rows are blank on sheets 68/70).",
              "5. Unentered weft: an estimate in my closing only (days since last production entry x loom's meters a day).",
              "7. Yarn in hand: the accountant has 185; the count of 68 on 28-Sep plus the 130 bags received on 29-Sep less issues gives 194.4 (ERP book 190.9)."]:
        R.cell(row=r, column=1, value=t).font = B if t.startswith("WHO") else Font(); r += 1
    R.freeze_panes = "A5"

    o_ = OUTWB.create_sheet("Findings")
    o_["A1"] = "FINDINGS - accountant's 52 CTN workbook vs the ERP"; o_["A1"].font = T
    o_["A2"] = "ERROR = wrong in the sheet · CHECK = differs from the ERP / owner's basis, needs a look · INFO = explained difference · OK = verified."; o_["A2"].font = NOTE
    for i, (h, w) in enumerate(zip(["#", "Severity", "Area", "Item", "Sheet says", "ERP / recomputed says", "Note"], [5, 9, 24, 30, 42, 52, 60]), 1):
        c = o_.cell(row=4, column=i, value=h); c.font, c.fill = W, NAVY; o_.column_dimensions[c.column_letter].width = w
    order = {"ERROR": 0, "CHECK": 1, "INFO": 2, "OK": 3}
    for i, f_ in enumerate(sorted(F, key=lambda x: (order[x[0]], x[1])), 1):
        o_.append([i] + list(f_))
        o_.cell(row=o_.max_row, column=2).fill = PatternFill("solid", fgColor=SEV[f_[0]])
        for c in range(1, 8):
            o_.cell(row=o_.max_row, column=c).alignment = Alignment(wrap_text=True, vertical="top")
    o_.freeze_panes = "A5"; o_.auto_filter.ref = f"A4:G{o_.max_row}"
    OUT.parent.mkdir(exist_ok=True); OUTWB.save(OUT)
    from collections import Counter
    print(Counter(x[0] for x in F), "acc", round(acc_bal, 2), "mine", round(mine["bal"], 2), "ends", round(ends_eff, 2), "60/1", round(w60, 2), "m60", m60)
    for f_ in sorted(F, key=lambda x: (order[x[0]], x[1])):
        if f_[0] in ("ERROR", "CHECK"):
            print(" | ".join(str(x) for x in f_))
    if upload_id:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        c = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
        build("drive", "v3", credentials=c).files().update(fileId=upload_id, media_body=MediaFileUpload(
            str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
        print("uploaded", upload_id)


def run66(path, cno):
    """66_'s checks for one contract sheet, returned as findings (66_ writes its own file too)"""
    got = []
    orig = a66.Workbook

    class Grab(orig):
        def save(self, fn):
            ws = self.active
            for row in ws.iter_rows(min_row=5, values_only=True):
                if row[1]:
                    got.append(tuple(row[1:7]))
    a66.Workbook = Grab
    try:
        a66.main(path, cno, None, cno)
    finally:
        a66.Workbook = orig
    return got


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[a.index("--upload") + 1] if "--upload" in a else None)
