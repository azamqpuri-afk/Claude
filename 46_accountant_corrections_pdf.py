"""
46_accountant_corrections_pdf.py
One PDF (2026-09-26) for the accountant: every ERP transaction found to need
correction during the reporting work, with document references (GRN/SRN #,
gate pass #, voucher #) and a suggested debit/credit entry. Also lists items
for the partners to review and the year-end closing entries applied in the
Google Sheets. Read-only on the ERP - nothing here is posted by this script.
"""

import importlib.util
import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import closing_entries as ce  # noqa: E402
import merged_ledger as ml  # noqa: E402


def _load(name, file):
    s = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


dash = _load("dash", "40_business_dashboard.py")
fp = _load("fp", "42_flag_detail_pdfs.py")
st = _load("st", "45_stores_reconciliation_pdf.py")
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer, Table, TableStyle  # noqa: E402

ACC = {  # account code -> name, as they appear in the ERP chart of accounts
    "300501007": "SALES TAX RECEIVEABLE", "300201001": "YARN, THREAD STOCK",
    "350105002": "CONVERSION YARN SALE INCOME", "350205001": "PROFIT & LOSS ON SALE OF FIXED ASSET",
    "100201001": "ACCUMMULATED DEPRICIATION (capital reserves)", "300308001": "KHALID REJECTION",
    "350201002": "MISC. INCOME", "300101001": "STORES & SPARES-INVENTORY",
    "400301001": "STORES AND SPARES CONSUMED", "250106003": "SOLAR PLANT",
}
entry_no = [0]


def acct(code):
    return f"{code} {ACC.get(code, '')}".strip()


def entry(ref, lines, note=None):
    """A suggested journal: lines = [(dr_or_cr, account, amount), ...]."""
    entry_no[0] += 1
    rows = [["", "Account", "Debit", "Credit"]]
    for side, ac, amt in lines:
        rows.append(["Dr" if side == "Dr" else "   Cr", ac, fp.n(amt) if side == "Dr" else "", fp.n(amt) if side == "Cr" else ""])
    t = Table(rows, colWidths=[12 * mm, 120 * mm, 30 * mm, 30 * mm])
    t.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 8), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
                           ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.grey), ("ALIGN", (2, 0), (3, -1), "RIGHT"),
                           ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F6F8")),
                           ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#1F3B57"))]))
    out = [Paragraph(f"<b>Suggested entry {entry_no[0]}</b> - {ref}", fp.SMALL), t]
    if note:
        out.append(Paragraph(note, fp.SMALL))
    out.append(Spacer(1, 5))
    return out


