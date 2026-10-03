"""Store checks: issues, receipts, stock and supplier prices."""

from datetime import date


def run(a):
    d0, d1 = a.window
    t, cur = a.t, a.cur
    in_window = "(doc_date BETWEEN ? AND ? OR date(entered_at) BETWEEN ? AND ?)"

    # issue lines with no quantity (demand written but nothing given?)
    habit = (a.profile.get("zero_qty_issues_per_month") or 0) >= 3
    for dno, dd, to, name, qty in a.q(f"""SELECT s.doc_no, s.doc_date, s.issued_to, COALESCE(i.item_name, s.item_code), s.qty
            FROM store_issues s LEFT JOIN items i ON i.item_code = s.item_code
            WHERE {in_window.replace('doc_date', 's.doc_date').replace('entered_at', 's.entered_at')} AND COALESCE(s.qty, 0) <= 0""",
            d0, d1, d0, d1):
        a.add("WATCH" if habit else "CONFIRMED", "Store", f"iss0-{dno}-{name}",
              f"Issue slip {dno} ({dd}) to {to or '?'}: {name} with quantity {qty or 0:g}",
              "a slip with no quantity - item probably not in stock; the machine may still be waiting")

    # negative stock
    for code, name, qty in a.q("""SELECT b.item_code, COALESCE(i.item_name, b.item_code), SUM(b.qty) FROM stock_balances b
            LEFT JOIN items i ON i.item_code = b.item_code GROUP BY b.item_code HAVING SUM(b.qty) < -0.001"""):
        a.add("CONFIRMED", "Store", f"neg-{code}-{qty:g}", f"{name}: stock is {qty:g} (negative)",
              "more issued than received - a receipt is missing or an issue is wrong; count it physically")

    # purchase rates: vs the last purchase, and vs other suppliers
    receipts = a.q(f"""SELECT r.doc_no, r.doc_date, r.item_code, COALESCE(i.item_name, r.item_code), r.supplier_name, r.rate, r.qty
            FROM store_receipts r LEFT JOIN items i ON i.item_code = r.item_code
            WHERE {in_window.replace('doc_date', 'r.doc_date').replace('entered_at', 'r.entered_at')} AND r.rate > 0""",
                   d0, d1, d0, d1)
    jump = float(t["rate_jump_ratio"])
    cheap, cheap_min = float(t["cheaper_elsewhere_ratio"]), float(t["cheaper_elsewhere_min_extra"])
    for dno, dd, code, name, sup, rate, qty in receipts:
        prev = a.q("""SELECT rate, doc_date, supplier_name FROM store_receipts WHERE item_code = ? AND doc_no <> ? AND rate > 0
                      AND doc_date BETWEEN date(?, '-18 months') AND ? ORDER BY doc_date DESC LIMIT 1""", code, dno, dd, dd)
        if prev:
            pr, pd, ps = prev[0]
            ratio = rate / pr
            if ratio > jump or ratio < 1 / jump:
                a.add("ACTION" if ratio > 2.5 else "WATCH", "Store", f"rate-{dno}-{code}",
                      f"GRN {dno} {name}: rate {cur} {rate:,.0f} vs last {cur} {pr:,.0f} ({ratio:.1f}x)",
                      f"{sup or '?'} now; last {ps or '?'} on {pd}")
        alt = a.q("""SELECT rate, doc_date, supplier_name FROM store_receipts WHERE item_code = ? AND doc_no <> ? AND rate > 0
                     AND COALESCE(supplier_name, '') <> COALESCE(?, '') AND doc_date BETWEEN date(?, '-12 months') AND ?
                     ORDER BY rate LIMIT 1""", code, dno, sup, dd, dd)
        if alt:
            ar, ad, asup = alt[0]
            extra = (rate - ar) * (qty or 0)
            if rate > cheap * ar and extra > cheap_min:
                a.add("ACTION" if rate > 1.5 * ar and extra > 5 * cheap_min else "WATCH", "Store", f"mkt-{dno}-{code}",
                      f"GRN {dno} {name}: {cur} {rate:,.0f} from {sup or '?'} - {asup} sold it for {cur} {ar:,.0f} on {ad}",
                      f"{cur} {extra:,.0f} more on {qty:g} - ask for the market rate or a second quote")

    # the same single part issued again to the same machine within a few days
    days = int(t["repeat_issue_days"])
    for dno, dd, to, name, amt, dno2, dd2 in a.q("""
            SELECT x.doc_no, x.doc_date, x.issued_to, COALESCE(i.item_name, x.item_code), x.amount, y.doc_no, y.doc_date
            FROM store_issues x JOIN store_issues y ON y.item_code = x.item_code AND y.issued_to = x.issued_to AND y.doc_no <> x.doc_no
                 AND y.doc_date BETWEEN date(x.doc_date, ?) AND x.doc_date AND (y.doc_date < x.doc_date OR y.doc_no < x.doc_no)
            LEFT JOIN items i ON i.item_code = x.item_code
            WHERE x.doc_date BETWEEN ? AND ? AND x.qty = 1 AND y.qty = 1 AND x.issued_to IS NOT NULL""", f"-{days} days", d0, d1):
        a.add("ACTION" if (amt or 0) >= 2000 else "WATCH", "Store", f"repeat-{dno}-{name}",
              f"{name} issued again to {to}: slip {dno2} ({dd2}) and slip {dno} ({dd})",
              f"{cur} {amt or 0:,.0f} each - was the first one really fitted? ask for the old part back")

    # the same supplier bill on two receipts
    reuse = set(a.profile.get("bill_reuse", []))
    for sup, sname, bill, g1, dd1, a1, g2, dd2, a2 in a.q("""
            WITH g AS (SELECT doc_no, MIN(doc_date) dd, MAX(entered_at) ea, supplier_code sc, MAX(supplier_name) sn,
                              supplier_bill_no bill, SUM(amount) amt
                       FROM store_receipts WHERE supplier_bill_no IS NOT NULL
                         AND UPPER(TRIM(supplier_bill_no)) NOT IN ('', '0', '-', 'NIL', 'NILL', 'NA', 'N/A')
                       GROUP BY doc_no, supplier_code, supplier_bill_no)
            SELECT y.sc, y.sn, y.bill, x.doc_no, x.dd, x.amt, y.doc_no, y.dd, y.amt
            FROM g x JOIN g y ON y.sc = x.sc AND y.bill = x.bill AND y.doc_no > x.doc_no
            WHERE (y.dd BETWEEN ? AND ? OR date(y.ea) BETWEEN ? AND ?) AND x.dd >= date(?, '-365 days')""",
            d0, d1, d0, d1, d0):
        same_amt = a1 is not None and a2 is not None and abs(a1 - a2) < 1
        close = abs((date.fromisoformat(dd2) - date.fromisoformat(dd1)).days) <= 3
        if same_amt or (close and sup not in reuse):
            a.add("ACTION", "Store", f"dupbill-{sup}-{bill}-{g2}",
                  f"{sname or sup} bill {bill} entered on GRN {g1} ({dd1}, {cur} {a1 or 0:,.0f}) and GRN {g2} ({dd2}, {cur} {a2 or 0:,.0f})",
                  "same bill twice - check it is not a double purchase or double payment")

    n_iss, v_iss = a.q("SELECT COUNT(DISTINCT doc_no), SUM(amount) FROM store_issues WHERE doc_date BETWEEN ? AND ?", d0, d1)[0]
    n_rec, v_rec = a.q("SELECT COUNT(DISTINCT doc_no), SUM(amount) FROM store_receipts WHERE doc_date BETWEEN ? AND ?", d0, d1)[0]
    a.summary.append(f"Store: {n_iss} issue slips ({cur} {v_iss or 0:,.0f}), {n_rec} receipts ({cur} {v_rec or 0:,.0f}).")
