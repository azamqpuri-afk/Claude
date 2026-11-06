"""
Store reports added in 0.15 (generalised, read-only):

  store_expense   value issued, value returned and NET, per item, for a period (this month by default)
  issue_slips     every issue slip in a period (date, number, lines, value) - tap a slip for its lines
  store_returns   every store return in a period
  unused_stock    items not issued for N months: qty, last issue, last purchase, value, highest value first
  loom_consumption store value consumed per loom/department, month by month (a matrix), highest first

Rates audit and the slip/loom drill-downs are endpoints (they take an item / slip / loom), in web/server.py.
"""

from datetime import date, timedelta

from . import Report


def _months(d0, d1):
    out, y, m = [], d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _range(d0, d1, months_back=0):
    d1 = d1 or date.today()
    if not d0:
        d0 = date(d1.year, d1.month, 1) if not months_back else (d1 - timedelta(days=30 * months_back))
    return d0, d1


def store_expense(con, cfg, d0=None, d1=None):
    """Store expense = value issued less value returned, per item, over the period (this month by default)."""
    d0, d1 = _range(d0, d1)
    cur = cfg["company"]["currency"]
    iss = dict(con.execute("SELECT item_code, SUM(amount) FROM store_issues WHERE doc_date BETWEEN ? AND ? GROUP BY item_code",
                           (d0.isoformat(), d1.isoformat())))
    ret = dict(con.execute("SELECT item_code, SUM(amount) FROM store_returns WHERE doc_date BETWEEN ? AND ? GROUP BY item_code",
                           (d0.isoformat(), d1.isoformat())))
    names = {c: n for c, n in con.execute("SELECT item_code, item_name FROM items")}
    heads = _item_heads(con)                              # item_code -> head (item 30e); {} when the ERP has no heads
    rows = []
    for code in set(iss) | set(ret):
        i, r = round(iss.get(code, 0) or 0), round(ret.get(code, 0) or 0)
        rows.append([names.get(code, code), code, heads.get(code, ""), i, r, i - r])
    rows.sort(key=lambda x: -x[5])
    ti, tr = sum(r[3] for r in rows), sum(r[4] for r in rows)
    summary = [f"{d0:%d-%b-%Y} to {d1:%d-%b-%Y}: issued {cur} {ti:,.0f}, returned {cur} {tr:,.0f}, "
               f"NET store expense {cur} {ti - tr:,.0f} over {len(rows)} items."]
    if heads:                                            # a "by head" breakdown line when the ERP groups items by head
        by_head = {}
        for rr in rows:
            by_head[rr[2] or "—"] = by_head.get(rr[2] or "—", 0) + rr[5]
        top = sorted(by_head.items(), key=lambda x: -x[1])[:6]
        summary.append("By head: " + " · ".join(f"{h} {cur} {v:,.0f}" for h, v in top))
    return Report(f"Store expense - {d0:%d-%b-%Y} to {d1:%d-%b-%Y}",
                  ["Item", "Code", "Head", f"Issued {cur}", f"Returned {cur}", f"Net {cur}"], rows, summary)


def _item_heads(con):
    """item_code -> head name, when the items master carries an item head (item 30e). {} if the column is empty."""
    try:
        out = {c: h for c, h in con.execute("SELECT item_code, head FROM items WHERE head IS NOT NULL AND TRIM(head) <> ''")}
        return out
    except Exception:
        return {}


def issue_slips(con, cfg, d0=None, d1=None):
    """Every issue slip in the period: date, number, lines, departments/looms, value (newest first)."""
    d0, d1 = _range(d0, d1)
    cur = cfg["company"]["currency"]
    rows = [[dd, dno, n, to or "", round(amt or 0)] for dno, dd, n, to, amt in con.execute(
        """SELECT doc_no, MIN(doc_date), COUNT(*), MAX(issued_to), SUM(amount) FROM store_issues
           WHERE doc_date BETWEEN ? AND ? GROUP BY doc_no ORDER BY MIN(doc_date) DESC, doc_no DESC""",
        (d0.isoformat(), d1.isoformat()))]
    summary = [f"{len(rows)} issue slips, {cur} {sum(r[4] for r in rows):,.0f}, {d0:%d-%b-%Y} to {d1:%d-%b-%Y}. Tap a slip for its lines."]
    return Report(f"Issue slips - {d0:%d-%b-%Y} to {d1:%d-%b-%Y}",
                  ["Date", "Slip no.", "Lines", "To", f"Value {cur}"], rows, summary)