def main():
    cur = dash.connect(dash.load_config()).cursor()
    rows = dash.rows
    story = [Paragraph("Dost Textiles - accounting corrections for review", fp.H1),
             Paragraph(f"Prepared {date.today():%d-%b-%Y} from the ERP data (read only - nothing has been posted). "
                       "Each item gives the document to open, what is wrong, and a suggested debit/credit entry. "
                       "Please check each against the source documents before posting. Amounts in Rs.", fp.BODY)]
    summary = []

    # ---------------------------------------------------------------- A1 SRN missing sales tax
    srn = rows(cur, """
        SELECT m.dno, m.dd, a.an, m.bno, m.bd, m.gp_no, m.ge_no, x.gap
        FROM dostex.dmn m LEFT JOIN dostex.ach a ON a.ac = m.ac
        JOIN (SELECT vno, TRUNC(vd) vd, -SUM(amt) gap FROM dostex.party_ledger_v
              WHERE tbl='DMN' AND vt='SRN' GROUP BY vno, TRUNC(vd) HAVING ABS(SUM(amt)) > 1) x
          ON x.vno = m.dno AND x.vd = TRUNC(m.dd)
        WHERE m.dt = 'SRN' ORDER BY m.dd""")
    tot1 = sum(float(r[7]) for r in srn)
    a1 = [Paragraph("A1. Store receipts (GRN / SRN) posted without their sales-tax line", fp.H2),
          Paragraph("On each GRN below the supplier was credited with the full bill including sales tax, stores were "
                    "debited with the amount before tax, and the sales-tax (input tax) line is missing because the "
                    "voucher's sales-tax account (AC2) is blank. The voucher does not balance, so a separate journal "
                    "cannot fix it. <b>Fix: open each GRN, select the sales-tax account 300501007 SALES TAX RECEIVEABLE "
                    "and save again.</b> The ERP will then post the missing debit line shown.", fp.BODY)]
    t = [["GRN #", "GRN date", "Supplier", "Bill # / date", "Gate pass #", "Gate entry #", "Missing debit: Sales tax receivable"]]
    for dno, dd, sup, bno, bd, gp, ge, gap in srn:
        t.append([str(dno), fp.d(dd), sup or "", f"{bno or ''} / {fp.d(bd)}", str(gp or ""), str(ge or ""), fp.n(float(gap))])
    t.append(["", "", "Total", "", "", "", fp.n(tot1)])
    a1.append(fp.table(t, [16 * mm, 24 * mm, 62 * mm, 40 * mm, 22 * mm, 24 * mm, 50 * mm], num_cols=(6,), total_rows=(len(t) - 1,)))
    a1 += entry("per GRN above (the line each GRN should have posted)",
                [("Dr", acct("300501007"), tot1), ("Cr", "(already credited to the supplier on the GRN - no new credit)", 0)],
                "Posting this through the GRN itself (not a JV) keeps each voucher balanced. After this, the balance sheet "
                "difference of Rs 84,737 disappears (the remaining Rs 2 is rounding).")
    summary.append(("A1", "9 GRNs missing sales-tax line", "GRN 117, 184, 207, 311, 382, 432, 499, 603, 656", tot1))
    story += a1

    # ---------------------------------------------------------------- A2 yarn stock
    story.append(Paragraph("A2. Yarn sale and store sale posted to YARN, THREAD STOCK", fp.H2))
    story.append(Paragraph("YARN, THREAD STOCK (300201001) has never had a yarn receipt; its only two entries are sales, "
                           "leaving an impossible credit (negative) stock of Rs 27,631. The partners decided these belong "
                           "in yarn sale income.", fp.BODY))
    t = [["Document", "Date", "Party", "Detail", "Amount"],
         ["Outward yarn gate pass OGP_YARN trans # 36, gate pass # 15", "21-May-2025", "WAHAB SB", "B#36 12/1 CTN @27,500", "27,500"],
         ["Store sale bill # 6", "18-Feb-2025", "AR STORE", "posted to yarn stock account", "131"]]
    story.append(fp.table(t, [80 * mm, 24 * mm, 30 * mm, 70 * mm, 24 * mm], num_cols=(4,)))
    story += entry("OGP_YARN trans # 36 (GP # 15) and store sale bill # 6",
                   [("Dr", acct("300201001"), 27631), ("Cr", acct("350105002"), 27631)],
                   "Better still: on OGP_YARN # 36 change the credit account (DC_AC) from 300201001 to 350105002, and on "
                   "sale bill # 6 change the sales account; the ERP then posts it correctly at source.")
    summary.append(("A2", "Sales posted to yarn stock", "OGP_YARN # 36 / GP # 15; sale bill # 6", 27631))

    # ---------------------------------------------------------------- A3 vehicle
    story.append(Paragraph("A3. Vehicle LE-6892 sale - accumulated depreciation that was never charged", fp.H2))
    story.append(Paragraph("When vehicle LE-6892 (cost 17,50,500) was sold for 14,25,000 on 04-Mar-2019 (old ERP JV # 1, "
                           "through Jadoon Motors), the entry assumed Rs 8,20,213 of depreciation had been built up and "
                           "booked a profit on sale of 4,94,713. No depreciation was ever booked, so the 8,20,213 has sat as "
                           "a debit in CAPITAL RESERVES / ACCUMMULATED DEPRICIATION (100201001) ever since, and came into the "
                           "new ERP in the opening balances. The real result was a loss of 3,25,500.", fp.BODY))
    story += entry("old ERP JV # 1 dated 04-Mar-2019; new ERP opening balance of 100201001",
                   [("Dr", acct("350205001"), 820213), ("Cr", acct("100201001"), 820213)],
                   "Partners' decision: write it off against the profit on sale of the vehicle.")
    summary.append(("A3", "Vehicle sale - depreciation never charged", "old ERP JV # 1, 04-Mar-2019", 820213))

    # ---------------------------------------------------------------- A4 Khalid
    story.append(Paragraph("A4. KHALID REJECTION - Rs 177 over-received", fp.H2))
    story.append(Paragraph("Invoices 119 (22-Apr-2024), 202 (31-Aug-2024) and 251 (13-Nov-2024) total 33,61,998; receipts "
                           "total 33,62,175, the last being CR # 4 dated 11-Dec-2024 (Rs 21,074 through Mian Azam Sb). The "
                           "customer account shows a Rs 177 credit. Partners' decision: write it off.", fp.BODY))
    story += entry("KHALID REJECTION account 300308001, last receipt CR # 4 dated 11-Dec-2024",
                   [("Dr", acct("300308001"), 177), ("Cr", acct("350201002"), 177)])
    summary.append(("A4", "Customer over-receipt written off", "KHALID REJECTION, CR # 4 11-Dec-2024", 177))

    # ---------------------------------------------------------------- A5 stores
    story.append(PageBreak())
    story.append(Paragraph("A5. Monthly store consumption journals that do not match the store slips", fp.H2))
    story.append(Paragraph("Each month's journal \"BILL M/O ... (STORE)\" should credit STORES &amp; SPARES-INVENTORY with the "
                           "store's issue slips minus its return slips (at average rate). Main causes found: return slips are "
                           "deducted only about half the time; the Jul-2026 journal has the consumption line as 0; and in "
                           "Oct-2023 (new ERP go-live) all slips were keyed after the journal. <b>Rule going forward: always "
                           "deduct the month's return slips, and tie the journal to the store's issue report.</b> Sep-2026 is not "
                           "listed - its journal is simply not posted yet.", fp.BODY))
    months, iss, ret, jv = st.monthly(cur, rows)
    diff = {m: jv.get(m, (0, 0, 0.0))[2] - (iss[m] - ret[m]) for m in months}
    last_posted = max(m for m in jv)
    listed = ["2023-10", "2025-01", "2025-07", "2025-11", "2025-12", "2026-06", "2026-07"]
    jv_ref = {mo: (vno, vd) for vno, vd, mo in rows(cur, """
        SELECT tm.vno, tm.vd, TO_CHAR(tm.vd,'YYYY-MM') FROM dostex.tm tm
        WHERE tm.vt='JV' AND EXISTS (SELECT 1 FROM dostex.td td WHERE td.trans_no = tm.trans_no AND td.ac LIKE '300101%')""")}

    def slips(mo, dt):
        r = rows(cur, """SELECT m.dno, SUM(d.qty * d.avg_rate) FROM dostex.dmn m
                         JOIN dostex.ddt d ON d.trans_no = m.trans_no AND d.uc = m.uc
                         WHERE m.dt = :dt AND TO_CHAR(m.dd,'YYYY-MM') = :mo GROUP BY m.dno ORDER BY m.dno""", dt=dt, mo=mo)
        return r

    why = {"2023-10": "New ERP go-live month: all 31 issue slips were keyed after this journal, which was prepared from paper records.",
           "2025-01": "Return slips for the month were not deducted.",
           "2025-07": "Journal lower than the issue slips (no returns, no late slips) - check the store issue report used.",
           "2025-11": "Return slips (Rs 4,73,571) were not deducted. Largest: return slip # 25 dated 13-Nov-2025, 80 units CABLE 7X29 1COR @ 5,598 = 4,47,867. If this cable had been issued for the solar installation, credit SOLAR PLANT (250106003) for that part instead of consumption.",
           "2025-12": "Journal lower than the issue slips - check the store issue report used.",
           "2026-06": "Return slips (Rs 73,360) were not deducted.",
           "2026-07": "The STORES AND SPARES CONSUMED line was keyed as 0; only generator diesel (62,564) and a Rs 105 home item were posted."}
    tot_listed = 0.0
    for mo in listed:
        d = diff[mo]
        tot_listed += d
        vno, vd = jv_ref.get(mo, ("", None))
        rs = slips(mo, "RET")
        iss_s = slips(mo, "ISS")
        ref = (f"{datetime.strptime(mo, '%Y-%m'):%b-%Y}: JV # {vno} dated {fp.d(vd)} - store issued net {fp.n(iss[mo] - ret[mo])}, "
               f"journal {fp.n(jv.get(mo, (0, 0, 0.0))[2])}")
        detail = (f"Issue slips # {iss_s[0][0]}-{iss_s[-1][0]} ({len(iss_s)} slips, {fp.n(iss[mo])})" if iss_s else "No issue slips") + \
                 ("; return slips " + ", ".join(f"# {r[0]} ({fp.n(float(r[1] or 0))})" for r in rs) if rs else "; no return slips")
        lines = ([("Dr", acct("300101001"), d), ("Cr", acct("400301001"), d)] if d > 0 else
                 [("Dr", acct("400301001"), -d), ("Cr", acct("300101001"), -d)])
        story.append(KeepTogether(entry(ref, lines, f"{why[mo]} {detail}.")))

    old_cum = sum(v for m, v in diff.items() if "2016-10" <= m < "2023-10")
    small = sum(v for m, v in diff.items() if "2023-10" <= m <= last_posted and m not in listed)
    for label, amt, note in (
            ("Old ERP period Oct-2016 to Sep-2023 - net of all monthly differences (one entry in the new ERP)", old_cum,
             "The old ERP is closed, so the net drift of that period is corrected once. Months with the biggest differences: "
             "Feb-2017 +1,67,673; Oct-2021 +1,42,780; Nov-2021 +1,44,422; Sep-2023 -1,01,507 (see the separate stores PDF)."),
            (f"New ERP - all other months Oct-2023 to {datetime.strptime(last_posted, '%Y-%m'):%b-%Y} (small differences, net)", small,
             "Each of these months differs by less than Rs 50,000.")):
        lines = ([("Dr", acct("300101001"), amt), ("Cr", acct("400301001"), amt)] if amt > 0 else
                 [("Dr", acct("400301001"), -amt), ("Cr", acct("300101001"), -amt)])
        story.append(KeepTogether(entry(label, lines, note)))
    store_total = tot_listed + old_cum + small
    story.append(Paragraph(f"After all A5 entries, STORES &amp; SPARES-INVENTORY equals the store records (net change "
                           f"{fp.n(store_total)} - positive means stock goes up and consumption down).", fp.BODY))
    summary.append(("A5", "Store consumption journals vs store slips", "7 monthly JVs + 2 net entries", abs(store_total)))

    # ---------------------------------------------------------------- A6 unbilled dispatches
    story.append(PageBreak())
    story.append(Paragraph("A6. Fabric dispatched on gate passes with no rate (not invoiced)", fp.H2))
    gps = rows(cur, """
        SELECT gp_no, trans_no, trans_date, party, COUNT(*), SUM(total_mtr)
        FROM dostex.fab_gp_v WHERE trans_type='GPOUT' AND NVL(rate,0)=0
        GROUP BY gp_no, trans_no, trans_date, party ORDER BY trans_date, trans_no""")
    tm = sum(float(g[5] or 0) for g in gps)
    story.append(Paragraph(f"{len(gps)} outward gate passes carry {tm:,.0f} meters with no rate, so the ERP raised no invoice, "
                           "no receivable and no sale. Most of it is KHALID REJECTION (54,444 m on 08-Jun-2024) and BABAR(PV) "
                           "(10,667 m on 20-Jan-2024). <b>Fix: open each gate pass, enter the agreed rate (and GST %), and save "
                           "- the ERP will create the invoice.</b> If a dispatch was genuinely free (sample, stock transfer), "
                           "note that on the gate pass instead.", fp.BODY))
    t = [["Gate pass #", "Trans #", "Date", "Party", "Lines", "Meters"]]
    for gp, tno, td, party, cnt, mtr in gps:
        t.append([str(gp or ""), str(tno), fp.d(td), party or "", str(cnt), f"{float(mtr or 0):,.0f}"])
    t.append(["", "", "", "Total", "", f"{tm:,.0f}"])
    story.append(fp.table(t, [22 * mm, 18 * mm, 24 * mm, 80 * mm, 14 * mm, 26 * mm], num_cols=(4, 5), total_rows=(len(t) - 1,)))
    story += entry("each gate pass above, once a rate is entered (amount = meters x rate)",
                   [("Dr", "Customer account (party on the gate pass)", 0),
                    ("Cr", "350106001 SALE OF REJEC. / 350104001 SALE OF FABRIC / 350105001 CONVERSION INCOME (as applicable)", 0),
                    ("Cr", "200201001 SALES TAX P/A (if GST applies)", 0)],
                   "Amounts are blank because the rate is not in the ERP.")
    summary.append(("A6", "Gate passes with no rate - not invoiced", f"{len(gps)} gate passes, {tm:,.0f} m", None))

    # ---------------------------------------------------------------- B review items
    story.append(PageBreak())
    story.append(Paragraph("B. Items for the partners and accountant to review (no single entry)", fp.H2))
    bal = dict(rows(cur, """SELECT ac, SUM(amt) FROM dostex.acl WHERE ac IN ('200105006','300306013','200201001','100101001','100101002')
                            GROUP BY ac"""))
    neg_cash = []
    try:
        dj = json.loads((HERE / "business_dashboard" / "business_dashboard_data.json").read_text(encoding="utf-8"))
        for a in dj["cash"]["accounts"]:
            if a["kind"] != "Cash":
                continue
            v = None
            pairs = dict((i, b) for i, b in a["pairs"])
            for i in range(a["from"], a["to"] + 1):
                v = pairs.get(i, v)
                if v is not None and v < 0:
                    neg_cash.append(f"{dj['months'][i]} ({v:,.0f})")
    except Exception:
        pass
    items = [
        ("B1", "MIAN AZAM(STORE) (200105006) is a store-supplier account used for owner transactions",
         f"Balance {fp.n(float(bal.get('200105006', 0)))} (debit). Owner cash in/out, 'drawing for home', bardana/scrap/gatta sales, "
         "conversion invoices (e.g. INV 569, 571, 574 in Aug-2026) and a Rs 60 lac transfer to DF (JV # 4, 31-Jan-2026) all pass "
         "through it. Suggest: owner items to the partners' drawing / current accounts; by-product sales Dr cash or customer, "
         "Cr the sales account; keep this account for store purchases only."),
        ("B2", "DF (300306013) shown as a customer owing Rs 60 lac",
         f"Balance {fp.n(float(bal.get('300306013', 0)))}. Created by JV # 4 dated 31-Jan-2026 ('C/O MIAN AZAM SB' / 'TO DF'), not by any "
         "invoice. Confirm what DF is; if it is a partner or a loan, move it to the right account."),
        ("B3", "Partners' capital all in one name",
         f"HINA RASHEED {fp.n(-float(bal.get('100101002', 0)))} credit; MUHAMMAD AZAM QAYYUM {fp.n(-float(bal.get('100101001', 0)))}. "
         "Check against the partnership deed and the AOP return."),
        ("B4", "Sales tax payable (200201001) keeps growing",
         f"Balance {fp.n(-float(bal.get('200201001', 0)))} credit. Reconcile with the sales-tax returns and CPRs actually paid "
         "(the Google Sheets write all tax accounts off each 30 June by the partners' decision, but the ERP still shows this)."),
        ("B5", "Cash at factory went negative at some month-ends",
         ("Months: " + ", ".join(neg_cash[:30]) + (" ..." if len(neg_cash) > 30 else "")) if neg_cash else
         "Physically impossible - receipts are being entered late or missed."),
        ("B6", "Partner salary booked inside drawings",
         "Journals such as 'FOR MANAGING PARTNER SALARY + DRAWING FOR HOME' (15-Oct-2025). In an AOP a partner's salary is a "
         "share of profit, not an expense - keep it out of expenses."),
        ("B7", "Items taken home from the factory store",
         "The monthly store JV charges small amounts to DRAWING FOR REPAIR HOME (Rs 50 to 1,457 a month) - correct treatment; "
         "just make sure every such issue has a store slip marked for home."),
        ("B8", "Depreciation", "Not booked in either ERP since 2014. Partners decided not to book it; noted for the tax return, "
                              "where tax depreciation (section 22) is still claimed."),
    ]
    t = [["#", "Item", "Detail / suggestion"]] + [[a, b, c] for a, b, c in items]
    story.append(fp.table(t, [10 * mm, 70 * mm, 180 * mm]))
    for a, b, _ in items:
        summary.append((a, b, "review", None))

    # ---------------------------------------------------------------- C closing entries
    story.append(PageBreak())
    story.append(Paragraph("C. Year-end closing entries (as decided by the partners and applied in the Google Sheets)", fp.H2))
    story.append(Paragraph("Neither ERP has ever closed a year. The Google Sheets close each 30 June as below; the accountant can "
                           "post the same entries in the ERP if wanted. Every year's lines are in the 'Closing entries' tab of the "
                           "balance-sheet Google Sheet. Order: taxes written off, adjustments, drawings as expense, the rest split "
                           "50/50. If the A5 store entries are posted, the stores true-up (H) below becomes zero.", fp.BODY))
    months_all = dash.month_list(ml.FIRST_MONTH, date.today().strftime("%Y-%m"))
    L = ml.Ledger(cur, rows, months_all)
    dates = [(date(y, 6, 30), "") for y in range(2014, date.today().year + 1) if date(y, 6, 30) < date.today()] + [(date.today(), "")]
    C = ce.Closing(L, dates, st.load_store_gap(cur, rows, from_month="2016-10"))
    yrs = C.year_rows()
    t = [["FY", "Profit as booked", "Vehicle (D)", "Stores (H)", "KHALID (J)", "Taxes written off (F)", "Drawings (B)",
          "Left, split 50/50", "Each partner"]]
    for y in yrs:
        t.append([y["fy"], fp.n(y["profit_booked"]), fp.n(-y["vehicle"]), fp.n(y["stores"]), fp.n(y["khalid"]),
                  fp.n(-y["tax"]), fp.n(-y["drawings"]), fp.n(y["pad"]), fp.n(y["pad"] / 2)])
    story.append(fp.table(t, [14 * mm, 30 * mm, 24 * mm, 24 * mm, 22 * mm, 34 * mm, 30 * mm, 32 * mm, 28 * mm], num_cols=range(1, 9)))
    y = yrs[-1]
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Example - {y['fy']} closing (30-Jun-{y['fye'].year}):", fp.BODY))
    ex = []
    for h, lbl in (("300501", "300501 ADVANCE INCOME TAX & SALES TAX RECEIVABLE"), ("200201", "200201 SALES TAX PAYABLE"),
                   ("200202", "200202 WITHHOLDING TAX PAYABLE")):
        v = y["tax_by_head"][h]
        if abs(v) >= 1:
            ex.append(("Dr", "TAXES WRITTEN OFF (P&L)", v) if v > 0 else ("Dr", lbl, -v))
            ex.append(("Cr", lbl, v) if v > 0 else ("Cr", "TAXES WRITTEN OFF (P&L)", -v))
    ex += [("Dr", "PROFIT & LOSS ACCOUNT (drawings treated as expense)", y["drawings"]), ("Cr", "100303 PARTNERS' DRAWING", y["drawings"]),
           ("Dr", "PROFIT & LOSS ACCOUNT", y["pad"]), ("Cr", "100101001 CAPITAL - MUHAMMAD AZAM QAYYUM (50%)", y["pad"] / 2),
           ("Cr", "100101002 CAPITAL - HINA RASHEED (50%)", y["pad"] / 2)]
    story += entry(f"{y['fy']} year-end closing", ex, "Zakat & sadqa for the year is also closed to capital.")

    # ---------------------------------------------------------------- summary page (inserted after intro)
    srows = [["#", "Correction", "Reference", "Amount"]]
    for a, b, c, amt in summary:
        srows.append([a, b, c, fp.n(amt) if amt else ("rate needed" if a == "A6" else "-")])
    head = [Paragraph("Summary", fp.H2), fp.table(srows, [12 * mm, 100 * mm, 110 * mm, 30 * mm], num_cols=(3,)),
            Spacer(1, 4),
            Paragraph("A = entries to correct in the ERP (with suggested debit/credit). B = items to review with the partners. "
                      "C = year-end closing entries as applied in the Google Sheets.", fp.SMALL), PageBreak()]
    story = story[:2] + head + story[2:]
    fp.OUT.mkdir(exist_ok=True)
    fp.build("dost_textiles_corrections_for_accountant.pdf", story)
    print("built", entry_no[0], "entries; stores net", round(store_total), "old", round(old_cum), "small", round(small))


if __name__ == "__main__":
    main()
