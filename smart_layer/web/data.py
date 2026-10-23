"""
Everything the owner dashboard shows, built from the local store as one JSON-ready dict.
Works for any mill: it reads only the standard entities.
"""

import calendar
from datetime import date, timedelta

from .. import PRODUCT, audit
from ..reports.production import EFF_AGG
from ..reports.stock import _status_rows
from ..textile import beams as beams_mod
from ..textile import closing, pilferage
from ..textile.method import Method
from . import finance as fin_mod


def _one(con, sql, *p):
    row = con.execute(sql, p).fetchone()
    return row[0] if row and row[0] is not None else 0


def _month_ends(as_of, n=12):
    out = []
    for i in range(n - 1, -1, -1):
        yy, mm = divmod(as_of.year * 12 + as_of.month - 1 - i, 12)
        mm += 1
        out.append(date(yy, mm, calendar.monthrange(yy, mm)[1]))
    return out


def _chart_series(con, as_of):
    """Extra trend series for the Overview: receivables, cash & bank, fabric stock."""
    mes = _month_ends(as_of, 12)
    def _grp_to(group, me, sign=1):
        return round(sign * (_one(con, """SELECT SUM(l.amount) FROM ledger_entries l JOIN accounts a USING (account_code)
                                          WHERE a.account_group = ? AND l.voucher_date <= ?""", group, me.isoformat()) or 0))
    # receivables (what parties owe us) and payables (what we owe suppliers) on one chart, like Sales vs Expenses
    receivables = [{"month": me.strftime("%Y-%m"), "amount": _grp_to("customer", me),
                    "payable": _grp_to("supplier", me, -1)} for me in mes]
    # Fabric stock trend = the ERP's own month-end figure, captured as-of each month-end at sync time
    # (fabric_stock_history). We show exactly what the ERP's stock view says; nothing is recomputed here.
    hist = dict(con.execute("SELECT month, meters FROM fabric_stock_history"))
    fabric_stock = [{"month": me.strftime("%Y-%m"), "meters": round(hist[me.strftime("%Y-%m")])}
                    for me in mes if me.strftime("%Y-%m") in hist]
    # cash & bank: a running daily balance over the last 30 days
    start = as_of - timedelta(days=29)

    def _grp_daily(group):
        base = _one(con, """SELECT SUM(l.amount) FROM ledger_entries l JOIN accounts a USING (account_code)
                            WHERE a.account_group = ? AND l.voucher_date < ?""", group, start.isoformat())
        delta = dict(con.execute("""SELECT l.voucher_date, SUM(l.amount) FROM ledger_entries l JOIN accounts a USING (account_code)
                                    WHERE a.account_group = ? AND l.voucher_date >= ? AND l.voucher_date <= ? GROUP BY 1""",
                                 (group, start.isoformat(), as_of.isoformat())))
        run, out = base or 0, {}
        for i in range(30):
            day = (start + timedelta(days=i)).isoformat()
            run += delta.get(day, 0) or 0
            out[day] = round(run)
        return out

    cash_d, bank_d = _grp_daily("cash"), _grp_daily("bank")
    cash_bank = [{"date": (start + timedelta(days=i)).isoformat(),
                  "cash": cash_d[(start + timedelta(days=i)).isoformat()],
                  "bank": bank_d[(start + timedelta(days=i)).isoformat()]} for i in range(30)]
    return {"receivables": receivables, "fabric_stock": fabric_stock, "cash_bank": cash_bank}


