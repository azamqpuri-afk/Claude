"""
closing_entries.py
Year-end closing entries decided by the owner on 2026-09-25, applied ONLY in
the Google Sheets built by 41_pnl_balance_sheet.py. The Oracle ERP is never
written to - every figure here is derived from read-only ledger balances.

Applied at every fiscal year end (30 June, FY14..last completed FY):
  B  partners' drawings treated as an expense of the year, AFTER tax (owner's
     instruction 2026-09-25; tax is still worked out on profit before drawings,
     since drawings are not tax-deductible in an AOP)
  A  what is left after all expenses including drawings split 50/50 into the
     two partners' capital (MUHAMMAD AZAM QAYYUM, HINA RASHEED)
  C  zakat & sadqa for the year closed into capital
  D  FY19 only: the Rs 820,213 'accumulated depreciation' debit left by the
     sale of vehicle LE-6892 (04-Mar-2019) charged against that year's
     profit on sale of fixed assets
  F  ALL tax accounts written off every 30 June (owner's instruction): advance
     income tax / tax deducted by customers and sales tax receivable (300501),
     sales tax payable (200201), withholding tax payable (200202), plus the
     missing SRN sales-tax lines (K). The year's net movement goes to the P&L,
     as an expense if net debit, income if net credit. Replaces the earlier
     "tax deducted = expense" and G "net sales tax" treatment.
  J  KHALID REJECTION balance written off to income
Applied at every balance-sheet date (presentation):
  K  the sales-tax lines missing from store receipt (SRN) vouchers added to
     sales tax receivable, so the balance sheet balances
  H  Stores & spares trued up to store records every 30 June (approved
     2026-09-26): the difference between the accounts' monthly consumption
     journals and the store's own issue-minus-return slips (both ERPs, from
     Oct-2016 when the old ERP's store module started - extended 2026-09-26)
     is adjusted between STORES & SPARES and STORES CONSUMED.
     Yarn stock was corrected separately (merged_ledger.CORRECTIONS).
Left as booked: depreciation (E, ignored by instruction), accrued debit
balances (I).
"""

from collections import defaultdict
from datetime import date

from openpyxl.styles import Font

PL_TOPS = ("35", "40", "45", "55")
TAX_HEADS = ("300501", "200201", "200202")   # all tax accounts, written off yearly (F)
TAX_LEAVES = {"300501001", "551700004"}        # income tax deducted by party (new / old ERP)
STR_LEAVES = {"300501007", "551700003"}        # sales tax receivable (new / old ERP)
KHALID = {"300308001"}
AZAM = {"100101001", "151200001"}
HINA = {"100101002", "151200002"}
VEHICLE_WRITE_OFF = 820213.0
VEHICLE_FYE = date(2019, 6, 30)
PARTNERS = ("MUHAMMAD AZAM QAYYUM", "HINA RASHEED")


def fye_of(d):
    return date(d.year if (d.month, d.day) >= (6, 30) else d.year - 1, 6, 30)


