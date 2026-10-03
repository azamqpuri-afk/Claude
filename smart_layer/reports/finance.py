"""
Finance reports (generalised from Dost Textiles' 40_business_dashboard and 41_pnl_balance_sheet):

  pnl           revenue and expenses by account head, month by month, with profit
  balances      what the mill holds and owes now: cash, bank, receivables, payables, assets, liabilities
  parties       balance of every customer and supplier, with the date of its last entry

Heads come from accounts.head_name (the ERP's grouping); without it each account is its own head.
Opening-balance vouchers are left out of the P&L.
"""

from collections import defaultdict
from datetime import date

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
    data = defaultdict(lambda: defaultdict(float))       # (section, head) -> month -> amount
    for month, group, head, amt in con.execute("""
            SELECT strftime('%Y-%m', l.voucher_date), a.account_group, COALESCE(a.head_name, a.account_name, l.account_code),
                   SUM(l.amount)
            FROM ledger_entries l JOIN accounts a USING (account_code)
            WHERE a.account_group IN ('revenue', 'expense') AND l.voucher_kind <> 'opening'
              AND l.voucher_date >= ? AND l.voucher_date <= ?
            GROUP BY 1, 2, 3""", (ms[0] + "-01", as_of.isoformat())):
        sign = -1 if group == "revenue" else 1
        data[(group, head)][month] += sign * (amt or 0)
    while len(ms) > 1 and not any(v.get(ms[0]) for v in data.values()):
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
    profit = [totals["revenue"][m] - totals["expense"][m] for m in ms]
    rows.append(["Result", "PROFIT / (LOSS)"] + [round(v) for v in profit] + [round(sum(profit))])
    cur = cfg["company"]["currency"]
    return Report(f"Profit and loss - {ms[0]} to {ms[-1]}", ["", "Head"] + ms + ["Total"], rows,
                  [f"Profit for the period: {cur} {sum(profit):,.0f} on revenue of {cur} {sum(totals['revenue'].values()):,.0f}.",
                   "From the ledger; opening balances excluded. Year-end adjustments the accountant has not posted are not included."])


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
