"""
Finance reports (generalised from Dost Textiles' 40_business_dashboard and 41_pnl_balance_sheet):

  pnl           revenue and expenses by account head, month by month, with profit
  balances      what the mill holds and owes now: cash, bank, receivables, payables, assets, liabilities
  parties       balance of every customer and supplier, with the date of its last entry

Heads come from accounts.head_name (the ERP's grouping); without it each account is its own head.
Opening-balance vouchers are left out of the P&L.
"""

from collections import defaultdict
from datetime import date, timedelta

from . import Report


def _months(as_of, n):
    out = []
    for i in range(n - 1, -1, -1):
        y, m = divmod(as_of.year * 12 + as_of.month - 1 - i, 12)
        out.append(f"{y:04d}-{m + 1:02d}")
    return out


def pnl(con, cfg, as_of=None, months=12):
    as_of = as_of or date.today()
    ms = _months(as_of, months)
    taxset = _tax_account_set(cfg)                        # tax heads are shown as their own Taxes group (item AM), not here
    data = defaultdict(lambda: defaultdict(float))       # (section, head) -> month -> amount
    for month, acode, group, head, amt in con.execute("""
            SELECT strftime('%Y-%m', l.voucher_date), a.account_code, a.account_group,
                   COALESCE(a.head_name, a.account_name, l.account_code), SUM(l.amount)
            FROM ledger_entries l JOIN accounts a USING (account_code)
            WHERE a.account_group IN ('revenue', 'expense') AND l.voucher_kind <> 'opening'
              AND l.voucher_date >= ? AND l.voucher_date <= ?
            GROUP BY 1, 2""", (ms[0] + "-01", as_of.isoformat())):
        if acode in taxset:
            continue
        sign = -1 if group == "revenue" else 1
        data[(group, head)][month] += sign * (amt or 0)
    # item AM: the three tax costs (net sales tax, further tax, withholding) per month, from the same [cost] accounts,
    # so the P&L and the cost per metre agree on what tax the mill bears.
    taxrows = {"Net sales tax (output - input)": {}, "Further tax on sales": {}, "Withholding tax": {}}
    taxmonth = {}
    for m in ms:
        t = _tax_totals(con, cfg, m + "-01", _month_end(m, as_of))
        taxrows["Net sales tax (output - input)"][m] = t["net"] or 0
        taxrows["Further tax on sales"][m] = t["further"] or 0
        taxrows["Withholding tax"][m] = t["withholding"] or 0
        taxmonth[m] = t["total"] or 0
    taxed = any(abs(taxmonth[m]) > 0 for m in ms)
    while len(ms) > 1 and not any(v.get(ms[0]) for v in data.values()) and abs(taxmonth.get(ms[0], 0)) < 1:
        ms.pop(0)                                          # months before the ledger starts
    rows = []
    totals = {"revenue": defaultdict(float), "expense": defaultdict(float)}
    for section, label in (("revenue", "Revenue"), ("expense", "Expenses")):
        heads = sorted((h for (g, h) in data if g == section), key=lambda h: -sum(data[(section, h)].values()))
        for h in heads:
            vals = [data[(section, h)].get(m, 0.0) for m in ms]
            for m, v in zip(ms, vals):
                totals[section][m] += v
            rows.append([label, h] + [round(v) for v in vals] + [round(sum(vals))])
        rows.append([label, f"TOTAL {label.upper()}"] + [round(totals[section][m]) for m in ms]
                    + [round(sum(totals[section].values()))])
    if taxed:                                              # the Taxes group (counted as an expense towards the profit)
        for head, by in taxrows.items():
            vals = [by.get(m, 0.0) for m in ms]
            rows.append(["Taxes", head] + [round(v) for v in vals] + [round(sum(vals))])
        rows.append(["Taxes", "TOTAL TAXES"] + [round(taxmonth[m]) for m in ms] + [round(sum(taxmonth[m] for m in ms))])
    profit = [totals["revenue"][m] - totals["expense"][m] - taxmonth.get(m, 0) for m in ms]
    rows.append(["Result", "PROFIT / (LOSS)"] + [round(v) for v in profit] + [round(sum(profit))])
    cur = cfg["company"]["currency"]
    note = "From the ledger; opening balances excluded. Expenses include a Taxes group (net sales tax + further tax + " \
           "withholding), the same figures the cost per metre uses." if taxed else \
           "From the ledger; opening balances excluded. Year-end adjustments the accountant has not posted are not included."
    return Report(f"Profit and loss - {ms[0]} to {ms[-1]}", ["", "Head"] + ms + ["Total"], rows,
                  [f"Profit for the period: {cur} {sum(profit):,.0f} on revenue of {cur} {sum(totals['revenue'].values()):,.0f}.",
                   note])


