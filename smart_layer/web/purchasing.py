"""
Store purchasing: indents -> purchase orders -> goods receipt (item 30b).

Optional: reads purchase_indents and purchase_orders (the PO line already carries the matching goods-receipt figures,
so PO rate/qty can be compared with what was actually received). A mill that does not map these has no Purchasing
tab. In mills where the whole chain is keyed in one go at receipt, the "pending" lists are usually empty and the
useful view is the PO-vs-GRN differences and the supplier terms - so that leads. All generic; the SQL is in the
mapping.
"""

from datetime import date


def installed(con):
    try:
        return bool(con.execute("SELECT 1 FROM sync_log WHERE entity = 'purchase_orders' LIMIT 1").fetchone())
    except Exception:
        return False


def _item_names(con):
    try:
        return {str(c): n for c, n in con.execute("SELECT item_code, item_name FROM items")}
    except Exception:
        return {}


def _diff(a, b, tol=1e-6):
    return a is not None and b is not None and abs((a or 0) - (b or 0)) > tol


_PAID_SET = "('PAID','Y','YES')"                           # values that count as paid (case-insensitive)


def overview(con, cfg, q="", only="all", limit=500):
    """PO lines with their goods-receipt comparison, newest first. `only`: all | diff (rate or qty differs) |
    pending (not received) | unpaid. `q` searches PO no / supplier / item. Tiles are counted over ALL PO lines in
    SQL (not just the listed page), then the list is limited. Plus the pending indents."""
    if not installed(con):
        return {"installed": False, "rows": [], "pending_indents": [], "tiles": {}}
    names = _item_names(con)
    # headline tiles over EVERY PO line (DBSERVER: the old per-page count under-reported on 5,216 live lines)
    tiles = dict(zip(("po_lines", "received", "pending", "rate_diffs", "qty_diffs", "unpaid"),
                     con.execute(f"""SELECT COUNT(*), SUM(received_qty IS NOT NULL), SUM(received_qty IS NULL),
                            SUM(received_rate IS NOT NULL AND ABS(COALESCE(rate,0) - received_rate) > 1e-6),
                            SUM(received_qty IS NOT NULL AND ABS(COALESCE(qty,0) - received_qty) > 1e-6),
                            SUM(paid IS NOT NULL AND UPPER(paid) NOT IN {_PAID_SET})
                        FROM purchase_orders""").fetchone()))
    tiles = {k: (v or 0) for k, v in tiles.items()}
    rows = []
    for r in con.execute("""SELECT po_no, po_date, supplier_code, supplier_name, item_code, rate, qty, purchaser,
                                   payment_term, paid, payment_date, received_qty, received_rate, received_date, grn_no
                            FROM purchase_orders ORDER BY po_date DESC, po_no DESC"""):
        (po_no, po_date, sup_c, sup_n, ic, rate, qty, buyer, term, paid, pay_dt,
         rq, rr, rdt, grn) = r
        rate_diff = _diff(rate, rr)
        qty_diff = _diff(qty, rq)
        received = rq is not None
        rows.append({"po_no": po_no, "po_date": str(po_date)[:10] if po_date else None,
                     "supplier": sup_n or sup_c, "item": names.get(str(ic), ic), "item_code": ic,
                     "rate": rate, "qty": qty, "buyer": buyer, "term": term, "paid": paid,
                     "payment_date": str(pay_dt)[:10] if pay_dt else None,
                     "received_qty": rq, "received_rate": rr, "received_date": str(rdt)[:10] if rdt else None,
                     "grn_no": grn, "received": received,
                     "rate_diff": rate_diff, "qty_diff": qty_diff,
                     "diff": bool(rate_diff or qty_diff)})
    ql = q.strip().lower()
    if ql:
        rows = [r for r in rows if ql in " ".join(str(x) for x in (r["po_no"], r["supplier"], r["item"], r["item_code"]) if x).lower()]
    if only == "diff":
        rows = [r for r in rows if r["diff"]]
    elif only == "pending":
        rows = [r for r in rows if not r["received"]]
    elif only == "unpaid":
        rows = [r for r in rows if r["paid"] and str(r["paid"]).upper() not in ("PAID", "Y", "YES")]
    return {"installed": True, "rows": rows[:int(limit)], "total": len(rows), "tiles": tiles,
            "pending_indents": pending_indents(con, cfg, names)}


def pending_indents(con, cfg, names=None):
    """Indent lines that no PO line references (REF_INDENT_* on the PO points back at the indent line). Usually
    empty in a mill that keys the whole chain at receipt."""
    if names is None:
        names = _item_names(con)
    try:
        refd = set()
        for tn, sr in con.execute("SELECT ref_indent_trans, ref_indent_sr FROM purchase_orders "
                                  "WHERE ref_indent_trans IS NOT NULL"):
            refd.add((str(tn), str(sr)))
        out = []
        for ino, idt, tn, sr, dept, ic, qty, rdt, purpose in con.execute(
                """SELECT indent_no, indent_date, trans_no, sr_no, dept, item_code, qty, required_date, purpose
                   FROM purchase_indents ORDER BY indent_date DESC LIMIT 4000"""):
            if (str(tn), str(sr)) in refd:
                continue
            out.append({"indent_no": ino, "indent_date": str(idt)[:10] if idt else None, "dept": dept,
                        "item": names.get(str(ic), ic), "qty": qty,
                        "required_date": str(rdt)[:10] if rdt else None, "purpose": purpose})
        return out[:500]
    except Exception:
        return []


def item_pos(con, item, limit=20):
    """One item's recent PO lines (the item drill): supplier, rate, qty, received, terms."""
    if not installed(con):
        return {"installed": False, "rows": []}
    names = _item_names(con)
    rows = [{"po_no": po, "po_date": str(d)[:10] if d else None, "supplier": sn or sc, "rate": rate, "qty": qty,
             "received_rate": rr, "received_qty": rq, "paid": paid}
            for po, d, sc, sn, rate, qty, rr, rq, paid in con.execute(
                """SELECT po_no, po_date, supplier_code, supplier_name, rate, qty, received_rate, received_qty, paid
                   FROM purchase_orders WHERE item_code = ? ORDER BY po_date DESC LIMIT ?""", (item, int(limit)))]
    return {"installed": True, "item": names.get(str(item), item), "rows": rows}
