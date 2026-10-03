"""
72_approval_folder_check.py - is every ERP document of a month in the signed-approval folders? (owner, 30-Sep-2026: keep a digital,
signed backup of every ERP entry instead of paper files; first run 3-Nov-2026 for October).

The accountant puts each ERP print (PDF) in the owner's approval folders; the owner signs them digitally and the Google script files them in
"Approved/<document type>" as "<name> - SIGNED - <CODE>-<no>-<yyyymmdd>.pdf" (or "Correction Required" when sent back).
This reads the text of every PDF (document numbers and dates are printed in them) and checks, for each ERP document of the period:
signed / sent back for correction / filed under another type / MISSING. Production and beam issues are daily reports, so for those
every working date must have a signed report. ERP read only; result -> Google Sheet + ntfy push.

python 72_approval_folder_check.py [--month 2026-10 | --from 2026-09-15 --to 2026-09-30] [--no-push]
"""
import importlib.util
import io
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_a = importlib.util.spec_from_file_location("a70", HERE / "70_erp_audit.py"); a70 = importlib.util.module_from_spec(_a); _a.loader.exec_module(a70)
APPROVED = "1uKYQO8JmTPiBe0E5bXROC573TgrAZgQW"
CORRECTION = "1HbfqObucvaXpHwrSaFkvuPbTxZldtPT8"
SHEET_ID = None           # set below by --sheet or the constant after creation
OUT = HERE / "financial_statements" / "approval_folder_check.xlsx"
MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
NAVY = PatternFill("solid", fgColor="1F3B57"); W = Font(bold=True, color="FFFFFF"); B = Font(bold=True); T = Font(bold=True, size=14, color="1F3B57")
FILL = {"SIGNED": "D9F2E3", "SIGNED (number differs)": "E8EDF2", "SIGNED - ERP DIFFERS": "F8C9C9", "SIGNED - VALUE RE-RATED": "FFF4C2", "NOT APPROVED (in correction)": "FFE3B3", "MISSING": "F8C9C9"}


def drive():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"),
                                                                                                 scopes=["https://www.googleapis.com/auth/drive"]))