def _weaving(con, as_of):
    """Loom floor: ERP vs scoreboard efficiency per loom and shed-wide, with a 30-day trend."""
    d, start = as_of.isoformat(), (as_of - timedelta(days=29)).isoformat()
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    erp = {r[0]: r for r in con.execute(f"""
        SELECT p.loom_code, {EFF_AGG} AS eff, SUM(p.meters) AS m, MAX(p.prod_date) AS last
        FROM loom_production p WHERE p.prod_date BETWEEN ? AND ? GROUP BY p.loom_code""", (start, d))}
    today_m = dict(con.execute("SELECT loom_code, SUM(meters) FROM loom_production WHERE prod_date = ? GROUP BY loom_code", (d,)))
    scb = {r[0]: r for r in con.execute("""
        SELECT loom_code, SUM(efficiency_pct * meters) / NULLIF(SUM(meters), 0) AS eff,
               SUM(meters) AS m, AVG(rpm) AS rpm
        FROM loom_monitor WHERE read_date BETWEEN ? AND ? GROUP BY loom_code""", (start, d))}
    scb_avail = bool(scb)
    # The real looms are the ones that have ever had ERP production - NOT the LOOMS master (full of unused rows) and
    # NOT the scoreboard (which has sensor channels for looms that do not physically exist). Scoreboard data is
    # overlaid only where it matches a real loom, so phantom scoreboard-only machines never appear on the floor.
    real = {r[0] for r in con.execute("SELECT DISTINCT loom_code FROM loom_production")}
    looms = []
    for code in sorted(real, key=str):
        e, s = erp.get(code), scb.get(code)
        # the floor shows 30-day average efficiency only (no running/idle/stopped - the ERP posts production in
        # ~3-day batches, so "running today" is not a reliable signal; the 30-day average is).
        looms.append({"loom": names.get(code, code), "code": code,
                      "erp_eff": round(e[1], 1) if e and e[1] is not None else None,
                      "scb_eff": round(s[1], 1) if s and s[1] is not None else None,
                      "meters_30d": round(e[2] or 0) if e else 0,
                      "scb_meters_30d": round(s[2] or 0) if s else 0,
                      "last_date": e[3] if e else None})
    # shed-wide 30-day averages
    shed_erp = _one(con, f"SELECT {EFF_AGG} FROM loom_production WHERE prod_date BETWEEN ? AND ?", start, d)
    real_only = "AND loom_code IN (SELECT DISTINCT loom_code FROM loom_production)"   # drop phantom scoreboard-only looms
    shed_scb = _one(con, f"""SELECT SUM(efficiency_pct*meters)/NULLIF(SUM(meters),0)
                            FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only}""", start, d) if scb_avail else None
    ndays = max((as_of - date.fromisoformat(start)).days + 1, 1)
    ag = con.execute("SELECT SUM(COALESCE(first_quality_m,0)), SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ?", (start, d)).fetchone()
    erp_tot = _one(con, "SELECT SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ?", start, d) or 0
    scb_tot = (_one(con, f"SELECT SUM(meters) FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only}", start, d) or 0) if scb_avail else None
    shed = {"erp_eff": round(shed_erp, 1) if shed_erp else None,
            "scb_eff": round(shed_scb, 1) if shed_scb else None,
            "a_grade": round(100.0 * ag[0] / ag[1], 1) if ag and ag[1] else None,
            "erp_meters_day": round(erp_tot / ndays), "erp_meters_total": round(erp_tot),
            "scb_meters_day": round(scb_tot / ndays) if scb_avail else None,
            "scb_meters_total": round(scb_tot) if scb_avail else None}
    # 30-day efficiency trend, ERP vs scoreboard
    erp_day = dict(con.execute(f"SELECT prod_date, {EFF_AGG} FROM loom_production WHERE prod_date BETWEEN ? AND ? GROUP BY prod_date", (start, d)))
    scb_day = dict(con.execute(f"""SELECT read_date, SUM(efficiency_pct*meters)/NULLIF(SUM(meters),0)
                                  FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only} GROUP BY read_date""", (start, d))) if scb_avail else {}
    trend = [{"date": (as_of - timedelta(days=29 - i)).isoformat(),
              "erp": round(erp_day[x], 1) if (x := (as_of - timedelta(days=29 - i)).isoformat()) in erp_day and erp_day[x] is not None else None,
              "scb": round(scb_day[x], 1) if x in scb_day and scb_day[x] is not None else None} for i in range(30)]
    # monthly efficiency & A-grade (ERP), last 12 months
    monthly = [{"month": m, "eff": round(e, 1) if e is not None else None,
                "a_grade": round(100.0 * (a or 0) / t, 1) if t else None}
               for m, e, a, t in con.execute(f"""
        SELECT strftime('%Y-%m', prod_date), {EFF_AGG}, SUM(COALESCE(first_quality_m, 0)), SUM(meters)
        FROM loom_production WHERE prod_date >= ? GROUP BY 1 ORDER BY 1""", (_month_ends(as_of, 12)[0].replace(day=1).isoformat(),))]
    return {"scoreboard_available": scb_avail, "shed": shed, "looms": looms, "trend": trend, "monthly": monthly}


