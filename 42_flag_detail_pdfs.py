"""
42_flag_detail_pdfs.py
Project : Dost Textiles - detail PDFs for flags raised with the P&L / balance
          sheet (2026-09-25). Read-only on the ERP. One-off, not scheduled.
  1. flag01_unbalanced_srn_vouchers.pdf  - SRN 656, 184, 117, 207 (+ the rest)
  6. flag06_partners_drawings.pdf        - every drawing entry, by FY/head
  7. flag07_sales_tax_payable.pdf        - sales tax P/A ledger + FY summary
 10. flag10_unbilled_dispatches.pdf      - GPOUT gate passes with no rate

SRN numbers (DMN.DNO) restart over time, so a voucher is identified by
number AND date.
"""

import importlib.util
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("dash", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dash)
OUT = HERE / "flag_pdfs"

NAVY = colors.HexColor("#1F3B57")
LIGHT = colors.HexColor("#E8EDF2")
RED = colors.HexColor("#B91C1C")
ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=16, textColor=NAVY, alignment=0, spaceAfter=4)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=NAVY, spaceBefore=8, spaceAfter=4)
BODY = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
SMALL = ParagraphStyle("s", parent=BODY, fontSize=7.5, leading=9.5)
CELL = ParagraphStyle("c", parent=BODY, fontSize=7.5, leading=9)


def n(v, blank_zero=True):
    if v is None or (blank_zero and abs(v) < 0.5):
        return ""
    s = f"{abs(v):,.0f}"
    return f"({s})" if v < 0 else s


def d(v):
    return v.strftime("%d-%b-%Y") if v else ""


