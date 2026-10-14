"""
Finance for the owner dashboard: KPIs, the party list, one party's ledger and
the daily voucher summary - all read from the local store's canonical finance
entities (accounts, account_balances, ledger_entries), so it works for any ERP
once its mapping classifies accounts into account_group and marks the opening
(old-ERP) balance as voucher_kind 'opening'.

Balances are signed: debit positive, credit negative (ledger_entries.amount and
account_balances.balance). A customer with a positive balance owes the mill; a
negative balance is an advance received. The same sign rule classifies every
account, so one party's advance is never netted against another's debt.
"""

from datetime import date, datetime, timedelta

# canonical account_group -> (chip key, chip label) for the ledger desk's filter chips.
# Only groups that actually have accounts are shown.
CHIPS = [
    ("customer", "conversion", "Conversion"),
    ("supplier", "supplier", "Store & supplier"),
    ("liability", "accrued", "Accrued"),
    ("cash", "cash", "Cash"),
    ("bank", "bank", "Bank"),
]
CHIP_GROUP = {chip: grp for grp, chip, _ in CHIPS}
PARTY_GROUPS = ("customer", "supplier", "cash", "bank", "liability")


def _go_live(con):
    """Day before the first real (non-opening) entry: the switch-over from the old ERP."""
    row = con.execute("SELECT MIN(voucher_date) FROM ledger_entries WHERE voucher_kind <> 'opening'").fetchone()
    if row and row[0]:
        try:
            return (date.fromisoformat(str(row[0])[:10]) - timedelta(days=1)).isoformat()
        except ValueError:
            return str(row[0])[:10]
    return None


def kpis(con):
    """Headline finance numbers as of now, by account group and sign."""
    rows = con.execute("""
        SELECT a.account_group, COALESCE(b.balance, 0)
        FROM account_balances b JOIN accounts a USING (account_code)""").fetchall()
    g = {}
    for grp, bal in rows:
        g.setdefault(grp or "other", []).append(bal or 0)

    def s(grp, f=lambda b: True):
        return sum(b for b in g.get(grp, []) if f(b))

    pos = lambda b: b > 0
    neg = lambda b: b < 0
    return {
        "receivable_net": round(s("customer")),
        "receivable_gross": round(s("customer", pos)),
        "advances_from_parties": round(-s("customer", neg)),
        "owe_store_suppliers": round(-s("supplier", neg)),
        "advances_to_suppliers": round(s("supplier", pos)),
        "owe_store_suppliers_net": round(-s("supplier")),
        "accrued_payables": round(-s("liability")),
        "cash": round(s("cash")),
        "bank": round(s("bank")),
    }


def chips(con):
    """The filter chips that have accounts, with a count each (plus All)."""
    counts = dict(con.execute("""
        SELECT a.account_group, COUNT(*) FROM accounts a
        JOIN account_balances b USING (account_code) GROUP BY a.account_group"""))
    out = [{"key": chip, "label": label, "count": counts.get(grp, 0)}
           for grp, chip, label in CHIPS if counts.get(grp, 0)]
    out.append({"key": "all", "label": "All", "count": sum(c for g, c in counts.items() if g in PARTY_GROUPS)})
    return out


def _side(group, bal):
    """A short human label for which way a balance runs."""
    if group in ("cash", "bank"):
        return "in hand" if bal >= 0 else "overdrawn"
    if group == "customer":
        return "Owes us" if bal > 0 else "Advance received" if bal < 0 else "Settled"
    if group == "supplier":
        return "We owe" if bal < 0 else "Advance paid" if bal > 0 else "Settled"
    if group == "liability":
        return "Payable" if bal < 0 else "Debit balance"
    return "Owes us" if bal > 0 else "In advance"