def store_returns(con, cfg, d0=None, d1=None):
    """Every store return in the period."""
    d0, d1 = _range(d0, d1)
    cur = cfg["company"]["currency"]
    rows = [[dd, dno, n, frm or "", round(amt or 0)] for dno, dd, n, frm, amt in con.execute(
        """SELECT doc_no, MIN(doc_date), COUNT(*), MAX(returned_from), SUM(amount) FROM store_returns
           WHERE doc_date BETWEEN ? AND ? GROUP BY doc_no ORDER BY MIN(doc_date) DESC, doc_no DESC""",
        (d0.isoformat(), d1.isoformat()))]
    summary = [f"{len(rows)} return slips, {cur} {sum(r[4] for r in rows):,.0f}, {d0:%d-%b-%Y} to {d1:%d-%b-%Y}."]
    return Report(f"Store returns - {d0:%d-%b-%Y} to {d1:%d-%b-%Y}",
                  ["Date", "Slip no.", "Lines", "From", f"Value {cur}"], rows, summary)


def unused_stock(con, cfg, as_of=None):
    """Items in stock with no issue for the configured number of months: qty, last issue, last purchase, value."""
    as_of = as_of or date.today()
    months = int(cfg["reports"].get("unused_stock_months", 6))
    cutoff = (as_of - timedelta(days=int(months * 30.44))).isoformat()
    cur = cfg["company"]["currency"]
    stock = dict(con.execute("SELECT item_code, SUM(qty) FROM stock_balances GROUP BY item_code"))
    last_issue = dict(con.execute("SELECT item_code, MAX(doc_date) FROM store_issues GROUP BY item_code"))
    names = {c: n for c, n in con.execute("SELECT item_code, item_name FROM items")}
    heads = _item_heads(con)                              # item 30e
    rows = []
    for code, qty in stock.items():
        if (qty or 0) <= 0:
            continue
        li = last_issue.get(code)
        if li and li > cutoff:                       # issued within the window - it is in use
            continue
        lp = con.execute("""SELECT doc_date, rate, supplier_name FROM store_receipts WHERE item_code = ? AND rate > 0
                            ORDER BY doc_date DESC, doc_no DESC LIMIT 1""", (code,)).fetchone()
        rate = lp[1] if lp else None
        value = round((qty or 0) * rate) if rate else None
        rows.append([names.get(code, code), code, heads.get(code, ""), round(qty, 2), li or "never", lp[0] if lp else None,
                     round(rate) if rate else None, value])
    rows.sort(key=lambda x: -(x[7] or 0))
    total = sum((r[7] or 0) for r in rows)
    summary = [f"{len(rows)} item(s) in stock with no issue for {months}+ months, tying up about {cur} {total:,.0f}."]
    if heads:
        by_head = {}
        for rr in rows:
            by_head[rr[2] or "—"] = by_head.get(rr[2] or "—", 0) + (rr[7] or 0)
        top = sorted(by_head.items(), key=lambda x: -x[1])[:6]
        summary.append("By head: " + " · ".join(f"{h} {cur} {v:,.0f}" for h, v in top))
    return Report(f"Unused stock ({months}+ months) - {as_of:%d-%b-%Y}",
                  ["Item", "Code", "Head", "Stock", "Last issued", "Last bought", f"Last rate {cur}", f"Value {cur}"], rows, summary)


def _is_loom(to, loomset):
    t = str(to or "").upper()
    return to in loomset or "LOOM" in t


def loom_consumption(con, cfg, d0=None, d1=None):
    """Store value consumed per loom / department, month by month (a matrix): months across, looms then
    departments down, with per-month and per-row totals. Net of returns. Highest total first within each group."""
    d1 = d1 or date.today()
    d0 = d0 or (date(d1.year, d1.month, 1) - timedelta(days=365))   # ~13 months by default
    d0 = d0.replace(day=1)
    cur = cfg["company"]["currency"]
    ms = _months(d0, d1)
    loomset = {c for (c,) in con.execute("SELECT loom_name FROM looms")} | {c for (c,) in con.execute("SELECT loom_code FROM looms")}
    cell = {}                                        # (to, month) -> net value
    for to, mth, amt in con.execute("""SELECT issued_to, strftime('%Y-%m', doc_date), SUM(amount) FROM store_issues
            WHERE doc_date BETWEEN ? AND ? GROUP BY issued_to, 2""", (d0.isoformat(), d1.isoformat())):
        cell[(to or "?", mth)] = cell.get((to or "?", mth), 0) + (amt or 0)
    for frm, mth, amt in con.execute("""SELECT returned_from, strftime('%Y-%m', doc_date), SUM(amount) FROM store_returns
            WHERE doc_date BETWEEN ? AND ? GROUP BY returned_from, 2""", (d0.isoformat(), d1.isoformat())):
        cell[(frm or "?", mth)] = cell.get((frm or "?", mth), 0) - (amt or 0)
    tos = sorted({k[0] for k in cell})
    looms = sorted([t for t in tos if _is_loom(t, loomset)], key=lambda t: _loom_num(t))
    depts = sorted([t for t in tos if not _is_loom(t, loomset)], key=lambda t: -sum(cell.get((t, m), 0) for m in ms))
    rows = []
    for t in looms + depts:
        vals = [round(cell.get((t, m), 0)) for m in ms]
        rows.append([t] + vals + [round(sum(vals))])
    totals = ["TOTAL"] + [round(sum(cell.get((t, m), 0) for t in tos)) for m in ms]
    totals += [round(sum(totals[1:]))]
    rows.append(totals)
    summary = [f"Store value consumed per loom/department, {ms[0]} to {ms[-1]} (net of returns), in {cur}.",
               "Looms first (by number), then departments; each row's highest periods stand out."]
    return Report(f"Store consumption by loom - {ms[0]} to {ms[-1]}",
                  ["Loom / Dept"] + [m for m in ms] + ["Total"], rows, summary)