def table(rows, widths, num_cols=(), total_rows=(), header=True, red_rows=()):
    data = [[Paragraph(str(c), CELL) if isinstance(c, str) and len(c) > 18 else c for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    st = [("FONT", (0, 0), (-1, -1), "Helvetica", 7.5),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D1D9")),
          ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
               ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5)]
    for c in num_cols:
        st.append(("ALIGN", (c, 1 if header else 0), (c, -1), "RIGHT"))
    for r in total_rows:
        st += [("FONT", (0, r), (-1, r), "Helvetica-Bold", 7.5), ("BACKGROUND", (0, r), (-1, r), LIGHT)]
    for r in red_rows:
        st += [("TEXTCOLOR", (0, r), (-1, r), RED), ("FONT", (0, r), (-1, r), "Helvetica-Bold", 7.5)]
    t.setStyle(TableStyle(st))
    return t


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.grey)
    canvas.drawString(12 * mm, 8 * mm, f"Dost Textiles - source: DOSTEX ERP (read-only), generated {datetime.now():%d-%b-%Y %H:%M}")
    canvas.drawRightString(doc.pagesize[0] - 12 * mm, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build(name, story, orient=landscape(A4)):
    doc = SimpleDocTemplate(str(OUT / name), pagesize=orient, leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title=name)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def fy(dt):
    return f"FY{(dt.year + 1 if dt.month >= 7 else dt.year) % 100:02d}"


# ---------------------------------------------------------------- flag 1
def flag1(cur):
    unbalanced = dash.rows(cur, """
        SELECT vno, vd, SUM(amt) FROM dostex.party_ledger_v
        WHERE tbl='DMN' AND vt='SRN' GROUP BY vno, vd HAVING ABS(SUM(amt)) > 1
        ORDER BY vd""")
    requested = [(656, "2026-08-11"), (184, "2025-06-04"), (117, "2025-04-09"), (207, "2025-07-03")]
    story = [Paragraph("Flag 1 - Store receipt (SRN) vouchers that do not balance", H1),
             Paragraph("Requested: SRN 656, 184, 117 and 207. SRN numbers restart over time, so each voucher is "
                       "identified by number <b>and</b> date. Lines are exactly as the ERP posts them to the ledger "
                       "(view STORE_T / PARTY_LEDGER_V).", BODY), Spacer(1, 6),
             Paragraph("What is wrong", H2),
             Paragraph("On each of these vouchers the supplier is credited with the <b>full bill including sales "
                       "tax</b>, while stores are debited with the amount <b>before</b> sales tax. The third line "
                       "that should debit <b>SALES TAX RECEIVEABLE</b> (input tax) is missing, because the voucher has "
                       "no sales-tax account filled in. The difference on every voucher equals its sales tax "
                       "exactly. A correctly posted voucher of the same kind (SRN 117 of 28-Mar-2024, same item) "
                       "is shown at the end for comparison.", BODY),
             Paragraph("Fix in the ERP: open each voucher, set the sales-tax (input tax) account, and save again. "
                       "Total input tax not claimed on these vouchers: <b>Rs " +
                       f"{-sum(float(x[2]) for x in unbalanced):,.0f}</b>.", BODY)]

    rows = [["SRN", "Date", "Difference (Rs)", "Requested"]]
    for vno, vd, diff in unbalanced:
        rows.append([str(vno), d(vd), n(float(diff)), "Yes" if (vno, vd.strftime("%Y-%m-%d")) in requested else ""])
    rows.append(["", "Total", n(sum(float(x[2]) for x in unbalanced)), ""])
    story += [Paragraph("All unbalanced SRN vouchers since go-live", H2),
              table(rows, [25 * mm, 35 * mm, 35 * mm, 25 * mm], num_cols=(2,), total_rows=(len(rows) - 1,))]

    def voucher(vno, vdate, title):
        hdr = dash.rows(cur, """
            SELECT m.dno, m.dd, m.ac, a.an, m.bno, m.bd, m.ac1, m.ac2, m.usern, m.feedd, m.tdesc, m.trans_no
            FROM dostex.dmn m LEFT JOIN dostex.ach a ON a.ac = m.ac
            WHERE m.dt='SRN' AND m.dno=:v AND TRUNC(m.dd)=TO_DATE(:d,'YYYY-MM-DD')""", v=vno, d=vdate)
        lines = dash.rows(cur, """
            SELECT l.ac, a.an, l.item, l.qty, l.rate, l.gst_amt, l.amt
            FROM dostex.party_ledger_v l LEFT JOIN dostex.ach a ON a.ac = l.ac
            WHERE l.tbl='DMN' AND l.vt='SRN' AND l.vno=:v AND TRUNC(l.vd)=TO_DATE(:d,'YYYY-MM-DD')
            ORDER BY CASE WHEN l.amt > 0 THEN 0 ELSE 1 END, l.ac""", v=vno, d=vdate)
        out = [Paragraph(title, H2)]
        if hdr:
            h = hdr[0]
            info = [["Supplier", f"{h[3] or ''} ({h[2] or ''})", "Bill no / date", f"{h[4] or ''}  {d(h[5])}"],
                    ["Entered by", f"{h[8] or ''}  {d(h[9])}", "Stores account (AC1)", h[6] or ""],
                    ["Sales-tax account (AC2)", h[7] or "MISSING", "Description", h[10] or ""]]
            t = table(info, [38 * mm, 90 * mm, 38 * mm, 100 * mm], header=False)
            t.setStyle(TableStyle([("FONT", (0, 0), (0, -1), "Helvetica-Bold", 7.5),
                                   ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 7.5),
                                   ("TEXTCOLOR", (1, 2), (1, 2), RED if not h[7] else colors.black)]))
            out.append(t)
            out.append(Spacer(1, 4))
        rows = [["Account", "Account name", "Item", "Qty", "Rate", "Sales tax", "Debit", "Credit"]]
        dr = cr = 0.0
        for ac, an, item, qty, rate, gst, amt in lines:
            amt = float(amt or 0)
            dr += max(amt, 0); cr += max(-amt, 0)
            rows.append([ac, an or "", item or "", f"{float(qty or 0):,.0f}", f"{float(rate or 0):,.2f}",
                         n(float(gst or 0)), n(max(amt, 0)), n(max(-amt, 0))])
        rows.append(["", "Total", "", "", "", "", n(dr), n(cr)])
        red = ()
        if abs(dr - cr) > 0.5:
            rows.append(["", "Difference (credit - debit)", "", "", "", "", "", n(cr - dr)])
            red = (len(rows) - 1,)
        out.append(table(rows, [24 * mm, 58 * mm, 72 * mm, 14 * mm, 20 * mm, 22 * mm, 26 * mm, 26 * mm],
                         num_cols=(3, 4, 5, 6, 7), total_rows=(len(rows) - (2 if red else 1),), red_rows=red))
        return out

    for vno, vd in requested:
        story.append(KeepTogether(voucher(vno, vd, f"SRN {vno} dated {datetime.strptime(vd, '%Y-%m-%d'):%d-%b-%Y}")))
    story.append(KeepTogether(voucher(117, "2024-03-28", "For comparison - SRN 117 dated 28-Mar-2024 (posted correctly)")))
    build("flag01_unbalanced_srn_vouchers.pdf", story)