def parties(con, chip="conversion", q="", limit=500):
    """The party / account list for the ledger desk, filtered by chip and search, biggest balance first."""
    where, params = ["b.balance IS NOT NULL"], []
    if chip and chip != "all":
        where.append("a.account_group = ?")
        params.append(CHIP_GROUP.get(chip, chip))
    else:
        where.append("a.account_group IN (%s)" % ",".join("?" * len(PARTY_GROUPS)))
        params.extend(PARTY_GROUPS)
    if q:
        where.append("(a.account_name LIKE ? OR a.account_code LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    rows = con.execute(f"""
        SELECT a.account_code, a.account_name, a.account_group, COALESCE(b.balance, 0) AS bal,
               (SELECT MAX(l.voucher_date) FROM ledger_entries l
                WHERE l.account_code = a.account_code AND l.voucher_kind <> 'opening') AS last_date
        FROM accounts a JOIN account_balances b USING (account_code)
        WHERE {' AND '.join(where)}
        ORDER BY ABS(COALESCE(b.balance, 0)) DESC
        LIMIT ?""", [*params, int(limit)]).fetchall()
    return [{"code": c, "name": n, "group": grp, "balance": round(bal),
             "side": _side(grp, bal), "last_date": ld} for c, n, grp, bal, ld in rows]


def ledger(con, account_code):
    """One account's full ledger: opening first, then entries with a running balance, plus a balance series."""
    acc = con.execute("SELECT account_name, account_group FROM accounts WHERE account_code = ?",
                       (account_code,)).fetchone()
    if not acc:
        return None
    name, group = acc
    go_live = _go_live(con)
    raw = con.execute("""
        SELECT CASE WHEN voucher_kind = 'opening' THEN 0 ELSE 1 END AS grp,
               voucher_date, voucher_type, voucher_no, voucher_kind, narration, COALESCE(amount, 0)
        FROM ledger_entries WHERE account_code = ?
        ORDER BY grp, voucher_date, voucher_type, voucher_no""", (account_code,)).fetchall()
    rows, run, debits, credits, entries = [], 0.0, 0.0, 0.0, 0
    series, seen_month = [], None
    for grp, vdate, vtype, vno, vkind, narr, amt in raw:
        run += amt or 0
        opening = (vkind == "opening")
        shown_date = go_live if opening else vdate
        if opening:
            narr = narr or "Balance brought forward from the old ERP"
        else:
            debits += amt if amt > 0 else 0
            credits += -amt if amt < 0 else 0
            entries += 1
        rows.append({"date": shown_date, "voucher_type": vtype, "voucher_no": vno, "kind": vkind,
                     "narration": narr, "debit": round(amt) if amt > 0 else 0,
                     "credit": round(-amt) if amt < 0 else 0, "balance": round(run)})
        mkey = str(shown_date)[:7]
        if mkey != seen_month:
            series.append({"date": shown_date, "balance": round(run)})
            seen_month = mkey
    if rows:
        series.append({"date": rows[-1]["date"], "balance": round(run)})
    return {
        "code": account_code, "name": name, "group": group,
        "balance": round(run), "side": _side(group, run),
        "debits": round(debits), "credits": round(credits), "entries": entries,
        "rows": rows, "series": series,
    }


# voucher kinds we always show a line for, in this order, even when the day has none
DAILY_KINDS = [("receipt", "Receipts"), ("payment", "Payments"),
               ("journal", "Journal"), ("sale", "Sales"), ("purchase", "Purchases")]


def daily(con, day):
    """The day's entries summarised by voucher type, plus cash/bank movement and the entry list."""
    d = day.isoformat() if hasattr(day, "isoformat") else str(day)
    by_type = con.execute("""
        SELECT voucher_kind, voucher_type,
               COUNT(DISTINCT voucher_id) AS vouchers,
               SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END) AS debit,
               SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END) AS credit
        FROM ledger_entries WHERE voucher_date = ? AND voucher_kind <> 'opening'
        GROUP BY voucher_kind, voucher_type""", (d,)).fetchall()
    order = {k: i for i, (k, _) in enumerate(DAILY_KINDS)}
    label = dict(DAILY_KINDS)
    summary = [{"kind": k, "label": label.get(k, k.title()), "voucher_type": vt,
                "vouchers": n, "debit": round(db or 0), "credit": round(cr or 0)}
               for k, vt, n, db, cr in sorted(by_type, key=lambda r: (order.get(r[0], 9), r[1]))]
    tot_v = sum(r["vouchers"] for r in summary)
    tot_d = sum(r["debit"] for r in summary)
    tot_c = sum(r["credit"] for r in summary)

    move = dict(con.execute("""
        SELECT a.account_group, SUM(l.amount) FROM ledger_entries l JOIN accounts a USING (account_code)
        WHERE l.voucher_date = ? AND a.account_group IN ('cash', 'bank') GROUP BY a.account_group""", (d,)))

    entries = [{"voucher_type": vt, "voucher_no": vno, "account": an, "narration": narr,
                "amount": round(abs(amt or 0)), "side": "Dr" if (amt or 0) > 0 else "Cr"}
               for vt, vno, an, narr, amt in con.execute("""
        SELECT l.voucher_type, l.voucher_no, a.account_name, l.narration, l.amount
        FROM ledger_entries l LEFT JOIN accounts a USING (account_code)
        WHERE l.voucher_date = ? AND l.voucher_kind <> 'opening'
        ORDER BY l.voucher_type, l.voucher_no LIMIT 300""", (d,))]
    return {
        "date": d, "summary": summary,
        "totals": {"vouchers": tot_v, "debit": round(tot_d), "credit": round(tot_c)},
        "cash_move": round(move.get("cash", 0) or 0), "bank_move": round(move.get("bank", 0) or 0),
        "entries": entries,
    }


def overview(con):
    """What the dashboard's /api/data embeds for the Finance tab's first paint."""
    return {"kpis": kpis(con), "chips": chips(con), "go_live": _go_live(con),
            "parties": parties(con, chip=(chips(con)[0]["key"] if chips(con) else "all"))}
