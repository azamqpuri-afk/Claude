"""
Everything the owner dashboard shows, built from the local store as one JSON-ready dict.
Works for any mill: it reads only the standard entities.
"""

from datetime import date, timedelta

from .. import PRODUCT, audit
from ..reports.production import EFF_AGG
from ..reports.stock import _status_rows
from ..textile import closing, pilferage
from ..textile.method import Method


def _one(con, sql, *p):
    row = con.execute(sql, p).fetchone()
    return row[0] if row and row[0] is not None else 0


def build(con, cfg, as_of=None):
    as_of = as_of or (date.today() - timedelta(days=1))
    d = as_of.isoformat()
    cur = cfg["company"]["currency"]

    # production: last 30 days, and per loom on the day
    start = (as_of - timedelta(days=29)).isoformat()
    prod = {r[0]: r for r in con.execute(f"""
        SELECT prod_date, SUM(meters), {EFF_AGG},
               SUM(COALESCE(rejected_m, 0)), COUNT(DISTINCT loom_code)
        FROM loom_production WHERE prod_date BETWEEN ? AND ? GROUP BY prod_date""", (start, d))}
    days = [(as_of - timedelta(days=29 - i)).isoformat() for i in range(30)]
    production = [{"date": x, "meters": round(prod[x][1] or 0) if x in prod else 0,
                   "eff": round(prod[x][2], 1) if x in prod and prod[x][2] is not None else None} for x in days]
    looms = [{"loom": n, "meters": round(m or 0), "eff": round(e, 1) if e is not None else None,
              "rej": round(100 * rj / m, 1) if m else None, "quality": q}
             for n, m, e, rj, q in con.execute(f"""
        SELECT COALESCE(l.loom_name, p.loom_code), SUM(p.meters),
               {EFF_AGG},
               SUM(COALESCE(p.rejected_m, 0)), GROUP_CONCAT(DISTINCT p.quality)
        FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
        WHERE p.prod_date = ? GROUP BY p.loom_code ORDER BY 1""", (d,))]
    today = prod.get(d)

    # money
    def group_balance(group):
        return _one(con, """SELECT SUM(b.balance) FROM account_balances b JOIN accounts a USING (account_code)
                            WHERE a.account_group = ?""", group)
    months = []
    m0 = date(as_of.year, as_of.month, 1)
    for i in range(11, -1, -1):
        y, m = divmod(m0.year * 12 + m0.month - 1 - i, 12)
        months.append(f"{y:04d}-{m + 1:02d}")
    fin = {r[0]: r for r in con.execute("""
        SELECT strftime('%Y-%m', l.voucher_date),
               -SUM(CASE WHEN a.account_group = 'revenue' THEN l.amount ELSE 0 END),
               SUM(CASE WHEN a.account_group = 'expense' THEN l.amount ELSE 0 END)
        FROM ledger_entries l JOIN accounts a USING (account_code)
        WHERE l.voucher_kind <> 'opening' AND l.voucher_date >= ? AND l.voucher_date <= ? GROUP BY 1""",
        (months[0] + "-01", d))}
    finance = [{"month": mo, "sales": round(fin[mo][1] or 0) if mo in fin else 0,
                "expenses": round(fin[mo][2] or 0) if mo in fin else 0} for mo in months]

    while len(finance) > 1 and not (finance[0]["sales"] or finance[0]["expenses"]):
        finance.pop(0)                                 # months before the data starts

    # attention: the day's audit (nothing is pushed or remembered from here)
    a = audit.run(con, cfg, as_of)
    order = {"ACTION": 0, "CONFIRMED": 1, "WATCH": 2, "INFO": 3}
    findings = [{"severity": f.severity, "section": f.section, "title": f.title, "detail": f.detail}
                for f in sorted(a.findings, key=lambda f: (order[f.severity], f.section))]

    # stock
    items = _status_rows(con, as_of, int(cfg["reports"]["tracked_issue_days"]))
    stock = [{"status": r["status"], "item": r["item_name"], "code": r["item_code"], "unit": r["unit"],
              "stock": r["stock"], "avg_month": round(r["avg_month"], 1)} for r in items if r["status"] != "IN STOCK"]

    # yarn
    crows = closing.contract_rows(con, Method(cfg["closing"]), as_of)
    flags, weekly, sizing, t = pilferage.analyse(con, cfg, as_of - timedelta(days=int(t_look(cfg))), as_of)

    return {
        "product": PRODUCT, "company": cfg["company"]["name"], "currency": cur, "as_of": d,
        "synced_at": _one(con, "SELECT MAX(synced_at) FROM sync_log") or None,
        "kpis": {
            "meters": round(today[1] or 0) if today else 0,
            "eff": round(today[2], 1) if today and today[2] is not None else None,
            "rej": round(100 * today[3] / today[1], 1) if today and today[1] else None,
            "looms": today[4] if today else 0,
            "cash": round(group_balance("cash")), "bank": round(group_balance("bank")),
            "receivable": round(group_balance("customer")), "payable": round(-group_balance("supplier")),
            "sales_month": finance[-1]["sales"], "expenses_month": finance[-1]["expenses"],
            "out_of_stock": sum(1 for r in items if r["status"] == "OUT OF STOCK"),
            "low_stock": sum(1 for r in items if r["status"] == "LOW STOCK"),
            "action": sum(1 for f in findings if f["severity"] == "ACTION"),
            "confirmed": sum(1 for f in findings if f["severity"] == "CONFIRMED"),
            "yarn_flags": len(flags),
        },
        "production": production, "looms": looms, "finance": finance, "findings": findings, "stock": stock,
        "closing": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items() if k != "balance_lb"} for r in crows],
        "pilferage": [{"contract": f["contract"], "party": f["party"], "date": f["date"], "kg": round(f["kg"]),
                       "reasons": f["reasons"]} for f in sorted(flags, key=lambda f: (-len(f["reasons"]), f["date"]))],
        "sizing": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
                   for s in sorted(sizing, key=lambda s: -s["loss_pct"])],
        "sizing_limit": float(t["sizing_loss_pct"]),
    }


def t_look(cfg):
    return {**pilferage.DEFAULTS, **cfg["pilferage"]}["lookback_days"]