# ---------------------------------------------------------------- flag 6
def flag6(cur):
    rows = dash.rows(cur, """
        SELECT l.vd, l.vt, l.vno, l.ac, a.an, l.naration, l.amt
        FROM dostex.acl l LEFT JOIN dostex.ach a ON a.ac = l.ac
        WHERE l.ac LIKE '100303%' ORDER BY CASE WHEN l.vt='OPN' THEN 0 ELSE 1 END, l.vd, l.vno""")
    opn = sum(float(r[6]) for r in rows if r[1] == "OPN")
    live = [r for r in rows if r[1] != "OPN"]
    heads = sorted({r[4] or r[3] for r in rows})
    fys = sorted({fy(r[0]) for r in live})
    by = defaultdict(float)
    for r in live:
        by[(r[4] or r[3], fy(r[0]))] += float(r[6])
    opn_by = defaultdict(float)
    for r in rows:
        if r[1] == "OPN":
            opn_by[r[4] or r[3]] += float(r[6])

    story = [Paragraph("Flag 6 - Partners' drawings", H1),
             Paragraph("All postings to PARTNERS' DRAWING (100303) and its sub-accounts. Debit = money taken out "
                       "of the business; credit (in brackets) = reversal or money put back. Opening balance is the "
                       "amount carried from the old ERP at go-live.", BODY), Spacer(1, 4),
             Paragraph("Summary by account and fiscal year (Jul-Jun)", H2)]
    hdr = ["Account", "Opening"] + [f + (" (to date)" if f == fys[-1] else "") for f in fys] + ["Total"]
    srows = [hdr]
    col_t = [opn] + [0.0] * len(fys)
    for h in heads:
        vals = [opn_by[h]] + [by[(h, f)] for f in fys]
        if not any(abs(v) >= 1 for v in vals):
            continue
        srows.append([h] + [n(v) for v in vals] + [n(sum(vals))])
        for i, v in enumerate(vals[1:]):
            col_t[i + 1] += v
    srows.append(["Total"] + [n(v) for v in col_t] + [n(sum(col_t))])
    story.append(table(srows, [70 * mm] + [30 * mm] * (len(hdr) - 1), num_cols=range(1, len(hdr)),
                       total_rows=(len(srows) - 1,)))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Drawings by voucher type (since go-live): cash payments (CP), bank payments (BP) and "
                           "journal transfers (JV) - most JVs move amounts booked in the MIAN AZAM(STORE) account "
                           "into drawings.", SMALL))
    vt_t = defaultdict(float)
    for r in live:
        vt_t[r[1]] += float(r[6])
    story.append(table([["Voucher type", "Amount"]] + [[k, n(v)] for k, v in sorted(vt_t.items())],
                       [40 * mm, 35 * mm], num_cols=(1,)))
    story.append(PageBreak())
    story.append(Paragraph("Every entry since go-live (Oct-2023)", H2))
    lrows = [["Date", "Type", "Vch #", "Account", "Narration", "Debit", "Credit", "Running total"]]
    run = opn
    lrows.append(["", "OPN", "", "Opening balance (old ERP)", "", n(max(opn, 0)), n(max(-opn, 0)), n(run)])
    for vd, vt, vno, ac, an, nar, amt in live:
        amt = float(amt)
        run += amt
        lrows.append([d(vd), vt, str(vno), an or ac, nar or "", n(max(amt, 0)), n(max(-amt, 0)), n(run)])
    story.append(table(lrows, [22 * mm, 12 * mm, 14 * mm, 50 * mm, 95 * mm, 24 * mm, 24 * mm, 28 * mm],
                       num_cols=(5, 6, 7)))
    build("flag06_partners_drawings.pdf", story)


