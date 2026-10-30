"""
Store stock reports (generalised from Dost Textiles' 34_, 36_ and 39_ scripts):
  stock_status  - every tracked item: OUT OF STOCK / LOW STOCK / IN STOCK
  new_zero      - items that ran out since the last check (for a daily push)
  reorder_list  - draft order list for out / low items, with last supplier and rate

"Tracked items" = items issued from the store in the last
reports.tracked_issue_days days, i.e. items the mill actually uses.
"LOW" = stock below one month of average use (the ERP's reorder levels are
often not maintained, so usage is the reliable yardstick).
"""

import re
from datetime import date, timedelta

from ..store import get_state, set_state
from . import Report

_WORD = re.compile(r"[A-Za-z0-9]+")


def _tokens(s):
    return _WORD.findall((s or "").upper())


def _is_partnum(tok):
    return len(tok) >= 3 and any(c.isdigit() for c in tok)


def _strict_similar(a, b):
    """Strict name/code similarity: one token list is a whole-word prefix of the other where that prefix is
    specific (a part number like 6202, or 2+ words) - e.g. 6202 vs 6202 SKF; or both start with the same
    part-number-like token - e.g. 6202 SKF vs 6202 ZZ. A single generic word (BELT vs BELT GUARD) is NOT a match,
    and there is no fuzzy / typo matching."""
    if not a or not b or a == b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if long[:len(short)] == short and (len(short) >= 2 or any(_is_partnum(t) for t in short)):
        return True
    return a[0] == b[0] and _is_partnum(a[0])


def _in_stock_index(con):
    """Every item that currently has stock > 0, with its name/code tokens, to match near-identical names against."""
    pos = []
    for code, name, qty, unit in con.execute("""
            SELECT st.item_code, COALESCE(i.item_name, st.item_code), st.qty, i.unit
            FROM (SELECT item_code, SUM(qty) AS qty FROM stock_balances GROUP BY item_code) st
            LEFT JOIN items i ON i.item_code = st.item_code WHERE st.qty > 0"""):
        pos.append((code, name, qty, unit or "", _tokens(name), _tokens(code)))
    return pos


def _similar_in_stock(pos, code, name, limit=3):
    """Items with stock whose name OR code is strictly similar to this one (excluding the item itself)."""
    nt, ct = _tokens(name), _tokens(code)
    out = [(pname, pcode, qty, unit) for pcode, pname, qty, unit, pnt, pct in pos
           if pcode != code and (_strict_similar(nt, pnt) or _strict_similar(ct, pct))]
    out.sort(key=lambda x: -x[2])
    return [f"{pname} [{pcode}]: {qty:g}{(' ' + unit) if unit else ''}" for pname, pcode, qty, unit in out[:limit]]


def store_room_rows(con, cfg, as_of=None):
    """The whole store room: EVERY item that has stock now or any issue/receipt in history - not just the out/low
    items used lately. Status and average use are still worked out over the tracked window; an item not issued in
    that window shows 'no recent use' and keeps its real stock, instead of disappearing. Newest-movement first.
    (The out/low and demand reports keep their usage filter; this is the searchable full list for the Store tab.)"""
    as_of = as_of or date.today()
    days = int(cfg["reports"]["tracked_issue_days"])
    since = (as_of - timedelta(days=days)).isoformat()
    months = days / 30.0
    stock = dict(con.execute("SELECT item_code, SUM(qty) FROM stock_balances GROUP BY item_code"))
    names = {c: (n, u) for c, n, u in con.execute("SELECT item_code, item_name, unit FROM items")}
    win = {c: (iss, last) for c, iss, last in con.execute(
        "SELECT item_code, SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END), MAX(doc_date) "
        "FROM store_issues WHERE doc_date > ? AND doc_date <= ? GROUP BY item_code", (since, as_of.isoformat()))}
    last_issue = dict(con.execute("SELECT item_code, MAX(doc_date) FROM store_issues GROUP BY item_code"))
    last_recv = dict(con.execute("SELECT item_code, MAX(doc_date) FROM store_receipts GROUP BY item_code"))
    codes = set(c for c, q in stock.items() if (q or 0) != 0) | set(last_issue) | set(last_recv)
    out = []
    for code in codes:
        qty = stock.get(code, 0) or 0
        iss, _ = win.get(code, (0, None))
        avg = (iss or 0) / months
        recent = bool(win.get(code))
        if not recent:
            status = "IN STOCK" if qty > 0 else "IDLE"          # dormant item: not alarming, just listed
        else:
            status = "OUT OF STOCK" if qty <= 0 else "LOW STOCK" if qty < avg else "IN STOCK"
        name = (names.get(code) or (code, ""))[0]
        unit = (names.get(code) or (code, ""))[1] or ""
        out.append(dict(item_code=code, item_name=name or code, unit=unit, stock=qty, avg_month=avg,
                        status=status, recent_use=recent,
                        last_issue=last_issue.get(code), last_receipt=last_recv.get(code)))
    order = {"OUT OF STOCK": 0, "LOW STOCK": 1, "IN STOCK": 2, "IDLE": 3}
    out.sort(key=lambda r: (r["last_issue"] or "0000-00-00"), reverse=True)   # newest movement first (None last)
    out.sort(key=lambda r: order.get(r["status"], 9))                         # stable: out/low on top
    return out


