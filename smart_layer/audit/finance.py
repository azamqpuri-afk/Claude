"""Finance checks on the general ledger."""

from datetime import date


def run(a):
    d0, d1 = a.window
    t, cur = a.t, a.cur
    in_window = "(voucher_date BETWEEN ? AND ? OR date(entered_at) BETWEEN ? AND ?)"

    # vouchers whose debits and credits differ
    for vid, vt, vno, vd, gap in a.q(f"""SELECT voucher_id, voucher_type, voucher_no, voucher_date, SUM(amount)
            FROM ledger_entries WHERE {in_window} GROUP BY voucher_id HAVING ABS(SUM(amount)) > 1""", d0, d1, d0, d1):
        a.add("CONFIRMED", "Finance", f"unbal-{vid}", f"{vt} {vno} of {vd} does not balance by {cur} {abs(gap):,.0f}",
              "debits and credits differ - a line or account is missing")

    # dated in the future / keyed long after their date
    lag95 = a.profile.get("voucher_lag_p95", {})
    for vid, vt, vno, vd, keyed, user, amt in a.q("""SELECT voucher_id, voucher_type, voucher_no, voucher_date,
                MAX(date(entered_at)), MAX(entered_by), SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)
            FROM ledger_entries WHERE date(entered_at) BETWEEN ? AND ? GROUP BY voucher_id""", d0, d1):
        gap = (date.fromisoformat(keyed) - date.fromisoformat(vd)).days
        if gap < -1:
            a.add("CONFIRMED", "Finance", f"future-{vid}", f"{vt} {vno} dated {vd} but keyed on {keyed} (future date)",
                  f"user {user or '?'}")
            continue
        usual = lag95.get(vt) or 0
        if gap > max(int(t["backdate_min_days"]), usual + 3):
            a.add("ACTION" if gap > max(45, 3 * usual) else "WATCH", "Finance", f"backdate-{vid}",
                  f"{vt} {vno} keyed {gap} days after its date ({vd} -> {keyed}), {cur} {amt:,.0f}",
                  f"user {user or '?'}; this voucher type is normally keyed within {usual:.0f} days")

    # the same payment twice: same account, same amount, within 3 days, different vouchers
    for acc, name, amt, n, vouchers in a.q("""
            SELECT l.account_code, MAX(ac.account_name), l.amount, COUNT(DISTINCT l.voucher_id),
                   GROUP_CONCAT(l.voucher_type || ' ' || l.voucher_no || ' ' || l.voucher_date, ', ')
            FROM ledger_entries l LEFT JOIN accounts ac ON ac.account_code = l.account_code
            WHERE l.voucher_kind = 'payment' AND COALESCE(ac.account_group, '') NOT IN ('cash', 'bank')
              AND l.amount >= ? AND l.voucher_date BETWEEN date(?, '-3 days') AND ?
            GROUP BY l.account_code, l.amount HAVING COUNT(DISTINCT l.voucher_id) > 1
               AND MAX(l.voucher_date) >= ? AND julianday(MAX(l.voucher_date)) - julianday(MIN(l.voucher_date)) <= 3""",
            float(t["duplicate_payment_min"]), d0, d1, d0):
        a.add("ACTION", "Finance", f"dup-{acc}-{amt}-{vouchers}", f"Possible double payment: {name or acc} {cur} {amt:,.0f} paid {n} times",
              vouchers)

    # trial balance and cash
    tb = a.q("SELECT SUM(balance) FROM account_balances")[0][0]
    if tb is not None and abs(tb) > float(t.get("trial_balance_tolerance", 10)):
        a.add("CONFIRMED", "Finance", f"tb-{round(tb)}", f"Trial balance is out by {cur} {tb:,.0f}",
              "total debits differ from total credits - see the unbalanced vouchers")
    for acc, name, bal in a.q("""SELECT b.account_code, ac.account_name, b.balance FROM account_balances b
            JOIN accounts ac ON ac.account_code = b.account_code WHERE ac.account_group = 'cash' AND b.balance < -1"""):
        a.add("CONFIRMED", "Finance", f"cashneg-{acc}-{round(bal)}", f"Cash account {name} is negative: {cur} {bal:,.0f}",
              "a payment was keyed without its receipt, or a receipt is missing")

    # large payments - listed for the owner's eye
    for vid, vt, vno, vd, name, amt in a.q("""SELECT l.voucher_id, l.voucher_type, l.voucher_no, l.voucher_date, ac.account_name, l.amount
            FROM ledger_entries l LEFT JOIN accounts ac ON ac.account_code = l.account_code
            WHERE l.voucher_kind = 'payment' AND COALESCE(ac.account_group, '') NOT IN ('cash', 'bank')
              AND l.amount >= ? AND l.voucher_date BETWEEN ? AND ?""", float(t["large_payment"]), d0, d1):
        a.add("WATCH", "Finance", f"big-{vid}-{name}", f"Large payment {vt} {vno}: {name} {cur} {amt:,.0f}", vd)

    n, total = a.q("""SELECT COUNT(DISTINCT voucher_id), SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)
                      FROM ledger_entries WHERE voucher_date BETWEEN ? AND ?""", d0, d1)[0]
    a.summary.append(f"Finance: {n} vouchers, {cur} {total or 0:,.0f} debits"
                     + (f"; trial balance difference {cur} {tb:,.0f}" if tb is not None else "") + ".")