# ---------------------------------------------------------------- flag 7
def flag7(cur):
    rows = dash.rows(cur, """
        SELECT l.vd, l.vt, l.tbl, l.vno, l.naration, l.amt
        FROM dostex.acl l WHERE l.ac LIKE '200201%'
        ORDER BY CASE WHEN l.vt='OPN' THEN 0 ELSE 1 END, l.vd, l.vno""")
    opn = -sum(float(r[5]) for r in rows if r[2] == "ABALS")
    live = [r for r in rows if r[2] != "ABALS"]

    def kind(r):
        if r[2] == "GAC":
            return "Sales tax on invoices"
        if r[1] in ("CP", "BP"):
            return "Paid (cash/bank)"
        return "Journal adjustments"

    fys = sorted({fy(r[0]) for r in live})
    kinds = ["Sales tax on invoices", "Journal adjustments", "Paid (cash/bank)"]
    by = defaultdict(float)
    for r in live:
        by[(kind(r), fy(r[0]))] += -float(r[5])  # payable shown positive

    story = [Paragraph("Flag 7 - Sales tax payable (200201 SALES TAX P/A)", H1),
             Paragraph("Amounts are shown as the liability: a positive figure increases what is owed to the tax "
                       "department, a figure in brackets reduces it. Sales tax on invoices comes from the "
                       "conversion/fabric gate-pass invoices (GST charged to customers).", BODY),
             Paragraph("Summary by fiscal year (Jul-Jun)", H2)]
    hdr = ["", *[f + (" (to date)" if f == fys[-1] else "") for f in fys], "Total"]
    srows = [hdr]
    bal = opn
    open_row, close_row = ["Opening balance"], ["Closing balance"]
    for f in fys:
        open_row.append(n(bal, False))
        bal += sum(by[(k, f)] for k in kinds)
        close_row.append(n(bal, False))
    srows.append(open_row + [n(opn, False)])
    for k in kinds:
        v = [by[(k, f)] for f in fys]
        srows.append([k] + [n(x) for x in v] + [n(sum(v))])
    srows.append(close_row + [n(bal, False)])
    story.append(table(srows, [55 * mm] + [32 * mm] * (len(hdr) - 1), num_cols=range(1, len(hdr)),
                       total_rows=(len(srows) - 1,)))
    charged = sum(by[("Sales tax on invoices", f)] for f in fys)
    paid = -sum(by[("Paid (cash/bank)", f)] for f in fys)
    story.append(Spacer(1, 4))
    story.append(Paragraph(f"Since go-live: sales tax charged on invoices Rs {charged:,.0f}; paid through cash/bank "
                           f"Rs {paid:,.0f}; balance owed now <b>Rs {bal:,.0f}</b>. Check this against the sales-tax "
                           "returns and payment challans actually filed with FBR - payments made from an account not "
                           "recorded in this ERP would not show here.", BODY))
    story.append(PageBreak())
    story.append(Paragraph("Full ledger", H2))
    lrows = [["Date", "Type", "Vch/Inv #", "Narration", "Increase", "Decrease", "Balance owed"]]
    run = opn
    lrows.append(["", "OPN", "", "Opening balance (old ERP)", n(max(opn, 0)), n(max(-opn, 0)), n(run, False)])
    for vd, vt, tbl, vno, nar, amt in live:
        v = -float(amt)
        run += v
        lrows.append([d(vd), vt, str(vno), nar or "", n(max(v, 0)), n(max(-v, 0)), n(run, False)])
    story.append(table(lrows, [22 * mm, 16 * mm, 18 * mm, 120 * mm, 28 * mm, 28 * mm, 30 * mm], num_cols=(4, 5, 6)))
    build("flag07_sales_tax_payable.pdf", story)