def _status_rows(con, as_of, days):
    since = (as_of - timedelta(days=days)).isoformat()
    months = days / 30.0
    rows = con.execute("""
        WITH used AS (
            SELECT item_code, SUM(qty) AS issued, MAX(doc_date) AS last_issue
            FROM store_issues WHERE doc_date > ? AND doc_date <= ? AND qty > 0
            GROUP BY item_code),
        stock AS (SELECT item_code, SUM(qty) AS qty FROM stock_balances GROUP BY item_code)
        SELECT u.item_code, COALESCE(i.item_name, u.item_code), i.unit, COALESCE(s.qty, 0), u.issued, u.last_issue
        FROM used u LEFT JOIN items i ON i.item_code = u.item_code LEFT JOIN stock s ON s.item_code = u.item_code
        ORDER BY 2""", (since, as_of.isoformat())).fetchall()
    out = []
    for code, name, unit, qty, issued, last_issue in rows:
        avg = issued / months
        status = "OUT OF STOCK" if qty <= 0 else "LOW STOCK" if qty < avg else "IN STOCK"
        out.append(dict(item_code=code, item_name=name, unit=unit or "", stock=qty, avg_month=avg,
                        status=status, last_issue=last_issue))
    return out


def stock_status(con, cfg, as_of=None):
    as_of = as_of or date.today()
    days = int(cfg["reports"]["tracked_issue_days"])
    items = _status_rows(con, as_of, days)
    order = {"OUT OF STOCK": 0, "LOW STOCK": 1, "IN STOCK": 2}
    items.sort(key=lambda r: (order[r["status"]], r["item_name"]))
    n = {s: sum(1 for r in items if r["status"] == s) for s in order}
    pos = _in_stock_index(con)
    rows, dupes = [], 0
    for r in items:
        # for out/low items, flag a near-identical item that DOES have stock (so it isn't re-ordered by mistake)
        sim = "; ".join(_similar_in_stock(pos, r["item_code"], r["item_name"])) if r["status"] != "IN STOCK" else ""
        if sim:
            dupes += 1
        rows.append([r["status"], r["item_name"], r["item_code"], r["unit"], r["stock"], r["avg_month"], r["last_issue"], sim])
    summary = [f"{len(items)} items used in the last {days} days: {n['OUT OF STOCK']} out of stock, "
               f"{n['LOW STOCK']} low (below one month of use), {n['IN STOCK']} in stock."]
    if dupes:
        summary.append(f"{dupes} out/low item(s) have a similar-named item still in stock - check before ordering.")
    return Report(
        f"Store stock check - {as_of:%d-%b-%Y}",
        ["Status", "Item", "Code", "Unit", "Stock", "Avg use / month", "Last issued", "Similar item in stock"],
        rows, summary,
    )


def _last_purchase(con, code):
    return con.execute("""SELECT doc_date, supplier_name, qty, rate FROM store_receipts
                          WHERE item_code = ? AND qty > 0 ORDER BY doc_date DESC, doc_no DESC LIMIT 1""", (code,)).fetchone()


def _last_issue(con, code):
    return con.execute("""SELECT doc_date, issued_to FROM store_issues
                          WHERE item_code = ? AND qty > 0 ORDER BY doc_date DESC, doc_no DESC LIMIT 1""", (code,)).fetchone()