def _loom_num(t):
    import re
    d = re.findall(r"\d+", str(t or ""))
    return int(d[-1]) if d else 9999


# ---- drill-downs (endpoints; they take an item / slip / loom) --------------------------------------------------
def rates_audit(con, cfg, item, d0=None, d1=None):
    """Purchase history of one item (and similar-named items) over a period: date, GRN/SRN, supplier, qty, rate,
    amount; plus lowest/highest/last rate and each supplier's last & average rate."""
    d1 = d1 or date.today()
    d0 = d0 or (d1 - timedelta(days=365))
    name = con.execute("SELECT item_name FROM items WHERE item_code = ?", (item,)).fetchone()
    if not name:                                          # not a code - the caller passed an item NAME; resolve it
        byname = con.execute("SELECT item_code, item_name FROM items WHERE item_name = ? LIMIT 1", (item,)).fetchone()
        if byname:
            item, name = byname[0], (byname[1],)
    rows = con.execute("""SELECT doc_date, doc_no, supplier_name, qty, rate, amount FROM store_receipts
                          WHERE item_code = ? AND doc_date BETWEEN ? AND ? AND rate > 0
                          ORDER BY doc_date DESC, doc_no DESC""", (item, d0.isoformat(), d1.isoformat())).fetchall()
    hist = [{"date": a, "doc_no": b, "supplier": c, "qty": round(d or 0, 2), "rate": round(e or 0, 2),
             "amount": round(f or 0)} for a, b, c, d, e, f in rows]
    rates = [r["rate"] for r in hist if r["rate"]]
    by_sup = {}
    for r in hist:
        by_sup.setdefault(r["supplier"] or "?", []).append(r)
    suppliers = [{"supplier": s, "n": len(v), "last_rate": v[0]["rate"],
                  "avg_rate": round(sum(x["rate"] for x in v) / len(v), 2)} for s, v in by_sup.items()]
    suppliers.sort(key=lambda s: s["avg_rate"])
    # similar-named items also bought
    from .stock import _in_stock_index, _similar_in_stock
    similar = _similar_in_stock(_in_stock_index(con), item, (name[0] if name else item))
    return {"item_code": item, "item_name": (name[0] if name else item),
            "rows": hist, "suppliers": suppliers,
            "summary": {"low": min(rates) if rates else None, "high": max(rates) if rates else None,
                        "last": hist[0]["rate"] if hist else None, "n": len(hist)},
            "similar": similar, "from": d0.isoformat(), "to": d1.isoformat()}


def slip_lines(con, doc_no):
    """The lines of one issue slip."""
    rows = con.execute("""SELECT s.doc_date, COALESCE(i.item_name, s.item_code), s.item_code, s.qty, s.amount, s.issued_to
                          FROM store_issues s LEFT JOIN items i ON i.item_code = s.item_code
                          WHERE s.doc_no = ? ORDER BY s.item_code""", (doc_no,)).fetchall()
    return {"doc_no": doc_no, "date": (rows[0][0] if rows else None),
            "rows": [{"item": b, "code": c, "qty": round(d or 0, 2), "amount": round(e or 0), "to": f} for _, b, c, d, e, f in rows]}


def loom_lines(con, loom, d0=None, d1=None, limit=300):
    """The issue lines that went to one loom/department in a period (newest first)."""
    d1 = d1 or date.today()
    d0 = d0 or (date(d1.year, d1.month, 1) - timedelta(days=365)).replace(day=1)
    rows = con.execute("""SELECT s.doc_date, s.doc_no, COALESCE(i.item_name, s.item_code), s.qty, s.amount
                          FROM store_issues s LEFT JOIN items i ON i.item_code = s.item_code
                          WHERE s.issued_to = ? AND s.doc_date BETWEEN ? AND ? ORDER BY s.doc_date DESC, s.doc_no DESC LIMIT ?""",
                       (loom, d0.isoformat(), d1.isoformat(), int(limit))).fetchall()
    return {"loom": loom, "rows": [{"date": a, "slip": b, "item": c, "qty": round(d or 0, 2), "amount": round(e or 0)}
                                   for a, b, c, d, e in rows]}