class Closing:
    def __init__(self, L, dates, store_gap=None):
        self.L = L
        self.store_gap = store_gap or {}   # month -> journal consumption - store net issues
        self.dates = [d for d, _ in dates]
        today = self.dates[-1]
        self.fyes = [date(y, 6, 30) for y in range(2014, today.year + 1) if date(y, 6, 30) <= today]
        self.x = {}
        for d in set(self.dates) | set(self.fyes):
            self.x[d] = L.leaf_balances_at(d.isoformat())

    # -- helpers on a leaf-balance dict {(ac, head): v}
    @staticmethod
    def pl(x):
        return sum(v for (ac, h), v in x.items() if h[:2] in PL_TOPS)

    @staticmethod
    def leaves(x, acs):
        return sum(v for (ac, h), v in x.items() if ac in acs)

    @staticmethod
    def head(x, h6):
        return sum(v for (ac, h), v in x.items() if h == h6)

    @staticmethod
    def srn_gap(x):
        """K: sales-tax lines missing from SRN vouchers = what keeps the ledger balanced."""
        return -sum(x.values())

    def stores_true_up(self, fye):
        """H: cumulative over-charged consumption up to fye (added back to stock)."""
        if fye is None:
            return 0.0
        cut = f"{fye.year}-06"
        return sum(v for m, v in self.store_gap.items() if m <= cut)

    def tax(self, x):
        """All tax accounts (debit positive) incl. the missing SRN lines."""
        return sum(v for (ac, h), v in x.items() if h in TAX_HEADS) + self.srn_gap(x)

    def closed_profit(self, fye):
        """Cumulative profit after tax and closing adjustments, all years to fye."""
        if fye is None or fye not in self.x:
            return 0.0
        x = self.x[fye]
        return (-self.pl(x) - self.tax(x) - self.leaves(x, KHALID)
                - (VEHICLE_WRITE_OFF if fye >= VEHICLE_FYE else 0.0) - self.head(x, "100303")
                + self.stores_true_up(fye))

    def year_rows(self):
        """Per closed FY: dict of the year's figures."""
        out, prev = [], None
        for fye in self.fyes:
            x, p = self.x[fye], (self.x[prev] if prev else {})
            mv = lambda f, *a: f(x, *a) - (f(p, *a) if prev else 0.0)  # noqa: E731
            rev = -sum(v for (ac, h), v in x.items() if h[:2] == "35") + (
                sum(v for (ac, h), v in p.items() if h[:2] == "35") if prev else 0.0)
            profit = -mv(self.pl)
            row = {
                "fye": fye, "fy": f"FY{fye.year % 100:02d}",
                "revenue": rev, "expenses": rev - profit, "profit_booked": profit,
                "vehicle": VEHICLE_WRITE_OFF if fye == VEHICLE_FYE else 0.0,
                "khalid": -mv(self.leaves, KHALID),
                "tax": mv(self.tax),
                "tax_by_head": {h: mv(self.head, h) for h in TAX_HEADS},
                "srn": mv(self.srn_gap),
                "stores": self.stores_true_up(fye) - self.stores_true_up(prev),
                "drawings": mv(self.head, "100303"),
                "zakat": mv(self.head, "100301"),
                "cap_azam": -mv(self.leaves, AZAM), "cap_hina": -mv(self.leaves, HINA),
            }
            row["pbt"] = row["profit_booked"] - row["vehicle"] + row["khalid"] + row["stores"]
            row["pat"] = row["pbt"] - row["tax"]
            row["pad"] = row["pat"] - row["drawings"]
            out.append(row)
            prev = fye
        return out

    def adjusted(self, d):
        """Head-level balances at d after the closing entries (natural sign)."""
        x = self.x[d]
        fye = fye_of(d) if fye_of(d) >= self.fyes[0] else None
        xf = self.x[fye] if fye else {}
        b = defaultdict(float)
        cp = self.closed_profit(fye)
        cur_pl = 0.0
        for (ac, h), v in x.items():
            f = xf.get((ac, h), 0.0)
            if h[:2] in PL_TOPS:
                cur_pl += v - f
            elif h in TAX_HEADS:
                b["TAX_NET"] += v - f
            elif ac in KHALID:
                b[h] += v - f
            elif h == "100303":          # drawings: an expense of the year
                cur_pl += v - f
            elif h == "100301":
                b[h] += v - f
                b["DRAW_CLOSED"] += f
            elif h == "100201":
                b[h] += 0.0 if (fye and fye >= VEHICLE_FYE) else v
            elif ac in AZAM:
                b["CAP_AZAM"] += v
            elif ac in HINA:
                b["CAP_HINA"] += v
            elif h.startswith("3003") and v < 0:
                b["209001"] += v
            elif h.startswith("2001") and v > 0:
                b["309001"] += v
            else:
                b[h] += v
        b["CAP_AZAM"] -= cp / 2
        b["CAP_HINA"] -= cp / 2
        b["CUR_PROFIT"] = cur_pl
        b["300101"] += self.stores_true_up(fye)
        # K for the current (unclosed) year; earlier years are written off with the tax accounts
        b["TAX_NET"] += self.srn_gap(x) - (self.srn_gap(xf) if fye else 0.0)
        net = b.pop("TAX_NET")
        b["ST_ASSET" if net > 0 else "ST_LIAB"] = net
        return b


NAMES = {"CAP_AZAM": "Capital - MUHAMMAD AZAM QAYYUM", "CAP_HINA": "Capital - HINA RASHEED",
         "DRAW_CLOSED": "Less: zakat & sadqa closed to capital (prior years)",
         "CUR_PROFIT": "Profit for the current year after drawings (not yet closed)",
         "ST_ASSET": "TAX ACCOUNTS, CURRENT YEAR (net debit, written off at 30 June)",
         "ST_LIAB": "TAX ACCOUNTS, CURRENT YEAR (net credit, written off at 30 June)",
         "209001": "ADVANCES FROM CUSTOMERS", "309001": "ADVANCES TO SUPPLIERS",
}