def issue_history(con, code, limit=20, as_of=None):
    """The last `limit` store issues of one item, newest first, for the drill-down: date, who it went to (loom /
    department), quantity, value, slip number and who keyed it. Plus a 90-day summary line. Zero-quantity slips
    (a demand with nothing issued) are flagged. Read-only; no schema change (store_issues already has these)."""
    as_of = as_of or date.today()
    limit = max(1, min(int(limit), 200))
    name = con.execute("SELECT item_name, unit FROM items WHERE item_code = ?", (code,)).fetchone()
    rows = con.execute("""SELECT doc_date, issued_to, qty, amount, doc_no, entered_by FROM store_issues
                          WHERE item_code = ? ORDER BY doc_date DESC, entered_at DESC, rowid DESC LIMIT ?""",
                       (code, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = [{"date": dd, "to": to, "qty": qty, "amount": amt, "slip": dno, "by": by,
            "zero": (qty or 0) <= 0} for dd, to, qty, amt, dno, by in rows]
    since = (as_of - timedelta(days=90)).isoformat()
    n90, q90 = con.execute("""SELECT COUNT(*), COALESCE(SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END), 0)
                              FROM store_issues WHERE item_code = ? AND doc_date > ? AND doc_date <= ?""",
                           (code, since, as_of.isoformat())).fetchone()
    total = con.execute("SELECT COUNT(*) FROM store_issues WHERE item_code = ?", (code,)).fetchone()[0]
    return {"item_code": code, "item_name": (name[0] if name else code), "unit": (name[1] if name else "") or "",
            "rows": out, "more": more, "total": total,
            "summary": {"days": 90, "slips": n90 or 0, "qty": round(q90 or 0, 2)}}


def new_zero(con, cfg, as_of=None, remember=False):
    """Items that were in stock at the previous check and are at zero now.
    The first run only records the current state (nothing is 'new' yet). remember=True (only the daily push) moves
    the baseline forward; dashboard views and manual runs leave it, so opening the report never uses up the alert."""
    as_of = as_of or date.today()
    items = _status_rows(con, as_of, int(cfg["reports"]["tracked_issue_days"]))
    now = {r["item_code"]: r["stock"] for r in items}
    before = get_state(con, "stock_snapshot")
    rows = []
    if before is not None:
        for r in items:
            if r["stock"] <= 0 and before.get(r["item_code"], 0) > 0:
                lp = _last_purchase(con, r["item_code"])
                rows.append([r["item_name"], r["item_code"], r["stock"], r["avg_month"],
                             lp[0] if lp else None, lp[1] if lp else None, lp[3] if lp else None])
    if remember:
        set_state(con, "stock_snapshot", now)
    summary = ([f"{len(rows)} item(s) ran out since the last check."] if before is not None
               else ["First run: current stock recorded; new zero-stock items are reported from the next run."])
    return Report(f"Ran out of stock - {as_of:%d-%b-%Y}",
                  ["Item", "Code", "Stock", "Avg use / month", "Last bought", "From", "Rate"], rows, summary)


def demand_list(con, cfg, as_of=None):
    """Items the store should be buying now, on usage not on the ERP's reorder levels (often unmaintained). An item
    is listed when ANY of: (a) it was issued in the last `demand_used_days` and stock is now <= 0; (b) stock is
    below its 90-day average monthly use; (c) a zero-quantity issue slip was raised in the last `demand_zero_days`
    (a demand written when nothing was in stock). Columns: item, stock, avg use, last issue, last purchase + from,
    and why it is listed. A draft to review - nothing is ordered."""
    as_of = as_of or date.today()
    r = cfg["reports"]
    used_days = int(r.get("demand_used_days", 180))
    avg_days = int(r.get("demand_avg_days", 90))
    zero_days = int(r.get("demand_zero_days", 60))
    d = as_of.isoformat()
    used_since = (as_of - timedelta(days=used_days)).isoformat()
    avg_since = (as_of - timedelta(days=avg_days)).isoformat()
    zero_since = (as_of - timedelta(days=zero_days)).isoformat()
    months = avg_days / 30.0
    # candidates: issued (qty>0) within the used window, OR a zero-qty demand slip within the zero window
    cand = con.execute("""
        WITH used AS (SELECT item_code, SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END) AS issued90
                      FROM store_issues WHERE doc_date > ? AND doc_date <= ? GROUP BY item_code),
             used180 AS (SELECT item_code, SUM(CASE WHEN qty > 0 THEN qty ELSE 0 END) AS issued180
                         FROM store_issues WHERE doc_date > ? AND doc_date <= ? GROUP BY item_code),
             zero AS (SELECT DISTINCT item_code FROM store_issues WHERE doc_date > ? AND doc_date <= ? AND COALESCE(qty,0) <= 0),
             stock AS (SELECT item_code, SUM(qty) AS qty FROM stock_balances GROUP BY item_code)
        SELECT c.item_code, COALESCE(i.item_name, c.item_code), i.unit, COALESCE(s.qty, 0),
               COALESCE(u.issued90, 0), COALESCE(u2.issued180, 0), (c.item_code IN (SELECT item_code FROM zero)) AS has_zero
        FROM (SELECT item_code FROM used180 WHERE issued180 > 0 UNION SELECT item_code FROM zero) c
        LEFT JOIN items i ON i.item_code = c.item_code
        LEFT JOIN stock s ON s.item_code = c.item_code
        LEFT JOIN used u ON u.item_code = c.item_code
        LEFT JOIN used180 u2 ON u2.item_code = c.item_code
        ORDER BY 2""", (avg_since, d, used_since, d, zero_since, d)).fetchall()
    pos = _in_stock_index(con)
    cur = cfg["company"]["currency"]
    rows = 0
    out = []
    for code, name, unit, qty, issued90, issued180, has_zero in cand:
        avg = (issued90 or 0) / months
        reasons = []
        if qty <= 0 and issued180 > 0:                    # rule (a): used in the last 180 days AND out of stock now
            reasons.append("out of stock, still in use")
        if avg > 0 and qty < avg:
            reasons.append("below 90-day average use")
        if has_zero:
            reasons.append(f"demand slip with no stock in last {zero_days} days")
        if not reasons:
            continue
        rows += 1
        lp = _last_purchase(con, code)
        li = _last_issue(con, code)
        sim = "; ".join(_similar_in_stock(pos, code, name))
        out.append([name, code, unit or "", qty, round(avg, 1),
                    li[0] if li else None, lp[0] if lp else None,
                    (lp[1] if (lp and lp[1]) else "(no purchase on record)"),
                    lp[3] if (lp and lp[3]) else None, "; ".join(reasons), sim])
    summary = [f"{rows} item(s) the store should consider buying, on usage (last {used_days} days) not the ERP's "
               f"reorder levels. Average use is per 30 days over the last {avg_days} days.",
               "DRAFT - review before anything is ordered."]
    dupes = sum(1 for x in out if x[10])
    if dupes:
        summary.append(f"{dupes} item(s) have a similar-named item still in stock - check the last column first.")
    return Report(f"Store demand - {as_of:%d-%b-%Y}",
                  ["Item", "Code", "Unit", "Stock", "Avg use / month", "Last issued", "Last bought", "Last from",
                   f"Last rate {cur}", "Why listed", "Similar item in stock"], out, summary)


def reorder_list(con, cfg, as_of=None):
    """Draft only - nothing is sent to suppliers; the owner reviews it first."""
    as_of = as_of or date.today()
    cover = float(cfg["reports"]["reorder_cover_months"])
    pos = _in_stock_index(con)
    rows, total = [], 0.0
    for r in _status_rows(con, as_of, int(cfg["reports"]["tracked_issue_days"])):
        if r["status"] == "IN STOCK":
            continue
        need = max(0.0, cover * r["avg_month"] - max(r["stock"], 0))
        lp = _last_purchase(con, r["item_code"])
        rate = lp[3] if lp and lp[3] else None
        value = need * rate if rate else None
        total += value or 0
        li = _last_issue(con, r["item_code"])
        party = lp[1] if (lp and lp[1]) else "(no purchase on record)"
        sim = "; ".join(_similar_in_stock(pos, r["item_code"], r["item_name"]))
        rows.append([r["item_name"], r["item_code"], r["stock"], round(need, 2),
                     rate, party, round(lp[2]) if (lp and lp[2]) else None, lp[0] if lp else None,
                     (li[1] if li else None), sim])
    # items with a known last purchase first; "(no purchase on record)" at the bottom
    rows.sort(key=lambda x: (x[5] == "(no purchase on record)", str(x[5]), str(x[0])))
    cur = cfg["company"]["currency"]
    dupes = sum(1 for x in rows if x[9])
    summary = [f"{len(rows)} items to order for {cover:g} months of use, about {cur} {total:,.0f} at last rates.",
               "DRAFT - review before anything is sent to suppliers."]
    if dupes:
        summary.insert(1, f"{dupes} item(s) have a similar-named item still in stock - check the last column before ordering.")
    return Report(f"Draft order list - {as_of:%d-%b-%Y}",
                  ["Item", "Code", "Stock", "Order qty", "Last rate", "Last party", "Last qty", "Last date",
                   "Last issued to", "Similar item in stock"], rows, summary)
