"""
44_stock_accounts_pdf.py
One-off PDF (2026-09-25) of the two stock accounts, so the owner can judge
them before any year-end adjustment: STORES & SPARES (new 300101 / old 60110)
and YARN, THREAD STOCK (new 300201 / old 60130), across both ERPs.
Read-only on the ERP.
"""

import importlib.util
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import merged_ledger as ml  # noqa: E402

_s = importlib.util.spec_from_file_location("dash", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)
_s = importlib.util.spec_from_file_location("fp", HERE / "42_flag_detail_pdfs.py")
fp = importlib.util.module_from_spec(_s); _s.loader.exec_module(fp)
from reportlab.platypus import PageBreak, Paragraph, Spacer  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402

KIND = {"SRN": "Received (store receipts)", "JV": "Issued / consumed (journals)", "SAL": "Store sales",
        "ADJ": "Adjustments", "OGP_YARN": "Yarn gate-out"}


def main():
    cur = dash.connect(dash.load_config()).cursor()
    months = dash.month_list(ml.FIRST_MONTH, date.today().strftime("%Y-%m"))
    L = ml.Ledger(cur, dash.rows, months)
    story = [Paragraph("Stock accounts - Stores &amp; spares and Yarn stock (Jan-2014 to today)", fp.H1),
             Paragraph("Both ERPs combined (old ERP to Sep-2023, new ERP from Oct-2023). Debit (increase) = goods "
                       "received into the store at cost; credit (decrease, in brackets) = goods issued to the "
                       "floor, which the accounts team books once a month by journal to 'Stores &amp; spares "
                       "consumed'. The closing balance is what the books say is still in the store; it is only right "
                       "if the monthly issue journals match what physically left the store.", fp.BODY)]

    for head, title in (("300101", "STORES & SPARES"), ("300201", "YARN, THREAD STOCK")):
        by = defaultdict(lambda: defaultdict(float))
        opening = sum(v for ac, an, h, v in L.new_opening if h == head)
        old_close = 0.0
        for era, m, ac, an, h, vt, tbl, amt in L.records:
            if h == head:
                by[m][vt] += amt
                if era == "old":
                    old_close += amt
        kinds = sorted({k for mv in by.values() for k in mv}, key=lambda k: (k != "SRN", k != "JV", k))
        story.append(Paragraph(f"{title} - by fiscal year (Jul-Jun)", fp.H2))
        fys = defaultdict(lambda: defaultdict(float))
        for m, mv in by.items():
            y, mo = int(m[:4]), int(m[5:])
            for k, v in mv.items():
                fys[y + 1 if mo >= 7 else y][k] += v
        rows = [["FY"] + [KIND.get(k, k) for k in kinds] + ["Net change", "Closing"]]
        run = 0.0
        for y in sorted(fys):
            net = sum(fys[y].values())
            if y == 2024:   # the new ERP starts from the old ERP's closing figure
                run += opening - old_close
            run += net
            rows.append([f"FY{y % 100:02d}"] + [fp.n(fys[y].get(k, 0.0)) for k in kinds] + [fp.n(net), fp.n(run, False)])
        w = [18 * mm] + [(230 * mm) / (len(kinds) + 2)] * (len(kinds) + 2)
        story.append(fp.table(rows, w, num_cols=range(1, len(rows[0]))))
        if abs(opening - old_close) >= 1:
            story.append(Paragraph(f"Old ERP closing {fp.n(old_close, False)} vs new ERP opening {fp.n(opening, False)}.", fp.SMALL))

        if head == "300101":
            story.append(PageBreak())
            story.append(Paragraph("STORES &amp; SPARES - month by month", fp.H2))
            rows = [["Month"] + [KIND.get(k, k) for k in kinds] + ["Closing"]]
            run = 0.0
            for m in months:
                if m == ml.SWITCH_MONTH:
                    run = opening
                mv = by.get(m, {})
                run += sum(mv.values())
                if mv or m == months[-1]:
                    rows.append([m] + [fp.n(mv.get(k, 0.0)) for k in kinds] + [fp.n(run, False)])
            story.append(fp.table(rows, [22 * mm] + [(226 * mm) / (len(kinds) + 1)] * (len(kinds) + 1),
                                  num_cols=range(1, len(rows[0]))))
            story.append(PageBreak())
        else:
            story.append(Paragraph("YARN, THREAD STOCK - every entry", fp.H2))
            lines = fp.dash.rows(cur, """
                SELECT l.vd, l.vt, l.vno, l.naration, l.amt FROM dostex.acl l
                WHERE l.ac LIKE '300201%' ORDER BY l.vd""")
            old = fp.dash.rows(cur, """
                SELECT l.vd, l.vt, l.vno, l.naration, l.amt FROM dostt.acl l
                WHERE l.ac LIKE '60130%' ORDER BY l.vd""")
            rows = [["Date", "Type", "Vch #", "Narration", "Debit", "Credit"]]
            for vd, vt, vno, nar, amt in list(old) + list(lines):
                a = float(amt or 0)
                rows.append([fp.d(vd), vt, str(vno), nar or "", fp.n(max(a, 0)), fp.n(max(-a, 0))])
            story.append(fp.table(rows, [24 * mm, 22 * mm, 16 * mm, 130 * mm, 28 * mm, 28 * mm], num_cols=(4, 5)))
            story.append(Spacer(1, 6))
            story.append(Paragraph("No yarn receipt was ever booked into this account, so the credit balance cannot be "
                                   "real stock. The Rs 27,500 credit came from a yarn gate-out (OGP_YARN, 21-May-2025, 12/1 cotton yarn) whose "
                                   "sale value was posted here instead of to a sales account.", fp.BODY))
    fp.OUT.mkdir(exist_ok=True)
    fp.build("stock_accounts_stores_and_yarn.pdf", story)
    print("built")


if __name__ == "__main__":
    main()