def pdf_text(raw):
    from pypdf import PdfReader
    try:
        return re.sub(r"\s+", " ", " ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(raw)).pages))
    except Exception:
        return ""


def dates_in(s):
    out = set()
    for d_, m_, y_ in re.findall(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", s):
        try:
            out.add(date(int(y_), int(m_), int(d_)))
        except ValueError:
            pass
    for d_, m_, y_ in re.findall(r"\b(\d{1,2})-([A-Z]{3})-(\d{2,4})\b", s.upper()):
        if m_ in MON:
            try:
                out.add(date(int(y_) + (2000 if len(y_) == 2 else 0), MON[m_], int(d_)))
            except ValueError:
                pass
    for y_, m_, d_ in re.findall(r"(20\d{2})(\d{2})(\d{2})", s):
        try:
            out.add(date(int(y_), int(m_), int(d_)))
        except ValueError:
            pass
    return out


def ranges_in(s):
    """'FROM 28-SEP-26 TO 30-SEP-26' / 'FROM 28-SEP-26 AND 28-SEP-26' -> covered dates"""
    cov = set()
    for a_, b_ in re.findall(r"FROM\s*:?\s*(\d{1,2}-[A-Z]{3}-\d{2,4})\s*(?:TO|AND)\s*:?\s*(\d{1,2}-[A-Z]{3}-\d{2,4})", s.upper()):
        da, db = sorted(dates_in(a_))[0], sorted(dates_in(b_))[0]
        while da <= db and len(cov) < 62:
            cov.add(da); da += timedelta(days=1)
    return cov


def read_folders():
    """every PDF in Approved/<type> and Correction Required, with its text, numbers and dates"""
    s = drive(); docs = []
    for root, status in ((APPROVED, "SIGNED"), (CORRECTION, "IN CORRECTION")):
        stack = [(root, "")]
        while stack:
            fid, folder = stack.pop(); tok = None
            while True:
                r = s.files().list(q=f"'{fid}' in parents and trashed = false", fields="nextPageToken, files(id,name,mimeType,webViewLink)", pageSize=1000, pageToken=tok).execute()
                for x in r.get("files", []):
                    if x["mimeType"].endswith("folder"):
                        stack.append((x["id"], x["name"])); continue
                    if not x["name"].lower().endswith(".pdf"):
                        continue
                    t = pdf_text(s.files().get_media(fileId=x["id"]).execute())
                    nm = x["name"]
                    # the document's OWN number: the one in the file code (…-SRN-715-…), or printed next to "GP No", "GRN No",
                    # "Voucher No", "Bil No", "Ret No"; store-issue reports list every slip after "Iss No Iss Date"
                    key = {int(m.group(1)) for m in re.finditer(r"SIGNED - [A-Z]+-0*(\d{1,6})-\d{8}", nm)}
                    key |= {int(n) for n in re.findall(r"(\d{1,6})\s*(?:GP|GRN|Voucher|Bil|Bill|Ret|Iss)\s*No\b", t, re.I)}
                    key |= {int(n) for n in re.findall(r"(?:GP|GRN|Voucher|Bil|Bill|Ret|Iss)\s*No\s*:\s*(\d{1,6})\b", t, re.I)}
                    if "Iss No" in t:
                        key |= {int(n) for n in re.findall(r"\b(\d{3,5})\b", t.split("Iss No", 1)[1])}
                    docs.append(dict(status=status, folder=folder or "Correction Required", name=nm, link=x.get("webViewLink"), text=t, nums=key,
                                     dates=dates_in(nm) | dates_in(t), cover=ranges_in(t)))
                tok = r.get("nextPageToken")
                if not tok:
                    break
    return docs


def erp_docs(cur, d0, d1):
    """every ERP document of the period: (type, expected Approved sub-folder, number or None = daily report, date, detail, values to find
    in the signed print). The values are what the print must still show - if the ERP was edited after signing they no longer match."""
    W = f"BETWEEN DATE '{d0:%Y-%m-%d}' AND DATE '{d1:%Y-%m-%d}'"
    q = lambda s_: cur.execute(s_).fetchall()  # noqa: E731
    E = []
    VT = {"CP": "Cash Payment Voucher", "CR": "Cash Receipt Voucher", "BP": "Bank Payment Voucher", "BR": "Bank Receipt Voucher", "JV": "Journal Voucher"}
    for vt, vno, vd, nar, amt in q(f"""SELECT vt, vno, TRUNC(vd), MAX(NVL(naration, ' ')), SUM(CASE WHEN amt > 0 THEN amt ELSE 0 END) FROM dostex.acl
                                      WHERE vt IN ('CP','CR','BP','BR','JV') AND TRUNC(vd) {W} GROUP BY vt, vno, TRUNC(vd)"""):
        acs = [a_ for (a_,) in q(f"SELECT DISTINCT ac FROM dostex.acl WHERE vt = '{vt}' AND vno = {int(vno)} AND TRUNC(vd) = DATE '{vd:%Y-%m-%d}'")]
        E.append((f"{vt} voucher", VT[vt], int(vno), vd.date(), f"Rs {float(amt or 0):,.0f} {str(nar).strip()[:60]}", [("total Rs", float(amt or 0))] + [("account", str(a_)) for a_ in acs]))
    for dt, dno, dd, an, amt, qty in q(f"""SELECT m.dt, m.dno, TRUNC(m.dd), a.an,
                (SELECT SUM(r.total_amt) FROM dostex.store_receipt_v r WHERE r.trans_no = m.trans_no),
                (SELECT SUM(i.qty) FROM dostex.store_issue_v i WHERE i.trans_no = m.trans_no)
                FROM dostex.dmn m LEFT JOIN dostex.ach a ON a.ac = m.ac WHERE TRUNC(m.dd) {W}"""):
        folder = {"SRN": "IGP Store (Inspection - GRN)", "ISS": "Store Issue"}.get(dt, "Store Sale")
        if dt == "SRN":
            vals = ([("total Rs", float(amt))] if amt else []) + [("item", str(i_)) for (i_,) in q(f"SELECT DISTINCT r.ic FROM dostex.store_receipt_v r WHERE r.dno = {int(dno)} AND TRUNC(r.dd) = DATE '{dd:%Y-%m-%d}'")]
        elif dt == "ISS":       # the daily issue report: the day's net total and every item of this slip
            day, dq = q(f"SELECT SUM(amount), SUM(qty) FROM dostex.store_issue_v WHERE TRUNC(dd) = DATE '{dd:%Y-%m-%d}'")[0]
            # the report ends "Net Total : <qty to 1 decimal> <rupees>": the quantity is checked as that exact phrase (small numbers
            # appear everywhere on the page), the rupees separately - so a re-valued day can be told from an edited one
            vals = ([("day net total Rs", float(round(day)))] if day else []) + ([("day net qty", f"Net Total : {float(dq):.1f}")] if dq else []) \
                + [("item", str(i_).strip()[:18]) for (i_,) in q(f"SELECT DISTINCT idesc FROM dostex.store_issue_v WHERE dno = {int(dno)} AND TRUNC(dd) = DATE '{dd:%Y-%m-%d}'") if i_]
        else:
            vals = []
        E.append((f"{dt} (store)", folder, int(dno), dd.date(), f"{an or ''} Rs {float(amt or 0):,.0f}" if dt == "SRN" else "", vals))
    TT = {"IGP_YARN": "Yarn Received (Yarn From Party)", "TO_SIZING": "Yarn To Sizing", "FROM_SIZING": "Yarn From Sizing", "YARN_TO_LOOMS": "Yarn Issue (to Looms)",
          "YARN_FROM_LOOMS": "Yarn Issue (to Looms)", "OGP_YARN": "OGP Yarn", "IGP_SIZED_BEAMS": "IGP Sized Beams (IGP Sizing)"}
    for tt, tno, td, an, kg, bags in q(f"""SELECT m.tt, m.tno, TRUNC(m.td), a.an, (SELECT SUM(d.yarn_wt) FROM dostex.wytd d WHERE d.trans_no = m.trans_no),
                (SELECT SUM(d.bags) FROM dostex.wytd d WHERE d.trans_no = m.trans_no)
                FROM dostex.wytm m LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2) WHERE TRUNC(m.td) {W}"""):
        # total kg is distinctive on the print (453.6 / 5896.8); small bag counts also appear as row numbers, so only large ones are used
        vals = ([("kg", round(float(kg), 3))] if kg else []) + ([("bags", float(bags))] if bags and bags >= 20 else [])
        if tt == "IGP_SIZED_BEAMS":
            vals += [("beam", str(b_)) for (b_,) in q(f"SELECT b.bcode FROM dostex.wybeams b JOIN dostex.wytm m ON m.trans_no = b.trans_no WHERE m.tt = 'IGP_SIZED_BEAMS' AND m.tno = {int(tno)}")]
        E.append((tt.replace("_", " ").lower(), TT.get(tt, "Other (fallback)"), int(tno), td.date(), f"{an or ''} {float(bags or 0):g} bags {float(kg or 0):,.1f} kg", vals))
    for tt, tno, td, an, trans in q(f"SELECT m.tt, m.tno, TRUNC(m.td), a.an, m.trans_no FROM dostex.wv_gp_beamsm m LEFT JOIN dostex.ach a ON a.ac = m.ac1 WHERE TRUNC(m.td) {W}"):
        beams = [b for (b,) in q(f"SELECT beam_code FROM dostex.wv_gp_beamsd WHERE trans_no = {int(trans)}")]
        E.append((tt.replace("_", " ").lower(), "OGP Empty Beams" if tt == "OGP_EMPTY_BEAMS" else "Returnable Inward", int(tno), td.date(),
                  f"{an or ''} {len(beams)} beams", [("beam", b) for b in beams]))
    for tt, no, td, party, dto, m, th, amt in q(f"""SELECT trans_type, NVL(inv_no, gp_no), TRUNC(MAX(trans_date)), MAX(party), MAX(deliver_to), SUM(total_mtr), SUM(than), SUM(amt) FROM dostex.fab_gp_v
                                         WHERE TRUNC(trans_date) {W} GROUP BY trans_type, NVL(inv_no, gp_no)"""):
        if tt == "GPOUT":
            E.append(("fabric OGP", "OGP (Outward Fabric)", int(no), td.date(), f"{dto} {float(m or 0):,.0f} m", [("meters", float(m or 0))] + ([("than", float(th))] if th and th >= 10 else [])))
            if "REJECTION" not in str(dto).upper():
                E.append(("conversion bill", "Conversion (Sales Bill)", int(no), td.date(), f"{party} {float(m or 0):,.0f} m Rs {float(amt or 0):,.0f}",
                          [("meters", float(m or 0))] + ([("value Rs", float(round(amt)))] if amt else [])))
        else:
            E.append(("fabric IGP / return", "IGP (Inward Fabric - incl. Sale Return)", int(no), td.date(), f"{dto} {float(m or 0):,.0f} m", [("meters", float(m or 0))]))
    for (td,) in q(f"SELECT DISTINCT TRUNC(td) FROM dostex.wprdtm WHERE tt = 'PRODUCTION' AND TRUNC(td) {W}"):
        a_, b_, c_, cp_, t_ = q(f"""SELECT SUM(NVL(a,0)+NVL(a2,0)), SUM(NVL(b,0)), SUM(NVL(c,0)), SUM(NVL(cp,0)), SUM(total_mtr) FROM dostex.wv_prod_v
                                 WHERE tt = 'PRODUCTION' AND TRUNC(td) = DATE '{td:%Y-%m-%d}'""")[0]
        E.append(("daily production", "Daily Production Report", None, td.date(), f"production {float(t_ or 0):,.0f} m",
                  [(lab, float(round(v_))) for lab, v_ in (("A grade m", a_), ("B grade m", b_), ("C grade m", c_), ("CP grade m", cp_)) if v_]))
    for (td,) in q(f"SELECT DISTINCT TRUNC(td) FROM dostex.wprdtm WHERE tt = 'BEAMS_TO_LOOM' AND TRUNC(td) {W}"):
        bl = [b_ for (b_,) in q(f"""SELECT b.bcode FROM dostex.wprdtm m JOIN dostex.wprdtd d ON d.trans_no = m.trans_no JOIN dostex.wybeams b ON b.trans_no = d.ref_tno
                                   AND b.sr_no = d.ref_sr WHERE m.tt = 'BEAMS_TO_LOOM' AND TRUNC(m.td) = DATE '{td:%Y-%m-%d}'""")]
        E.append(("beams to looms", "Beams Issue (to Looms)", None, td.date(), f"{len(bl)} beams mounted", [("beam", str(b_)) for b_ in bl]))
    for fd, td_, gross, earned in q(f"SELECT fdate, tdate, SUM(gross_salary), SUM(earned_amount) FROM payroll.salm WHERE fdate {W} GROUP BY fdate, tdate"):
        E.append(("salary sheet", "Salary Sheet", None, fd.date(), f"salary {fd:%d-%b} to {td_:%d-%b} earned Rs {float(earned or 0):,.0f}",
                  [(lab, float(round(v_))) for lab, v_ in (("gross salary Rs", gross), ("earned Rs", earned)) if v_]))
    try:
        for no, gd, gt in q(f"SELECT ge_no, TRUNC(ge_date), ge_type FROM dostex.ge_ret_main WHERE TRUNC(ge_date) {W}"):
            E.append((f"returnable {gt}", "Returnable Outward (OGP Returnable)" if str(gt).upper().startswith("O") else "Returnable Inward", int(no), gd.date(), "", []))
    except Exception:
        pass
    return E


def shows(text, v):
    """does the print show this value? numbers in any of the ERP print formats (970,520 / 970520 / 5896.8 / 686.07)"""
    if isinstance(v, str):
        return v.strip() in text
    if float(v).is_integer():                         # whole numbers: 970,520 / 970520 / 970,520.00
        forms = {f"{int(v):,}", str(int(v)), f"{v:,.2f}", f"{v:.2f}", f"{v:.1f}"}
    else:                                             # decimals must match to their decimals (378.38 is not '378')
        forms = {f"{v:,.3f}", f"{v:.3f}"} | ({f"{v:,.2f}", f"{v:.2f}"} if round(v, 2) == round(v, 3) else set())
        if round(v, 1) == round(v, 2):
            forms |= {f"{v:,.1f}", f"{v:.1f}"}
    return any(re.search(r"(?<![\d.,])" + re.escape(f) + r"(?![\d])", text) for f in forms)


def match(E, P):
    """a document counts only when its signed PDF is in its own Approved sub-folder (owner, 30-Sep-2026); a copy sent back to
    'Correction Required' means not approved yet. Then the print is checked against the ERP entry as it is today."""
    used = set(); res = []
    for kind, folder, no, d, detail, vals in sorted(E, key=lambda e: (e[1], e[3], e[2] or 0)):
        hit = None
        for i, p in enumerate(P):
            if p["folder"] != folder and p["status"] == "SIGNED":        # different types share number ranges: only the own folder counts
                continue
            ok = (p["folder"] == folder and (d in p["cover"] or d in p["dates"])) if no is None else (no in p["nums"] and (d in p["dates"] or d in p["cover"]))
            if ok and (no is None or i not in used or folder == "Store Issue"):
                if hit is None or (hit[1]["status"] != "SIGNED" and p["status"] == "SIGNED"):   # an approved copy beats one in correction
                    hit = (i, p)
        if hit:
            i, p = hit
            if no is not None and folder != "Store Issue":
                used.add(i)
            res.append([kind, folder, no, d, detail, p["status"] if p["status"] == "SIGNED" else "NOT APPROVED (in correction)", p["name"], p["folder"], p.get("link"), vals, p])
        else:
            res.append([kind, folder, no, d, detail, "MISSING", "", "", "", vals, None])
    # a document still missing + an unused Approved PDF of its folder with the same date: the print carries another number
    taken = {r[6] for r in res if r[6]}
    for r in res:
        if r[5] == "MISSING" and r[2] is not None:
            cand = [p for p in P if p["status"] == "SIGNED" and p["folder"] == r[1] and p["name"] not in taken and r[3] in p["dates"]]
            if len(cand) == 1:
                taken.add(cand[0]["name"])
                r[5:9] = ["SIGNED (number differs)", cand[0]["name"], cand[0]["folder"], cand[0].get("link")]; r[10] = cand[0]
    # verify the signed print against the ERP entry today
    for r in res:
        if r[5].startswith("SIGNED") and r[9] and r[10]:
            text = r[10]["text"]
            if r[2] is None:        # daily reports (production, beams, salary): all signed prints of that date in the folder together
                text = " ".join(p["text"] for p in P if p["status"] == "SIGNED" and p["folder"] == r[1] and (r[3] in p["cover"] or r[3] in p["dates"]))
            elif r[1] == "Store Issue":   # the day's issue report may be split over several signed prints
                text = " ".join(p["text"] for p in P if p["status"] == "SIGNED" and p["folder"] == r[1] and r[3] in p["dates"])
            miss = [f"{lab} {v:,.2f}".rstrip("0").rstrip(".") if not isinstance(v, str) else f"{lab} {v}" for lab, v in r[9] if not shows(text, v)]
            if miss and r[1] == "Store Issue" and all(m_.startswith("day net total") for m_ in miss):
                # same items and quantities, only the rupee value moved: issues are valued at average rate, which the ERP recalculates
                r[5] = "SIGNED - VALUE RE-RATED"
                r[4] = f"{r[4]} | quantities match the signed print; ERP value now {miss[0][17:]} (average rate recalculated after a later GRN, not an edit)"
            elif miss:
                r[5] = "SIGNED - ERP DIFFERS"
                r[4] = f"{r[4]} | ERP now has {', '.join(miss[:4])} - not on the signed print (edited after signing?)"
    return [tuple(r[:9]) for r in res]


def main():
    args = sys.argv[1:]
    if "--from" in args:
        d0 = datetime.strptime(args[args.index("--from") + 1], "%Y-%m-%d").date(); d1 = datetime.strptime(args[args.index("--to") + 1], "%Y-%m-%d").date()
    else:
        if "--month" in args:
            y, mo = map(int, args[args.index("--month") + 1].split("-"))
        else:
            t = date.today().replace(day=1) - timedelta(days=1); y, mo = t.year, t.month
        d0 = date(y, mo, 1); d1 = date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1)
    cur = a70.dash.connect(a70.dash.load_config()).cursor()
    E = erp_docs(cur, d0, d1)
    P = read_folders()
    R = match(E, P)
    cnt = defaultdict(lambda: defaultdict(int))
    for r in R:
        cnt[r[1]][r[5]] += 1
    wb = Workbook(); s = wb.active; s.title = "Summary"
    s["A1"] = f"ERP DOCUMENTS vs SIGNED APPROVAL FOLDERS - {d0:%d-%b-%Y} to {d1:%d-%b-%Y}"; s["A1"].font = T
    s["A2"] = f"Checked {date.today():%d-%b-%Y}. {len(P)} PDFs read in Approved + Correction Required. ERP read only."; s["A2"].font = Font(italic=True)
    hd = ["Document type (Approved folder)", "In ERP", "Signed, matches ERP", "Signed (number differs)", "Signed - value re-rated", "Signed - ERP DIFFERS", "Not approved (in correction)", "MISSING"]
    for i, h in enumerate(hd, 1):
        c = s.cell(row=4, column=i, value=h); c.font, c.fill = W, NAVY
    r_ = 5
    for fo in sorted(cnt):
        v = cnt[fo]
        s.append([fo, sum(v.values()), v["SIGNED"], v["SIGNED (number differs)"], v["SIGNED - VALUE RE-RATED"], v["SIGNED - ERP DIFFERS"], v["NOT APPROVED (in correction)"], v["MISSING"]])
        if v["MISSING"]:
            s.cell(row=r_, column=8).fill = PatternFill("solid", fgColor=FILL["MISSING"])
        r_ += 1
    tot = [sum(sum(v.values()) for v in cnt.values())] + [sum(v[k] for v in cnt.values()) for k in ("SIGNED", "SIGNED (number differs)", "SIGNED - VALUE RE-RATED", "SIGNED - ERP DIFFERS", "NOT APPROVED (in correction)", "MISSING")]
    s.append(["TOTAL"] + tot); s.cell(row=s.max_row, column=1).font = B
    s.column_dimensions["A"].width = 42
    for col in "BCDEF":
        s.column_dimensions[col].width = 16
    for title, rows in (("To check", [r for r in R if r[5] in ("MISSING", "SIGNED - ERP DIFFERS", "NOT APPROVED (in correction)")]), ("All documents", R)):
        w = wb.create_sheet(title)
        for i, h in enumerate(["Type", "Expected folder", "ERP no.", "Date", "Party / detail", "Status", "PDF found", "Filed in", "Link"], 1):
            c = w.cell(row=1, column=i, value=h); c.font, c.fill = W, NAVY
        for r in rows:
            w.append([r[0], r[1], r[2] if r[2] is not None else "(daily)", r[3], r[4], r[5], r[6], r[7], r[8]])
            w.cell(row=w.max_row, column=4).number_format = "dd-mmm-yy"
            w.cell(row=w.max_row, column=6).fill = PatternFill("solid", fgColor=FILL.get(r[5], "FFFFFF"))
        for col, wd in zip("ABCDEFGHI", [20, 36, 9, 11, 50, 20, 50, 30, 30]):
            w.column_dimensions[col].width = wd
        w.freeze_panes = "A2"; w.auto_filter.ref = f"A1:I{max(2, w.max_row)}"
    OUT.parent.mkdir(exist_ok=True); wb.save(OUT)
    print(f"{d0} to {d1}: ERP {tot[0]}, signed & matching {tot[1] + tot[2]}, value re-rated {tot[3]}, signed but ERP differs {tot[4]}, not approved (correction) {tot[5]}, MISSING {tot[6]} -> {OUT}")
    for fo in sorted(cnt):
        print("  ", fo, dict(cnt[fo]))
    sid = args[args.index("--sheet") + 1] if "--sheet" in args else SHEET
    if sid:
        from googleapiclient.http import MediaFileUpload
        drive().files().update(fileId=sid, media_body=MediaFileUpload(str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
    if "--no-push" not in args:
        miss = [r for r in R if r[5] in ("MISSING", "SIGNED - ERP DIFFERS", "NOT APPROVED (in correction)")]
        lines = [f"{d0:%d-%b} to {d1:%d-%b}: {tot[0]} ERP documents - {tot[1] + tot[2] + tot[3]} signed and matching the ERP ({tot[3]} only re-valued at average rate), {tot[4]} signed but the ERP now differs, {tot[5]} not approved (in correction), {tot[6]} MISSING from Approved."]
        by = defaultdict(int)
        for r in miss:
            by[r[1]] += 1
        lines += [f"- {k}: {v} to check" for k, v in sorted(by.items(), key=lambda x: -x[1])[:10]]
        a70.push(f"Signed ERP documents - {d0:%b %Y}", "\n".join(lines), f"https://docs.google.com/spreadsheets/d/{sid}/edit" if sid else "https://drive.google.com",
                 "high" if miss else "default")
        print("pushed")


SHEET = "1J7JH0FDgAwujV8HkA7VZTFRve-z9lNvMrq2O0RjJmoo"     # Google Sheet 'ERP entries vs signed Approved folder - monthly check'


if __name__ == "__main__":
    main()