def build_closing_sheets(wb, L, names, dates, helpers, srn, store_gap=None):
    header, write_row, NUM, NOTE, WARN, TOT_FONT, get_column_letter, old_flags = helpers
    C = Closing(L, dates, store_gap)
    n = len(dates)
    adj = [C.adjusted(d) for d in C.dates]
    nm = dict(names); nm.update(NAMES)

    # ---- Balance sheet after closing entries
    ws = wb.create_sheet("Balance sheet - closed", 1)
    ws["A1"] = "Dost Textiles - Balance Sheet after year-end closing entries"
    ws["A1"].font = Font(bold=True, size=14, color="1F3B57")
    ws["A2"] = ("Closing entries decided by the owner, applied here only (the ERP is read, never changed): profit 50/50 to "
                "partners after drawings (drawings treated as an expense), zakat closed to capital, all tax accounts (income tax deducted, sales tax receivable and payable, withholding, missing SRN "
                "sales-tax lines) written off to the P&L each 30 June, KHALID REJECTION written off, FY19 vehicle write-off. See 'Closing "
                "entries'. No depreciation (by instruction).")
    ws["A2"].font = NOTE
    header(ws, 4, ["Code", "Head"] + [lbl for _, lbl in dates], [False, False] + old_flags)
    r = 5

    def section(t):
        nonlocal r
        write_row(ws, r, None, t, [None] * n, "section"); r += 1

    def group(keys, sign, label):
        nonlocal r
        tot = [0.0] * n
        for k in keys:
            v = [sign * a.get(k, 0.0) for a in adj]
            if any(abs(z) >= 1 for z in v):
                write_row(ws, r, k if k[:1].isdigit() else None, nm.get(k, k), v); r += 1
                tot = [p + q for p, q in zip(tot, v)]
        write_row(ws, r, None, label, tot, "total"); r += 1
        return tot

    allk = sorted({k for a in adj for k in a})
    section("ASSETS")
    nca = group([k for k in allk if k.startswith("25")], 1, "Total non-current assets")
    ca = group([k for k in allk if k.startswith("30")] + ["ST_ASSET"], 1, "Total current assets")
    ta = [a + b for a, b in zip(nca, ca)]
    write_row(ws, r, None, "TOTAL ASSETS", ta, "grand"); r += 2
    section("LIABILITIES")
    ltl = group([k for k in allk if k.startswith("15")], -1, "Total long-term liabilities")
    cl = group([k for k in allk if k.startswith("20")] + ["ST_LIAB"], -1, "Total current liabilities")
    tl = [a + b for a, b in zip(ltl, cl)]
    write_row(ws, r, None, "TOTAL LIABILITIES", tl, "grand"); r += 2
    section("EQUITY")
    te = group(["CAP_AZAM", "CAP_HINA", "DRAW_CLOSED"] + [k for k in allk if k.startswith("10")] + ["CUR_PROFIT"],
               -1, "TOTAL EQUITY")
    r += 1
    tle = [a + b for a, b in zip(tl, te)]
    write_row(ws, r, None, "TOTAL LIABILITIES + EQUITY", tle, "grand"); r += 1
    diff = [a - b for a, b in zip(ta, tle)]
    write_row(ws, r, None, "Difference (assets - liabilities - equity)", diff)
    for c in range(1, n + 3):
        ws.cell(row=r, column=c).font = Font(bold=True, color="B91C1C")
    r += 2
    ws.cell(row=r, column=2, value="Drawings are treated as an expense before the 50/50 split, so each partner's "
            "capital already carries half of them. Zakat is closed to capital as one line. Stock is as booked, pending review (item H).").font = WARN
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 50
    for i in range(n):
        ws.column_dimensions[get_column_letter(3 + i)].width = 15
    ws.freeze_panes = "C5"

    # ---- Profit appropriation + partners' capital accounts
    yrs = C.year_rows()
    ap = wb.create_sheet("Profit appropriation", 2)
    ap["A1"] = "Profit appropriation and partners' capital accounts, by fiscal year (Jul-Jun)"
    ap["A1"].font = Font(bold=True, size=14, color="1F3B57")
    ap["A2"] = "FY14 is Jan-Jun 2014 only. The current year is not closed until 30 June."
    ap["A2"].font = NOTE
    header(ap, 4, ["", "Rs"] + [y["fy"] for y in yrs] + ["Total"])
    r = 5
    tot = lambda k: [y[k] for y in yrs] + [sum(y[k] for y in yrs)]  # noqa: E731
    neg = lambda k: [-v for v in tot(k)]  # noqa: E731
    rows = [("Revenue", tot("revenue"), None), ("Less: expenses", neg("expenses"), None),
            ("Net profit as booked", tot("profit_booked"), "total"),
            ("Less: vehicle LE-6892 depreciation write-off (D)", neg("vehicle"), None),
            ("Add: KHALID REJECTION balance written off (J)", tot("khalid"), None),
            ("Stores & spares true-up to store records (H)", tot("stores"), None),
            ("Net profit before tax", tot("pbt"), "total"),
            ("Less: tax accounts written off, net (F) - expense / (income)", neg("tax"), None),
            ("NET PROFIT AFTER TAX", tot("pat"), "total"),
            ("Less: partners' drawings, treated as an expense (B)", neg("drawings"), None),
            ("PROFIT AFTER DRAWINGS, SPLIT 50/50", tot("pad"), "grand"),
            (f"Share to {PARTNERS[0]} (50%)", [v / 2 for v in tot("pad")], None),
            (f"Share to {PARTNERS[1]} (50%)", [v / 2 for v in tot("pad")], None)]
    for label, vals, kind in rows:
        write_row(ap, r, None, label, vals, kind); r += 1
    r += 1
    run = {"a": 0.0, "h": 0.0, "d": 0.0}
    blocks = []
    for y in yrs:
        o = dict(run)
        run["a"] += y["cap_azam"] + y["pad"] / 2
        run["h"] += y["cap_hina"] + y["pad"] / 2
        run["d"] += y["zakat"]
        blocks.append((o, dict(run), y))
    for label, key, cap_key in ((f"Capital account - {PARTNERS[0]}", "a", "cap_azam"),
                                (f"Capital account - {PARTNERS[1]}", "h", "cap_hina")):
        write_row(ap, r, None, label, [None] * (len(yrs) + 1), "section"); r += 1
        write_row(ap, r, None, "Opening", [o[key] for o, c, y in blocks] + [None]); r += 1
        write_row(ap, r, None, "Capital introduced / (withdrawn) as booked", [y[cap_key] for o, c, y in blocks] + [sum(y[cap_key] for y in yrs)]); r += 1
        write_row(ap, r, None, "Share of profit after tax and drawings (50%)", [y["pad"] / 2 for o, c, y in blocks] + [sum(y["pad"] for y in yrs) / 2]); r += 1
        write_row(ap, r, None, "Closing at 30 June", [c[key] for o, c, y in blocks] + [None], "grand"); r += 2
    write_row(ap, r, None, "Zakat closed to capital (both partners together)", [None] * (len(yrs) + 1), "section"); r += 1
    write_row(ap, r, None, "Zakat & sadqa for the year (C)", [y["zakat"] for y in yrs] + [sum(y["zakat"] for y in yrs)]); r += 1
    write_row(ap, r, None, "Cumulative closed to capital", [c["d"] for o, c, y in blocks] + [None], "total"); r += 2
    write_row(ap, r, None, "TOTAL PARTNERS' CAPITAL AT 30 JUNE", [c["a"] + c["h"] - c["d"] for o, c, y in blocks] + [None], "grand")
    ap.column_dimensions["A"].width = 4
    ap.column_dimensions["B"].width = 56
    for i in range(len(yrs) + 1):
        ap.column_dimensions[get_column_letter(3 + i)].width = 14
    ap.freeze_panes = "C5"

    # ---- Closing entries journal
    je = wb.create_sheet("Closing entries", 3)
    je["A1"] = "Closing entries (journal) - applied in this workbook only, not in the ERP"
    je["A1"].font = Font(bold=True, size=14, color="1F3B57")
    header(je, 3, ["Date", "Ref", "Account", "Debit", "Credit", "Narration"])
    r = 4

    def line(dt, ref, acct, dr, cr, nar):
        nonlocal r
        if abs(dr) < 0.5 and abs(cr) < 0.5:
            return
        je.cell(row=r, column=1, value=dt.strftime("%d-%b-%Y") if dt else "")
        je.cell(row=r, column=2, value=ref)
        je.cell(row=r, column=3, value=acct)
        for col, v in ((4, dr), (5, cr)):
            if abs(v) >= 0.5:
                c = je.cell(row=r, column=col, value=round(v))
                c.number_format = NUM
        je.cell(row=r, column=6, value=nar)
        r += 1

    def dc(dt, ref, dr_acct, cr_acct, amt, nar):
        if amt < 0:
            dr_acct, cr_acct, amt = cr_acct, dr_acct, -amt
        line(dt, ref, dr_acct, amt, 0, nar)
        line(dt, ref, cr_acct, 0, amt, "")

    for y in yrs:
        d, fy = y["fye"], y["fy"]
        for h, lbl in (("300501", "ADVANCE INCOME TAX & SALES TAX RECEIVABLE"), ("200201", "SALES TAX PAYABLE"),
                       ("200202", "WITHHOLDING TAX PAYABLE")):
            dc(d, f"{fy}-F", "TAXES WRITTEN OFF (P&L)", lbl, y["tax_by_head"][h], f"{fy}: write off the year's movement on {lbl.lower()}")
        dc(d, f"{fy}-F", "TAXES WRITTEN OFF (P&L)", "SALES TAX RECEIVABLE (missing SRN lines, K)", y["srn"],
           f"{fy}: write off the sales-tax lines added for SRN vouchers")
        dc(d, f"{fy}-D", "PROFIT & LOSS ON SALE OF FIXED ASSET", "CAPITAL RESERVES (ACCUMULATED DEPRECIATION)", y["vehicle"],
           "Vehicle LE-6892 sold 04-Mar-2019: write off depreciation never charged, against the sale profit")
        dc(d, f"{fy}-H", "STORES & SPARES", "STORES & SPARES CONSUMED", y["stores"],
           f"{fy}: bring stores & spares to the store's issue records")
        dc(d, f"{fy}-J", "KHALID REJECTION", "MISC. INCOME", y["khalid"], f"{fy}: write off KHALID REJECTION balance")
        dc(d, f"{fy}-B", "PROFIT & LOSS ACCOUNT (drawings as expense)", "PARTNERS' DRAWING", y["drawings"],
           f"{fy}: partners' drawings treated as an expense, after tax")
        dc(d, f"{fy}-A", "PROFIT & LOSS ACCOUNT", f"CAPITAL - {PARTNERS[0]}", y["pad"] / 2, f"{fy}: 50% of profit after tax and drawings")
        dc(d, f"{fy}-A", "PROFIT & LOSS ACCOUNT", f"CAPITAL - {PARTNERS[1]}", y["pad"] / 2, f"{fy}: 50% of profit after tax and drawings")
        dc(d, f"{fy}-C", "PARTNERS' CAPITAL", "ZAKAT & SADQA", y["zakat"], f"{fy}: zakat & sadqa closed to capital")
    r += 1
    je.cell(row=r, column=1, value="Before closing each year, all income and expense accounts are closed into the "
            "PROFIT & LOSS ACCOUNT (the P&L tab shows each account's figure).").font = NOTE
    r += 2
    je.cell(row=r, column=1, value="K - sales-tax lines missing from store receipt vouchers (added so the books balance)").font = TOT_FONT
    r += 1
    for vd, vno, gap in srn:
        dc(vd, f"SRN {vno}", "SALES TAX RECEIVEABLE", "(line missing on voucher)", -gap, "Input sales tax the voucher left out")
    r += 1
    je.cell(row=r, column=1, value="G - sales tax receivable and payable are shown as one net figure on every balance "
            "sheet date (presentation, no entry needed).").font = NOTE
    for col, wdt in zip("ABCDEF", (13, 11, 46, 15, 15, 70)):
        je.column_dimensions[col].width = wdt
    je.freeze_panes = "A4"
    return diff


def load_srn(cur, rows):
    """Store receipt vouchers whose lines don't balance (missing sales-tax line)."""
    return [(vd, vno, float(dif)) for vno, vd, dif in rows(cur, """
        SELECT vno, vd, SUM(amt) FROM dostex.party_ledger_v
        WHERE tbl='DMN' AND vt='SRN' GROUP BY vno, vd HAVING ABS(SUM(amt)) > 1 ORDER BY vd""")]