GROUPS = [("cash", "Cash in hand", 1), ("bank", "Bank", 1), ("customer", "Receivable from customers", 1),
          ("asset", "Other assets (stock, fixed assets ...)", 1), ("supplier", "Payable to suppliers", -1),
          ("liability", "Other liabilities", -1), ("equity", "Capital and reserves", -1)]


def balances(con, cfg, as_of=None):
    as_of = as_of or date.today()
    bal = dict(con.execute("""SELECT a.account_group, SUM(b.balance) FROM account_balances b JOIN accounts a USING (account_code)
                              GROUP BY a.account_group"""))
    pl = -(bal.get("revenue", 0) or 0) - (bal.get("expense", 0) or 0)
    rows = [[label, round(sign * (bal.get(g) or 0))] for g, label, sign in GROUPS]
    rows.append(["Profit / (loss) not yet closed to capital", round(pl)])
    other = bal.get("other") or 0
    if abs(other) >= 1:
        rows.append(["Accounts without a group (check the mapping)", round(other)])
    assets = sum((bal.get(g) or 0) for g in ("cash", "bank", "customer", "asset"))
    claims = -sum((bal.get(g) or 0) for g in ("supplier", "liability", "equity")) + pl
    cur = cfg["company"]["currency"]
    return Report(f"Balance summary - {as_of:%d-%b-%Y}", ["Item", f"Amount {cur}"], rows,
                  [f"Assets {cur} {assets:,.0f} against liabilities, capital and profit {cur} {claims:,.0f} "
                   f"(difference {cur} {assets - claims:,.0f} - should be 0 when the trial balance agrees)."])


def parties(con, cfg, as_of=None):
    as_of = as_of or date.today()
    rows = []
    for name, group, bal, last in con.execute("""
            SELECT COALESCE(p.party_name, a.account_name), a.account_group, b.balance,
                   (SELECT MAX(voucher_date) FROM ledger_entries l WHERE l.account_code = a.account_code)
            FROM accounts a JOIN account_balances b USING (account_code) LEFT JOIN parties p ON p.party_code = a.party_code
            WHERE a.account_group IN ('customer', 'supplier') AND ABS(b.balance) >= 1"""):
        days = (as_of - date.fromisoformat(last)).days if last else None
        kind = "Receivable" if bal > 0 else "Payable"
        rows.append([kind, name, group, round(abs(bal)), last, days])
    rows.sort(key=lambda r: (r[0], -r[3]))
    cur = cfg["company"]["currency"]
    rec = sum(r[3] for r in rows if r[0] == "Receivable")
    pay = sum(r[3] for r in rows if r[0] == "Payable")
    return Report(f"Party balances - {as_of:%d-%b-%Y}", ["", "Party", "Type", f"Balance {cur}", "Last entry", "Days since"], rows,
                  [f"Receivable {cur} {rec:,.0f} from {sum(1 for r in rows if r[0] == 'Receivable')} parties; "
                   f"payable {cur} {pay:,.0f} to {sum(1 for r in rows if r[0] == 'Payable')}."])


COST_KEYWORDS = {"sizing": "sizing", "size ": "sizing", "salary": "payroll", "wage": "payroll",
                 "payroll": "payroll", "labour": "payroll", "power": "power", "electric": "power",
                 "diesel": "power", "fuel": "power", "gas": "power", "store": "store", "spare": "store",
                 "packing": "store"}
COST_BUCKETS = ("store", "payroll", "power", "other", "sizing")


def _bucket_of(head, name, cfg):
    """Which cost bucket a ledger expense head falls in. [cost] heads in config maps a substring -> bucket and
    wins; otherwise sensible keyword defaults; otherwise 'other'. Sizing is kept separate (the 'without/with
    sizing' cost per metre)."""
    text = f"{head or ''} {name or ''}".lower()
    for patt, bucket in (cfg["cost"].get("heads") or {}).items():
        if str(patt).lower() in text:
            return bucket
    for kw, bucket in COST_KEYWORDS.items():
        if kw in text:
            return bucket
    return "other"