# ---------------------------------------------------------------- flag 10
def flag10(cur):
    rows = dash.rows(cur, """
        SELECT v.trans_date, v.gp_no, v.trans_no, v.party, v.deliver_to, s.cont_no, s.quality,
               v.than, v.total_mtr, v.inv_no
        FROM dostex.fab_gp_v v LEFT JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id
        WHERE v.trans_type='GPOUT' AND NVL(v.rate,0)=0
        ORDER BY v.trans_date, v.trans_no, v.sr_no""")
    story = [Paragraph("Flag 10 - Fabric dispatched with no rate (not invoiced)", H1),
             Paragraph("Outward fabric gate passes (GPOUT) where the rate is blank or zero. Because the ERP "
                       "values an invoice as meters x rate, these dispatches produced no invoice amount, no "
                       "receivable and no sales in the ledger. Some may be legitimately free (samples, stock "
                       "transfers); the rest are revenue that was never billed. Returns (GPIN) without a rate are "
                       "not included.", BODY),
             Paragraph("Summary by party", H2)]
    by = defaultdict(lambda: [0, 0.0, None, None])
    for r in rows:
        b = by[r[3]]
        b[0] += 1; b[1] += float(r[8] or 0)
        b[2] = min(b[2], r[0]) if b[2] else r[0]
        b[3] = max(b[3], r[0]) if b[3] else r[0]
    srows = [["Party", "Lines", "Meters", "First", "Last"]]
    for p, (c, m, f, l) in sorted(by.items(), key=lambda x: -x[1][1]):
        srows.append([p, str(c), f"{m:,.0f}", d(f), d(l)])
    srows.append(["Total", str(len(rows)), f"{sum(float(r[8] or 0) for r in rows):,.0f}", "", ""])
    story.append(table(srows, [70 * mm, 18 * mm, 28 * mm, 28 * mm, 28 * mm], num_cols=(1, 2),
                       total_rows=(len(srows) - 1,)))
    story.append(Paragraph("Detail", H2))
    lrows = [["Date", "GP #", "Trans #", "Party", "Delivered to", "Contract", "Quality", "Than", "Meters", "Inv #"]]
    for r in rows:
        lrows.append([d(r[0]), str(r[1] or ""), str(r[2]), r[3] or "", r[4] or "", str(r[5] or ""),
                      r[6] or "", f"{float(r[7] or 0):,.0f}", f"{float(r[8] or 0):,.0f}", str(r[9] or "")])
    story.append(table(lrows, [21 * mm, 13 * mm, 15 * mm, 40 * mm, 50 * mm, 16 * mm, 62 * mm, 13 * mm, 20 * mm, 14 * mm],
                       num_cols=(7, 8)))
    build("flag10_unbilled_dispatches.pdf", story)


def main():
    OUT.mkdir(exist_ok=True)
    cfg = dash.load_config()
    con = dash.connect(cfg)
    cur = con.cursor()
    for f in (flag1, flag6, flag7, flag10):
        f(cur)
        print("built", f.__name__)


if __name__ == "__main__":
    main()
