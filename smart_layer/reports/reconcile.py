"""
Store consumption reconciliation (generalised from Dost Textiles' 45_ and 67_ scripts).

Each month, the store consumption the accounts charged in the ledger is compared with what the store
actually issued (issue slips, at the store's value). A big difference means a journal was missed, posted
twice, or the store's issues are wrong.

Config [reconcile]:
  store_consumption_accounts = ["5103"]   account codes the accountant charges store consumption to
                                          (empty: expense accounts whose name or head mentions "store")
  difference_limit = 50000                months differing by more than this are flagged
"""

from collections import defaultdict
from datetime import date

from . import Report


def store_consumption(con, cfg, as_of=None, months=12):
    as_of = as_of or date.today()
    r = cfg["reconcile"]
    accounts = [str(a) for a in r.get("store_consumption_accounts", [])]
    if not accounts:
        accounts = [a for (a,) in con.execute("""SELECT account_code FROM accounts WHERE account_group = 'expense'
                     AND (LOWER(account_name) LIKE '%store%' OR LOWER(COALESCE(head_name, '')) LIKE '%store%')""")]
    limit = float(r.get("difference_limit", 50000))
    y, m = divmod(as_of.year * 12 + as_of.month - months, 12)
    since = f"{y:04d}-{m + 1:02d}-01"
    ledger = defaultdict(float)
    if accounts:
        marks = ",".join("?" * len(accounts))
        for month, amt in con.execute(f"""SELECT strftime('%Y-%m', voucher_date), SUM(amount) FROM ledger_entries
                WHERE account_code IN ({marks}) AND voucher_kind <> 'opening' AND voucher_date >= ? AND voucher_date <= ?
                GROUP BY 1""", (*accounts, since, as_of.isoformat())):
            ledger[month] = amt or 0
    issued = dict(con.execute("""SELECT strftime('%Y-%m', doc_date), SUM(amount) FROM store_issues
                                 WHERE doc_date >= ? AND doc_date <= ? GROUP BY 1""", (since, as_of.isoformat())))
    rows, flagged = [], 0
    for month in sorted(set(ledger) | set(issued), reverse=True):
        a, b = ledger.get(month, 0.0), issued.get(month, 0.0) or 0.0
        diff = a - b
        note = ""
        if abs(diff) > limit:
            flagged += 1
            note = ("journal lower than the store issued - a consumption journal may be missing" if diff < 0
                    else "journal higher than the store issued - posted twice, or issues missing in the store")
        rows.append([month, round(a), round(b), round(diff), note])
    cur = cfg["company"]["currency"]
    return Report(f"Store consumption: accounts vs store - to {as_of:%b-%Y}",
                  ["Month", f"Charged in the ledger {cur}", f"Issued by the store {cur}", f"Difference {cur}", "Check"], rows,
                  [f"Ledger accounts compared: {', '.join(accounts) or 'none found - set [reconcile] store_consumption_accounts'}.",
                   f"{flagged} month(s) differ by more than {cur} {limit:,.0f}."])