def _yarn_fabric(con, cfg, as_of):
    """Headline yarn and fabric stock tiles for the Yarn & Fabric tab."""
    from ..textile.stock_position import fabric_positions, yarn_positions
    hand, sizer, _ = yarn_positions(con, cfg, as_of)
    bag_kg = float(Method(cfg["closing"]).cfg["bag_lb"]) * 0.45359237
    # net the positions (over-issued contracts net against the rest) so the godown figure matches reality,
    # not the sum of only the positive contracts (which over-counts).
    hand_kg = max(sum(hand.values()), 0)
    sizer_kg = max(sum(sizer.values()), 0)
    by_count = {}
    for (_party, cnt), v in hand.items():
        by_count[cnt] = by_count.get(cnt, 0) + v
    counts = sum(1 for v in by_count.values() if v > 1)
    # Fabric in stock = read the ERP's own fabric-stock figure as-is (fabric_stock entity reads WV_FAB_STOCK_V):
    # the net total is exactly what the ERP report shows. We only FLAG negative lines, never recompute the total.
    frows = con.execute("SELECT meters FROM fabric_stock").fetchall()
    fab_total = sum((r[0] or 0) for r in frows)
    fab_anomalies = sum(1 for r in frows if (r[0] or 0) < -1)
    lots = sum(1 for r in frows if (r[0] or 0) > 1)

    # Count-wise loose-yarn stock, exactly as the mill's "Weft Stock Detail" / "Yarn Stock At Sizing" report:
    # one row per count + brand + packing, net total cones split back into bags and loose cones (sign preserved).
    def split(qty, cpb):
        cpb = cpb or 1
        b = int(qty / cpb)                 # TRUNC toward zero, like the report's CF_QTYFORMULA
        return round(b), round(qty - b * cpb)

    # counts that are the same yarn commercially can be shown as one line (config [yarn].combine_counts, each entry
    # [label, count, count, ...]) - e.g. ["52/1 & 54/1 CTN", "52/1 CTN", "54/1 CTN"]. Totals are summed, never recomputed.
    combine = {}
    for grp in cfg["yarn"].get("combine_counts", []):
        if isinstance(grp, (list, tuple)) and len(grp) >= 2:
            for c in grp[1:]:
                combine[c] = grp[0]
    agg_rows = {}                                        # (section, party, count, brand, cone_pbag) -> cones
    for section, party, cnt, brand, cpb, qty in con.execute(
            'SELECT section, party_name, "count" AS c, brand, cone_pbag, qty_cones FROM yarn_stock'):
        cnt = combine.get(cnt, cnt)
        key = (section, (party or "—") if section == "sizing" else None, cnt, brand, round(cpb or 0))
        agg_rows[key] = agg_rows.get(key, 0.0) + (qty or 0)
    weft, sizing = [], []
    wb = wc = sb = sc = 0
    for (section, party, cnt, brand, cpb), qty in agg_rows.items():
        b, c = split(qty, cpb)
        row = {"count": cnt, "brand": brand, "cone_pbag": cpb, "bags": b, "cones": c, "qty_cones": round(qty)}
        if section == "sizing":
            row["party"] = party
            sizing.append(row); sb += b; sc += c
        else:
            weft.append(row); wb += b; wc += c
    weft.sort(key=lambda r: (str(r["count"]), r["cone_pbag"], str(r["brand"])))
    sizing.sort(key=lambda r: (str(r.get("party") or ""), str(r["count"]), r["cone_pbag"]))

    return {"yarn_hand_kg": round(hand_kg), "yarn_hand_bags": wb,
            "yarn_sizer_kg": round(sizer_kg), "yarn_sizer_bags": sb,
            "yarn_hand_cones": wc, "yarn_sizer_cones": sc,
            "weft_stock": weft, "sizing_stock": sizing,
            "weft_total": {"bags": wb, "cones": wc}, "sizing_total": {"bags": sb, "cones": sc},
            "yarn_counts": counts, "fabric_m": round(fab_total), "fabric_lots": lots,
            "fabric_anomalies": fab_anomalies}


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
    from ..reports.stock import _in_stock_index, _similar_in_stock
    items = _status_rows(con, as_of, int(cfg["reports"]["tracked_issue_days"]))
    pos = _in_stock_index(con)
    stock = [{"status": r["status"], "item": r["item_name"], "code": r["item_code"], "unit": r["unit"],
              "stock": r["stock"], "avg_month": round(r["avg_month"], 1),
              "similar": _similar_in_stock(pos, r["item_code"], r["item_name"])}
             for r in items if r["status"] != "IN STOCK"]

    # yarn
    crows = closing.contract_rows(con, Method(cfg["closing"]), as_of)
    flags, weekly, sizing, t = pilferage.analyse(con, cfg, as_of - timedelta(days=int(t_look(cfg))), as_of)

    bt = beams_mod.settings(cfg)
    quality = {c: q for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    beam_rows = [{"loom": r["loom_name"], "beam": r["beam"], "contract": r["contract"],
                  "quality": quality.get(r["contract"]),
                  "days": round(r["days"], 1) if r["days"] is not None else None,
                  "to_come": round(r["to_come"]) if r["to_come"] is not None else None, "rate": round(r["rate"]),
                  "backup": (r["backup"] or {}).get("beam"), "over": r["over"],
                  "alert": bool(r["days"] is not None and r["days"] <= float(bt["warn_days"]) and not r["backup"] and not r["over"])}
                 for r in beams_mod.beams_on_looms(con, cfg, as_of)]

    m_start = date(as_of.year, as_of.month, 1).isoformat()
    disp_month = _one(con, """SELECT SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters)
                              FROM fabric_dispatches WHERE dispatch_date BETWEEN ? AND ?""", m_start, d)
    woven_month = _one(con, "SELECT SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ?", m_start, d)

    return {
        "product": PRODUCT, "company": cfg["company"]["name"], "currency": cur, "as_of": d,
        "synced_at": _one(con, "SELECT MAX(synced_at) FROM sync_log") or None,
        "kpis": {
            "meters": round(today[1] or 0) if today else 0,
            "dispatched_month": round(disp_month or 0), "woven_month": round(woven_month or 0),
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
            "beam_alerts": sum(1 for b in beam_rows if b["alert"]),
        },
        "production": production, "looms": looms, "finance": finance, "findings": findings, "stock": stock,
        "closing": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items() if k != "balance_lb"} for r in crows],
        "pilferage": [{"contract": f["contract"], "party": f["party"], "date": f["date"], "kg": round(f["kg"]),
                       "reasons": f["reasons"]} for f in sorted(flags, key=lambda f: (-len(f["reasons"]), f["date"]))],
        "sizing": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
                   for s in sorted(sizing, key=lambda s: -s["loss_pct"])],
        "sizing_limit": float(t["sizing_loss_pct"]),
        "beams": beam_rows, "beam_warn_days": float(bt["warn_days"]),
        "beam_stock": _beam_stock(con),
        "charts": _chart_series(con, as_of),
        "fin": fin_mod.overview(con),
        "weaving": _weaving(con, as_of),
        "yarn_fabric": _yarn_fabric(con, cfg, as_of),
    }


def _beam_stock(con):
    """Counts of beams by where they are now - for the Weaving tab's beam-stock panel."""
    counts = dict(con.execute("SELECT status, COUNT(*) FROM beams GROUP BY status"))
    # empty beams at sizers aren't in the beams snapshot - read them from the movements (sent out minus come back)
    at_sizer = con.execute("""SELECT SUM(CASE move_type WHEN 'empty_to_sizer' THEN 1
                                             WHEN 'empty_from_sizer' THEN -1 ELSE 0 END) FROM beam_movements""").fetchone()[0] or 0
    return {"on_loom": counts.get("on_loom", 0), "sized_waiting": counts.get("sized_waiting", 0),
            "empty_in_mill": counts.get("empty_in_mill", 0),
            "at_sizer": max(round(at_sizer), 0) or counts.get("at_sizer", 0),
            "sent_out": counts.get("sent_out", 0)}


def t_look(cfg):
    return {**pilferage.DEFAULTS, **cfg["pilferage"]}["lookback_days"]
