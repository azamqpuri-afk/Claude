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


def new_zero(con, cfg, as_of=None, remember=True):
    """Items that were in stock at the previous check and are at zero now.
    The first run only records the current state (nothing is 'new' yet)."""
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