def _month_closed(buckets, cfg):
    """Is a month's month-end posted? True when every bucket in [cost] closed_requires is > 0 (default store +
    payroll - the store month-end journal and salaries). A month with woven metres but these not posted yet is
    'not closed' and is kept out of the headline and the averages, so a part-posted month never misleads."""
    need = cfg["cost"].get("closed_requires") or ["store", "payroll"]
    return all((buckets.get(b, 0) or 0) > 0 for b in need)


def _cost_months(con, cfg, as_of, months):
    """Per-month cost data: [{month, woven, buckets{...}, no_sizing_per, sizing_per, with_sizing_per, closed}],
    oldest first, trimmed to where the data starts. Shared by the report and the headline."""
    ms = _months(as_of, months)
    woven = dict(con.execute("SELECT strftime('%Y-%m', prod_date), SUM(meters) FROM loom_production WHERE prod_date >= ? AND prod_date <= ? GROUP BY 1",
                             (ms[0] + "-01", as_of.isoformat())))
    taxset = _tax_account_set(cfg)                        # tax accounts are added as an explicit Taxes line, not a bucket
    bybucket = {m: {b: 0.0 for b in COST_BUCKETS} for m in ms}
    for month, acode, head, name, amt in con.execute("""
            SELECT strftime('%Y-%m', l.voucher_date), a.account_code, a.head_name, a.account_name, SUM(l.amount)
            FROM ledger_entries l JOIN accounts a USING (account_code)
            WHERE a.account_group = 'expense' AND l.voucher_kind <> 'opening' AND l.voucher_date >= ? AND l.voucher_date <= ?
            GROUP BY 1, a.account_code""", (ms[0] + "-01", as_of.isoformat())):
        if month in bybucket and acode not in taxset:
            bybucket[month][_bucket_of(head, name, cfg)] += (amt or 0)
    while len(ms) > 1 and not (woven.get(ms[0]) or any(bybucket[ms[0]].values())):
        ms.pop(0)
    running = as_of.strftime("%Y-%m")                     # the current month is never "closed" - it is not over yet
    # a month with production far below the normal run is incomplete (a truncated / part-keyed month), so it is not
    # a representative closed month even if its heads are posted - guard against a misleading cost/m from it.
    wv = sorted(w for w in woven.values() if (w or 0) > 0)
    median_w = wv[len(wv) // 2] if wv else 0
    out = []
    for m in ms:
        w = woven.get(m, 0) or 0
        b = bybucket[m]
        ns = b["store"] + b["payroll"] + b["power"] + b["other"]
        per = (lambda v: round(v / w, 2) if (w and v is not None) else None)
        full_month = w >= 0.2 * median_w if median_w else (w > 0)
        tx = _tax_totals(con, cfg, m + "-01", _month_end(m, as_of))   # item AM: this month's own tax figures
        tax_total = tx["total"]
        out.append({"month": m, "woven": round(w), "buckets": b, "tax": tx,
                    "store": per(b["store"]), "payroll": per(b["payroll"]), "power": per(b["power"]),
                    "other": per(b["other"]), "no_sizing": per(ns), "sizing": per(b["sizing"]),
                    "with_sizing": per(ns + b["sizing"]),
                    "taxes_total": tax_total, "taxes_per": per(tax_total),
                    "with_taxes": per(ns + b["sizing"] + (tax_total or 0)),
                    "closed": (w > 0 and m < running and full_month and _month_closed(b, cfg))})
    return out


def _tax_amount(con, accounts, side, vtypes, lo, hi):
    """Sum a set of GL accounts' month movement on one side (item AM). Debits are amount > 0, credits amount < 0
    (the store's sign convention). `vtypes` restricts to those voucher types (empty = all). Returns None when no
    accounts are configured, so the UI can say 'set the accounts' instead of showing a misleading zero."""
    accounts = [str(a) for a in (accounts or []) if str(a).strip()]
    if not accounts:
        return None
    where = "account_code IN (%s) AND voucher_date >= ? AND voucher_date <= ? AND voucher_kind <> 'opening'" % ",".join("?" * len(accounts))
    params = list(accounts) + [lo, hi]
    vtypes = [str(v) for v in (vtypes or []) if str(v).strip()]
    if vtypes:
        where += " AND voucher_type IN (%s)" % ",".join("?" * len(vtypes))
        params += vtypes
    expr = "SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END)" if side == "cr" else "SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)"
    row = con.execute("SELECT %s FROM ledger_entries WHERE %s" % (expr, where), params).fetchone()
    return round((row[0] or 0), 2)


def _tax_totals(con, cfg, lo, hi):
    """Item AM: a month's tax cost from the GL accounts named in [cost] (all read-only): the net sales-tax cost
    (output tax on sales minus input tax on purchases), further tax on sales, and withholding tax (income tax
    deducted by customers). `total` is the sum of the three and is what the cost per metre and the P&L both add."""
    c = cfg["cost"]
    out = _tax_amount(con, c.get("output_tax_accounts"), c.get("output_tax_side", "cr"), c.get("output_tax_vtypes"), lo, hi)
    inp = _tax_amount(con, c.get("input_tax_accounts"), c.get("input_tax_side", "dr"), c.get("input_tax_vtypes"), lo, hi)
    fur = _tax_amount(con, c.get("further_tax_accounts"), c.get("further_tax_side", "dr"), c.get("further_tax_vtypes"), lo, hi)
    wht = _tax_amount(con, c.get("withholding_tax_accounts"), c.get("withholding_tax_side", "dr"), c.get("withholding_tax_vtypes"), lo, hi)
    net = (out - inp) if (out is not None and inp is not None) else (out if inp is None else (-inp if out is None else None))
    parts = [v for v in (net, fur, wht) if v is not None]
    total = round(sum(parts), 2) if parts else None
    return {"output": out, "input": inp, "further": fur, "withholding": wht, "net": net, "total": total,
            "configured": any(v is not None for v in (out, inp, fur, wht)),
            "further_booked": (fur is not None and fur > 0),
            "withholding_booked": (wht is not None and wht > 0)}


def _tax_account_set(cfg):
    """Every GL account code named in [cost] as a tax account. These are EXCLUDED from the generic expense buckets
    (cost per metre) and the generic P&L grouping, then added back as an explicit Taxes line, so nothing is double
    counted (further tax, for example, is an expense-group account that would otherwise fall in 'other')."""
    c = cfg["cost"]
    out = set()
    for k in ("output_tax_accounts", "input_tax_accounts", "further_tax_accounts", "withholding_tax_accounts"):
        for a in (c.get(k) or []):
            if str(a).strip():
                out.add(str(a).strip())
    return out


def _month_end(month, as_of):
    """Last day of a 'YYYY-MM' string, capped at as_of (the current month ends today, not in the future)."""
    import calendar
    y, m = int(month[:4]), int(month[5:7])
    return min(date(y, m, calendar.monthrange(y, m)[1]), as_of).isoformat()


def cost_headline(con, cfg, as_of=None, months=12):
    """The cost-per-metre headline for the Overview tile / cost report: the LAST CLOSED month (month-end posted)
    with its cost/m without and with sizing and the per-head breakdown, plus the average, lowest and highest over
    the last 12 CLOSED months. Months not yet closed are excluded. Returns {} when no month is closed."""
    data = _cost_months(con, cfg, as_of or date.today(), months)
    closed = [d for d in data if d["closed"]]
    if not closed:
        return {"closed": False, "latest_open": (data[-1]["month"] if data else None)}
    last = closed[-1]
    ns = [d["no_sizing"] for d in closed if d["no_sizing"] is not None]
    ws = [d["with_sizing"] for d in closed if d["with_sizing"] is not None]
    lo = min(closed, key=lambda d: d["no_sizing"] if d["no_sizing"] is not None else 9e9)
    hi = max(closed, key=lambda d: d["no_sizing"] if d["no_sizing"] is not None else -9e9)
    # item AM: TOTAL Rs beside the per-metre figures, plus a taxes block and a grand total incl sizing and taxes.
    w = last["woven"] or 0                                 # metres woven in the headline (last closed) month
    b = last["buckets"]
    ns_total = round(b["store"] + b["payroll"] + b["power"] + b["other"], 2)
    sizing_total = round(b["sizing"], 2)
    taxes = last["tax"]                                     # this month's own tax figures (net + further + withholding)
    tax_total = taxes["total"] or 0
    grand_total = round(ns_total + sizing_total + tax_total, 2)
    per = (lambda v: round(v / w, 2) if (w and v is not None) else None)
    return {"closed": True, "month": last["month"], "no_sizing": last["no_sizing"], "with_sizing": last["with_sizing"],
            "heads": {"store": last["store"], "payroll": last["payroll"], "power": last["power"],
                      "other": last["other"], "sizing": last["sizing"]},
            "woven_m": round(w),
            "totals": {"store": round(b["store"], 2), "payroll": round(b["payroll"], 2), "power": round(b["power"], 2),
                       "other": round(b["other"], 2), "no_sizing": ns_total, "sizing": sizing_total,
                       "taxes": taxes["total"], "grand": grand_total},
            "taxes": {**taxes, "further_per": per(taxes["further"]), "net_per": per(taxes["net"]),
                      "withholding_per": per(taxes["withholding"]), "total_per": per(taxes["total"])},
            "grand_per_m": per(grand_total), "with_taxes_per_m": per(grand_total),
            "closed_months": len(closed),
            "avg_no_sizing": round(sum(ns) / len(ns), 2) if ns else None,
            "avg_with_sizing": round(sum(ws) / len(ws), 2) if ws else None,
            "low": {"month": lo["month"], "no_sizing": lo["no_sizing"]},
            "high": {"month": hi["month"], "no_sizing": hi["no_sizing"]},
            "currency": cfg["company"]["currency"]}


def cost_per_metre(con, cfg, as_of=None, months=12):
    """Monthly cost per metre woven: each expense head per metre, the total WITHOUT sizing and WITH sizing, with a
    'Closed?' column. Expense ledger heads are grouped into store/payroll/power/other/sizing ([cost].heads). The
    headline (last CLOSED month) and the 12-closed-month average/low/high lead the summary; months whose month-end
    entries are not posted yet are marked 'not closed' and excluded from the headline and the averages."""
    as_of = as_of or date.today()
    cur = cfg["company"]["currency"]
    data = _cost_months(con, cfg, as_of, months)
    rows = [[d["month"], d["woven"], d["store"], d["payroll"], d["power"], d["other"],
             d["no_sizing"], d["sizing"], d["with_sizing"], d["taxes_per"], d["with_taxes"],
             ("yes" if d["closed"] else "not closed")]
            for d in data]
    h = cost_headline(con, cfg, as_of, months)
    summary = []
    if h.get("closed"):
        summary.append(f"{h['month']} (last closed month): {cur} {h['no_sizing']:.2f}/m without sizing, "
                       f"{h['with_sizing']:.2f}/m with sizing.")
        if h.get("avg_no_sizing") is not None:
            summary.append(f"Average over {h['closed_months']} closed month(s): {cur} {h['avg_no_sizing']:.2f}/m "
                           f"(with sizing {h['avg_with_sizing']:.2f}); lowest {h['low']['no_sizing']:.2f} ({h['low']['month']}), "
                           f"highest {h['high']['no_sizing']:.2f} ({h['high']['month']}).")
    elif h.get("latest_open"):
        summary.append(f"No closed month yet (latest {h['latest_open']} has month-end entries pending).")
    summary.append(f"In {cur}/m. 'No sizing' = store + payroll + power + other; 'with sizing' adds sizing; 'incl. "
                   "taxes' adds the net sales tax, further tax and withholding tax for that month. A month is "
                   "'closed' once its month-end journals (store, salaries, power) are posted; not-closed months are "
                   "excluded from the headline and averages. Group the ledger heads in [cost].heads for accuracy.")
    first, last = (data[0]["month"] if data else ""), (data[-1]["month"] if data else "")
    return Report(f"Cost per metre - {first} to {last}",
                  ["Month", "Woven m", "Store/m", "Payroll/m", "Power/m", "Other/m", "Cost/m (no sizing)",
                   "Sizing/m", "Cost/m (with sizing)", "Taxes/m", "Cost/m incl. taxes", "Closed?"], rows, summary)


def _tolerance(cfg):
    return float((cfg["audit"] or {}).get("trial_balance_tolerance", 10) or 0)


def _unbalanced_vouchers(con, where, params):
    """Vouchers whose ledger lines do not net to zero (debits != credits) - a data-entry error. Returns
    [date, voucher_type, voucher_no, lines, debit, credit, difference], biggest out-of-balance first."""
    rows = con.execute(f"""
        SELECT voucher_date, MAX(voucher_type), MAX(voucher_no), COUNT(*),
               SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END),
               SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), SUM(amount)
        FROM ledger_entries WHERE {where} GROUP BY voucher_id
        HAVING ABS(SUM(amount)) > ? AND ABS(SUM(amount)) >= 0.5
        ORDER BY ABS(SUM(amount)) DESC, voucher_date DESC""", params).fetchall()
    return [[vd, vt, vno, n, round(dr or 0), round(cr or 0), round(diff or 0)] for vd, vt, vno, n, dr, cr, diff in rows]


def trial_balance(con, cfg, as_of=None):
    """Trial-balance check as of a date: total debits vs total credits, the difference, and - the part that matters -
    the unbalanced vouchers that break it. A healthy ledger balances to zero and lists no vouchers."""
    as_of = as_of or date.today()
    d, cur, tol = as_of.isoformat(), cfg["company"]["currency"], _tolerance(cfg)
    debit = credit = 0.0
    for (bal,) in con.execute("SELECT SUM(amount) FROM ledger_entries WHERE voucher_date <= ? GROUP BY account_code", (d,)):
        bal = bal or 0
        if bal > 0:
            debit += bal
        else:
            credit += -bal
    diff = debit - credit                                # = sum of every unbalanced voucher's net amount
    rows = _unbalanced_vouchers(con, "voucher_date <= ?", (d, tol))
    verdict = "BALANCED" if abs(diff) <= tol else f"OUT OF BALANCE by {cur} {abs(diff):,.0f}"
    summary = [f"As at {as_of:%d-%b-%Y}: total debits {cur} {debit:,.0f}, total credits {cur} {credit:,.0f}.",
               f"Trial balance: {verdict} (tolerance {cur} {tol:g})."]
    summary.append(f"{len(rows)} unbalanced voucher(s) shown below - these are what make the books not tie out."
                   if rows else "No unbalanced vouchers: every voucher's debits equal its credits.")
    # vouchers out of balance but within tolerance are not listed; note them so the rows reconcile to the difference
    un, ut = con.execute("""SELECT COUNT(*), COALESCE(SUM(ABS(s)), 0) FROM
                            (SELECT SUM(amount) s FROM ledger_entries WHERE voucher_date <= ?
                             GROUP BY voucher_id HAVING ABS(SUM(amount)) >= 0.5 AND ABS(SUM(amount)) <= ?)""", (d, tol)).fetchone()
    if un:
        summary.append(f"(+ {un} voucher(s) under the {cur} {tol:g} tolerance totalling {cur} {ut:,.0f}, not listed.)")
    return Report(f"Trial balance check - {as_of:%d-%b-%Y}",
                  ["Date", "Type", "Voucher", "Lines", f"Debit {cur}", f"Credit {cur}", f"Difference {cur}"], rows, summary)


def unbalanced(con, cfg, d0=None, d1=None):
    """Every voucher in the period whose debits do not equal its credits (detection report)."""
    d1 = d1 or date.today()
    d0 = d0 or (d1 - timedelta(days=365))
    cur, tol = cfg["company"]["currency"], _tolerance(cfg)
    rows = _unbalanced_vouchers(con, "voucher_date BETWEEN ? AND ?", (d0.isoformat(), d1.isoformat(), tol))
    net = sum(abs(r[6]) for r in rows)                   # total size of the imbalance (always positive)
    summary = [f"{d0:%d-%b-%Y} to {d1:%d-%b-%Y}: {len(rows)} voucher(s) where debits do not equal credits"
               + (f", out of balance by {cur} {net:,.0f} in total." if rows else " - the books are clean for this period.")]
    if rows:
        summary.append("Each line is a data-entry error to fix in the ERP: the voucher's debit and credit sides differ.")
    return Report(f"Unbalanced entries - {d0:%d-%b-%Y} to {d1:%d-%b-%Y}",
                  ["Date", "Type", "Voucher", "Lines", f"Debit {cur}", f"Credit {cur}", f"Difference {cur}"], rows, summary)
