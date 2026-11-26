"""
Everything the owner dashboard shows, built from the local store as one JSON-ready dict.
Works for any mill: it reads only the standard entities.
"""

import calendar
import re
from datetime import date, datetime, timedelta

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


_DATE = r"\d{4}-\d{2}-\d{2}"


def _migrate_acks(acked):
    """Map acks saved under the OLD volatile finding keys (which embedded a rolling date / window end / output ratio)
    to the new STABLE keys, so a notification the owner already cleared does not pop back after the upgrade. Best
    effort: an id that does not match a known pattern is kept unchanged."""
    out = {}
    for fid, v in (acked or {}).items():
        if "|" not in str(fid):
            out.setdefault(fid, v)
            continue
        sec, key = str(fid).split("|", 1)
        nk = key
        if key.startswith("lhist-"):                       # lhist-<code>-<date>[-<ratio>] -> lhist-<code>
            nk = re.sub(rf"^(lhist-.+?)-{_DATE}.*$", r"\1", key)
        elif key.startswith("monitor-"):                   # monitor-<k>-<window end> -> monitor-<k>
            nk = re.sub(rf"^(monitor-.+?)-{_DATE}$", r"\1", key)
        elif key.startswith("rej-"):                       # rej-<date>-<loom>-<cn> -> rej-<loom>-<cn>
            m = re.match(rf"^rej-{_DATE}-(.+)$", key)
            if m:
                nk = f"rej-{m.group(1)}"
        else:                                              # nobeam-/eff100-<date>-<loom> -> <type>-<loom>-<date>
            m = re.match(rf"^(nobeam|eff100)-({_DATE})-(.+)$", key)
            if m:
                nk = f"{m.group(1)}-{m.group(3)}-{m.group(2)}"
        out.setdefault(f"{sec}|{nk}", v)
    return out


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


def _weaving(con, cfg, as_of):
    """Loom floor: ERP vs scoreboard efficiency per loom and shed-wide, with a 30-day trend. Looms in
    [monitor] exclude_looms are kept off the shed-wide scoreboard average (they still show on the floor)."""
    d, start = as_of.isoformat(), (as_of - timedelta(days=29)).isoformat()
    excl = _excluded_looms(cfg)
    # match the configured loom NUMBERS to the monitor's own loom codes (which differ by mill: 'L10' vs '10')
    excl_codes = [c for (c,) in con.execute("SELECT DISTINCT loom_code FROM loom_monitor") if _loom_num(c) in excl]
    ex_clause = (" AND loom_code NOT IN (%s)" % ",".join("?" * len(excl_codes))) if excl_codes else ""
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    mstart = as_of.replace(day=1).isoformat()                 # this month (month to date)
    # the ERP posts production a day or two behind, but the scoreboard is live - so cap every scoreboard figure at
    # the ERP's latest production date, so "30 days" and "this month" cover the SAME span for both (item 8).
    _ea = con.execute("SELECT MAX(prod_date) FROM loom_production WHERE prod_date <= ?", (d,)).fetchone()[0]
    scb_hi = _ea or d
    erp = {r[0]: r for r in con.execute(f"""
        SELECT p.loom_code, {EFF_AGG} AS eff, SUM(p.meters) AS m, MAX(p.prod_date) AS last
        FROM loom_production p WHERE p.prod_date BETWEEN ? AND ? GROUP BY p.loom_code""", (start, d))}
    erp_m = {r[0]: r for r in con.execute(f"""
        SELECT p.loom_code, {EFF_AGG} AS eff, SUM(p.meters) AS m
        FROM loom_production p WHERE p.prod_date BETWEEN ? AND ? GROUP BY p.loom_code""", (mstart, d))}
    today_m = dict(con.execute("SELECT loom_code, SUM(meters) FROM loom_production WHERE prod_date = ? GROUP BY loom_code", (d,)))
    # scoreboard efficiency is weighted by SHIFT TIME (shift_sec), not meters - a loom running slow cloth still has
    # a full shift; weighting by meters would understate it. Falls back to meters only if shift_sec is absent.
    scb = {r[0]: r for r in con.execute("""
        SELECT loom_code, SUM(efficiency_pct * COALESCE(shift_sec, meters)) / NULLIF(SUM(COALESCE(shift_sec, meters)), 0) AS eff,
               SUM(meters) AS m, AVG(rpm) AS rpm
        FROM loom_monitor WHERE read_date BETWEEN ? AND ? GROUP BY loom_code""", (start, scb_hi))}
    scb_m = {r[0]: r for r in con.execute("""
        SELECT loom_code, SUM(efficiency_pct * COALESCE(shift_sec, meters)) / NULLIF(SUM(COALESCE(shift_sec, meters)), 0) AS eff,
               SUM(meters) AS m
        FROM loom_monitor WHERE read_date BETWEEN ? AND ? GROUP BY loom_code""", (mstart, scb_hi))}
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
        em, sm = erp_m.get(code), scb_m.get(code)
        looms.append({"loom": names.get(code, code), "code": code, "loom_no": _loom_num(code),
                      "erp_eff": round(e[1], 1) if e and e[1] is not None else None,
                      "scb_eff": round(s[1], 1) if s and s[1] is not None else None,
                      "meters_30d": round(e[2] or 0) if e else 0,
                      "scb_meters_30d": round(s[2] or 0) if s else 0,
                      "erp_eff_month": round(em[1], 1) if em and em[1] is not None else None,
                      "scb_eff_month": round(sm[1], 1) if sm and sm[1] is not None else None,
                      "meters_month": round(em[2] or 0) if em else 0,
                      "scb_meters_month": round(sm[2] or 0) if sm else 0,
                      "last_date": e[3] if e else None})
    # shed-wide 30-day averages
    shed_erp = _one(con, f"SELECT {EFF_AGG} FROM loom_production WHERE prod_date BETWEEN ? AND ?", start, d)
    # drop phantom scoreboard-only looms AND the configured exclude_looms from the shed scoreboard average
    real_only = "AND loom_code IN (SELECT DISTINCT loom_code FROM loom_production)" + ex_clause
    shed_scb = (con.execute(f"""SELECT SUM(efficiency_pct*COALESCE(shift_sec,meters))/NULLIF(SUM(COALESCE(shift_sec,meters)),0)
                            FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only}""", [start, scb_hi, *excl_codes]).fetchone()[0]) if scb_avail else None
    ndays = max((as_of - date.fromisoformat(start)).days + 1, 1)
    ag = con.execute("SELECT SUM(COALESCE(first_quality_m,0)), SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ?", (start, d)).fetchone()
    erp_tot = _one(con, "SELECT SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ?", start, d) or 0
    scb_tot = (con.execute(f"SELECT SUM(meters) FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only}", [start, scb_hi, *excl_codes]).fetchone()[0] or 0) if scb_avail else None
    shed_erp_m = _one(con, f"SELECT {EFF_AGG} FROM loom_production WHERE prod_date BETWEEN ? AND ?", mstart, d)
    shed_scb_m = (con.execute(f"""SELECT SUM(efficiency_pct*COALESCE(shift_sec,meters))/NULLIF(SUM(COALESCE(shift_sec,meters)),0)
                            FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only}""", [mstart, scb_hi, *excl_codes]).fetchone()[0]) if scb_avail else None
    erp_tot_m = _one(con, "SELECT SUM(meters) FROM loom_production WHERE prod_date BETWEEN ? AND ?", mstart, d) or 0
    scb_tot_m = (con.execute(f"SELECT SUM(meters) FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only}", [mstart, scb_hi, *excl_codes]).fetchone()[0] or 0) if scb_avail else None
    shed = {"erp_eff": round(shed_erp, 1) if shed_erp else None,
            "scb_eff": round(shed_scb, 1) if shed_scb else None,
            "a_grade": round(100.0 * ag[0] / ag[1], 1) if ag and ag[1] else None,
            "erp_meters_day": round(erp_tot / ndays), "erp_meters_total": round(erp_tot),
            "scb_meters_day": round(scb_tot / ndays) if scb_avail else None,
            "scb_meters_total": round(scb_tot) if scb_avail else None,
            "erp_eff_month": round(shed_erp_m, 1) if shed_erp_m else None,
            "scb_eff_month": round(shed_scb_m, 1) if shed_scb_m else None,
            "erp_meters_month": round(erp_tot_m), "scb_meters_month": round(scb_tot_m) if scb_avail else None,
            "month": as_of.strftime("%Y-%m")}
    # 30-day efficiency trend, ERP vs scoreboard
    erp_day = dict(con.execute(f"SELECT prod_date, {EFF_AGG} FROM loom_production WHERE prod_date BETWEEN ? AND ? GROUP BY prod_date", (start, d)))
    scb_day = dict(con.execute(f"""SELECT read_date, SUM(efficiency_pct*COALESCE(shift_sec,meters))/NULLIF(SUM(COALESCE(shift_sec,meters)),0)
                                  FROM loom_monitor WHERE read_date BETWEEN ? AND ? {real_only} GROUP BY read_date""", [start, d, *excl_codes])) if scb_avail else {}
    trend = [{"date": (as_of - timedelta(days=29 - i)).isoformat(),
              "erp": round(erp_day[x], 1) if (x := (as_of - timedelta(days=29 - i)).isoformat()) in erp_day and erp_day[x] is not None else None,
              "scb": round(scb_day[x], 1) if x in scb_day and scb_day[x] is not None else None} for i in range(30)]
    # monthly production by grade (ERP), last 12 months, for the stacked A / B / Rejection bar (item AD). Rejection
    # is C+CP only (rejected_m); B grade (second_quality_m) is a saleable second, shown separately, never a reject.
    # The current month is partial ("to date").
    this_month = as_of.strftime("%Y-%m")
    monthly = [{"month": m, "eff": round(e, 1) if e is not None else None, "meters": round(t or 0),
                "a_m": round(a or 0), "b_m": round(b or 0), "rej_m": round(rj or 0),
                "a_grade": round(100.0 * (a or 0) / t, 1) if t else None,
                "b_pct": round(100.0 * (b or 0) / t, 1) if t else None,
                "rej_pct": round(100.0 * (rj or 0) / t, 1) if t else None,
                "current": m == this_month}
               for m, e, a, b, rj, t in con.execute(f"""
        SELECT strftime('%Y-%m', prod_date), {EFF_AGG}, SUM(COALESCE(first_quality_m, 0)),
               SUM(COALESCE(second_quality_m, 0)), SUM(COALESCE(rejected_m, 0)), SUM(meters)
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
    # fabric in stock split by quality (for the Overview fabric-stock drill-down); only lines that net to real stock
    fab_by_quality = sorted(({"quality": (q or "—"), "meters": round(m or 0)}
                             for q, m in con.execute("SELECT quality, SUM(meters) FROM fabric_stock GROUP BY quality")
                             if (m or 0) > 1), key=lambda r: -r["meters"])

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
    # godown tile is driven by the count-wise data (what the ERP shows), not the old kg method: total bags INCLUDE
    # the loose cones (rounded once from cones/bag), and "counts" is the number of distinct yarn counts in stock.
    weft_bags_total = round(sum(r["qty_cones"] / (r["cone_pbag"] or 1) for r in weft))
    sizer_bags_total = round(sum(r["qty_cones"] / (r["cone_pbag"] or 1) for r in sizing))
    weft_counts = len({r["count"] for r in weft})

    return {"yarn_hand_kg": round(hand_kg), "yarn_hand_bags": weft_bags_total,
            "yarn_sizer_kg": round(sizer_kg), "yarn_sizer_bags": sizer_bags_total,
            "yarn_hand_cones": wc, "yarn_sizer_cones": sc,
            "weft_stock": weft, "sizing_stock": sizing,
            "weft_total": {"bags": wb, "cones": wc}, "sizing_total": {"bags": sb, "cones": sc},
            "yarn_counts": weft_counts, "fabric_m": round(fab_total), "fabric_lots": lots,
            "fabric_anomalies": fab_anomalies, "fabric_by_quality": fab_by_quality}


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

    # attention: the day's audit (nothing is pushed or remembered from here). The owner marks an item "read" to clear
    # it. A read item STAYS read even when its figures change (keys are stable), and is only re-shown if it ESCALATES
    # (severity goes up). If it disappears it is remembered for ack_keep_days before being forgotten, so a flicker
    # does not make it pop back but a real recurrence later does. Read items stay viewable under "show all".
    from .. import store as _store
    a = audit.run(con, cfg, as_of)
    order = {"ACTION": 0, "CONFIRMED": 1, "WATCH": 2, "INFO": 3}
    keep_days = int((cfg["reports"] or {}).get("ack_keep_days", 30))
    ack_today = date.today().isoformat()
    # item AH: acks are now one row per finding id (store.acks), so a mark-read POST and this daily housekeeping can
    # no longer clobber each other. We only ever delete/touch INDIVIDUAL ids here - never rewrite the whole set.
    acked = _store.ack_all(con)                            # also migrates the legacy state['acked'] blob once
    all_findings = [{"id": f"{f.section}|{f.key}", "severity": f.severity, "section": f.section,
                     "title": f.title, "detail": f.detail, **({"meta": f.meta} if f.meta else {})}
                    for f in sorted(a.findings, key=lambda f: (order[f.severity], f.section))]
    live_ids = {f["id"] for f in all_findings}
    findings, acknowledged = [], []
    for f in all_findings:
        ack = acked.get(f["id"])
        if not ack:
            findings.append(f)
            continue
        if order[f["severity"]] < order.get(ack.get("severity", f["severity"]), 3):
            _store.ack_delete(con, f["id"])                    # escalated since it was read - show it again as unread
            findings.append(f)
        else:
            if ack.get("last_seen") != ack_today:
                _store.ack_touch(con, f["id"], ack_today)       # still present: reset the forget clock
            acknowledged.append({**f, "read_at": ack.get("at", ""), "read": True})
    for fid, ack in list(acked.items()):                   # forget an ack only after the finding has been gone a while
        if fid in live_ids:
            continue
        last = str(ack.get("last_seen") or ack.get("at") or ack_today)[:10]
        try:
            if (date.fromisoformat(ack_today) - date.fromisoformat(last)).days > keep_days:
                _store.ack_delete(con, fid)
        except ValueError:
            pass

    # stock: the KPIs count only actionable (recently-used) out/low items; the Store tab lists the WHOLE store room
    # (every item with stock or any movement), searchable, with each item's last issue and last receipt.
    from ..reports.stock import _in_stock_index, _similar_in_stock, store_room_rows
    items = _status_rows(con, as_of, int(cfg["reports"]["tracked_issue_days"]))
    pos = _in_stock_index(con)
    stock = [{"status": r["status"], "item": r["item_name"], "code": r["item_code"], "unit": r["unit"],
              "location": r.get("location", ""),
              "stock": round(r["stock"], 2), "avg_month": round(r["avg_month"], 1), "recent_use": r["recent_use"],
              "last_issue": r["last_issue"], "last_receipt": r["last_receipt"],
              "similar": (_similar_in_stock(pos, r["item_code"], r["item_name"]) if r["status"] in ("OUT OF STOCK", "LOW STOCK") else [])}
             for r in store_room_rows(con, cfg, as_of)]

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

    # Sales trend for the Overview: billed revenue (Rs, from the ledger) and dispatched meters, per month.
    disp_by_month = dict(con.execute("""SELECT strftime('%Y-%m', dispatch_date),
                                        SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters)
                                        FROM fabric_dispatches WHERE dispatch_date >= ? AND dispatch_date <= ? GROUP BY 1""",
                                     (months[0] + "-01", d)))
    sales_series = [{"month": f["month"], "sales": f["sales"], "meters": round(disp_by_month.get(f["month"], 0) or 0)}
                    for f in finance]
    # average sales / month over the last 12 (available) months - excludes the current partial month so the
    # average is of whole months only
    # average over the last 12 COMPLETE months (exclude the current partial month). This needs a 13-month window, so
    # it is queried separately from the 12-month chart above.
    yy = as_of.year * 12 + as_of.month - 1
    avg_keys = [f"{(yy - j) // 12:04d}-{(yy - j) % 12 + 1:02d}" for j in range(12, 0, -1)]   # 12 whole months before this one
    # bound by the REAL first ledger month (not the 12-month chart window, which would drop a valid month), so the
    # average covers a full 12 complete months whenever the ledger goes back that far
    first_month = _one(con, "SELECT MIN(voucher_date) FROM ledger_entries WHERE voucher_kind <> 'opening'") or ""
    data_start = str(first_month)[:7] or avg_keys[0]
    keys = [k for k in avg_keys if k >= data_start]            # don't dilute the average with months before go-live
    # invoice value per month (the SAME basis as sales_month and the sales drill/history: the customer-account debit
    # on each sale voucher), so the "avg sales / month" tile compares like with like against this month's sales.
    arev = dict(con.execute("""SELECT strftime('%Y-%m', s.vd), SUM(s.v) FROM (
                       SELECT MIN(l.voucher_date) AS vd, SUM(l.amount) AS v
                       FROM ledger_entries l JOIN accounts a USING (account_code)
                       WHERE l.voucher_kind = 'sale' AND a.account_group = 'customer'
                             AND l.voucher_date >= ? AND l.voucher_date < ?
                       GROUP BY l.voucher_id HAVING SUM(l.amount) > 0) s
                   GROUP BY 1""", (avg_keys[0] + "-01", m_start)))
    adisp = dict(con.execute("""SELECT strftime('%Y-%m', dispatch_date),
                   SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters)
                   FROM fabric_dispatches WHERE dispatch_date >= ? AND dispatch_date < ? GROUP BY 1""",
                   (avg_keys[0] + "-01", m_start)))
    n = max(len(keys), 1)
    avg_sales = round(sum(round(arev.get(k, 0) or 0) for k in keys) / n)
    avg_meters = round(sum(round(adisp.get(k, 0) or 0) for k in keys) / n)
    sales_avg_months = len(keys)

    return {
        "product": PRODUCT, "company": cfg["company"]["name"], "currency": cur, "as_of": d,
        "synced_at": _one(con, "SELECT MAX(synced_at) FROM sync_log") or None,
        "now": datetime.now().isoformat(sep=" ", timespec="seconds"),   # server clock, for the "Live HH:MM" display
        "change_cursor": _change_cursor(con),           # the change_log id to pass back as `since` on the next refresh
        "edits_installed": _edits_installed(con),        # show the Edits tab only when the ERP change-history layer is present
        "purchasing_installed": _purchasing_installed(con),  # show the Indents & POs view only when those entities are mapped
        "dashboard": {k: cfg["dashboard"].get(k) for k in ("animations", "show_amounts", "refresh_seconds", "sound",
                                                            "latitude", "longitude")},
        "sync": _sync_health(con, cfg), "reports_window": int(cfg["reports"]["tracked_issue_days"]),
        "push_failures": _push_failures(con),            # item B1: alerts the outbox could not deliver in the last 24h
        "mapping_warning": _mapping_warning(cfg),         # item S: mill ERP mapping older than the app
        "ui_style": _ui_style(con),                       # item AE: saved look & feel (follows the login across devices)
        "ui_theme": _ui_theme(con),                       # item AE: saved Auto/Light/Dark choice (follows the login)
        "replay": _replay(con, as_of),                    # item AP: today's latest cash + dispatch, to replay once on open
        "kpis": {
            "meters": round(today[1] or 0) if today else 0,
            "dispatched_month": round(disp_month or 0), "woven_month": round(woven_month or 0),
            "eff": round(today[2], 1) if today and today[2] is not None else None,
            "rej": round(100 * today[3] / today[1], 1) if today and today[1] else None,
            "looms": today[4] if today else 0,
            "cash": round(group_balance("cash")), "bank": round(group_balance("bank")),
            "receivable": round(group_balance("customer")), "payable": round(-group_balance("supplier")),
            "sales_month": fin_mod.month_sales(con, as_of=as_of)["total"],   # invoice value, month-to-date = the sales drill
            "expenses_month": finance[-1]["expenses"],
            "sales_avg_12m": avg_sales, "dispatched_avg_12m": avg_meters, "sales_avg_months": sales_avg_months,
            "out_of_stock": sum(1 for r in items if r["status"] == "OUT OF STOCK"),
            "low_stock": sum(1 for r in items if r["status"] == "LOW STOCK"),
            "action": sum(1 for f in findings if f["severity"] == "ACTION"),
            "confirmed": sum(1 for f in findings if f["severity"] == "CONFIRMED"),
            "yarn_flags": len(flags),
            "beam_alerts": sum(1 for b in beam_rows if b["alert"]),
            **_scb_today(con, cfg),                       # live scoreboard efficiency for the current running shift
        },
        "production": production, "looms": looms, "finance": finance, "findings": findings,
        "acknowledged": acknowledged, "stock": stock,
        "closing": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items() if k != "balance_lb"} for r in crows],
        "pilferage": [{"contract": f["contract"], "party": f["party"], "date": f["date"], "kg": round(f["kg"]),
                       "reasons": f["reasons"]} for f in sorted(flags, key=lambda f: (-len(f["reasons"]), f["date"]))],
        "sizing": [{k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
                   for s in sorted(sizing, key=lambda s: -s["loss_pct"])],
        "sizing_limit": float(t["sizing_loss_pct"]),
        "beams": beam_rows, "beam_warn_days": float(bt["warn_days"]),
        "beam_stock": _beam_stock(con, cfg, as_of),
        "charts": {**_chart_series(con, as_of), "sales": sales_series},
        "fin": fin_mod.overview(con),
        "cost": _cost_head(con, cfg, as_of),
        "weaving": _weaving(con, cfg, as_of),
        "yarn_fabric": _yarn_fabric(con, cfg, as_of),
        "gully": _gully_summary(con, cfg, as_of),
    }


def _gully_summary(con, cfg, as_of):
    """A small Overview summary of the gully / rewind book balance (0 when no gully is recorded)."""
    from ..textile import gully
    try:
        bal = gully.rewind_balance(con, cfg, as_of)
    except Exception:
        return {"book_kg": 0, "gully_kg": 0, "yarns": 0}
    t = bal.get("totals", {})
    return {"book_kg": t.get("book", 0), "gully_kg": t.get("gully", 0),
            "reused_kg": t.get("reused", 0), "yarns": len(bal.get("rows", []))}


def _edits_installed(con):
    from .. import erp_edits
    try:
        return erp_edits.installed(con)
    except Exception:
        return False


def _purchasing_installed(con):
    from . import purchasing
    try:
        return purchasing.installed(con)
    except Exception:
        return False


def _change_cursor(con):
    from .. import changes as _changes
    try:
        return _changes.cursor(con)
    except Exception:
        return 0


def changes(con, cfg, since):
    """What changed since the client's last refresh, from the sync-time change_log (see smart_layer/changes.py).
    `since` is the change_log id cursor. Returns {events, cursor}; a blank/absent cursor yields no events."""
    from .. import changes as _changes
    return _changes.feed(con, cfg, since)


def _dmon(d):
    """A YYYY-MM-DD date as '16-Nov' for the feed line."""
    try:
        import datetime
        return datetime.date.fromisoformat(str(d)[:10]).strftime("%d-%b")
    except Exception:
        return str(d)


def _grade_labels(cfg, incl_b):
    gl = (cfg["weaving"].get("grade_labels") or {})
    la, lb, lr = gl.get("a", "A"), gl.get("b", "B"), gl.get("reject", "Rejection")
    if incl_b and lr == "Rejection":                     # when the rejection total already carries B, the remainder is C+CP
        lr = "C+CP"
    return {"a": la, "b": lb, "reject": lr}


def _incl_b(cfg, tot, a, b, r):
    """Does the rejection total already include the B/2nd-quality metres (Total = A + Rejection)? auto per date, or
    forced by [weaving] rejection_includes_second - same rule the contract report uses."""
    mode = cfg["weaving"].get("rejection_includes_second", "auto")
    if mode != "auto":
        return bool(mode)
    tol = max(1.0, 0.01 * tot) if tot else 1.0
    return bool(tot and abs((a + r) - tot) <= tol and r + tol >= b)


def production_summary(con, cfg, pd):
    """One production date's totals for the feed line (item N): metres, efficiency, the grade split and loom count.
    Generic - the grade columns come from first/second/rejected metres and the mapping's labels."""
    from ..reports.production import EFF_AGG
    r = con.execute(f"""SELECT COALESCE(SUM(meters),0), {EFF_AGG}, COALESCE(SUM(first_quality_m),0),
            COALESCE(SUM(second_quality_m),0), COALESCE(SUM(rejected_m),0), COUNT(DISTINCT loom_code)
            FROM loom_production WHERE prod_date = ?""", (str(pd),)).fetchone()
    tot, eff, a, b, rej, looms = (r or (0, None, 0, 0, 0, 0))
    tot, a, b, rej = (tot or 0), (a or 0), (b or 0), (rej or 0)
    incl_b = _incl_b(cfg, tot, a, b, rej)
    ccp = max(0.0, rej - b) if incl_b else None
    rej_pct = (100.0 * rej / tot) if tot else 0.0
    return {"date": str(pd), "meters": round(tot), "eff": (round(eff, 1) if eff is not None else None),
            "a": round(a), "b": round(b), "ccp": (round(ccp) if ccp is not None else None), "rej": round(rej),
            "rej_pct": round(rej_pct, 1), "looms": int(looms or 0),
            "labels": _grade_labels(cfg, incl_b), "incl_b": incl_b}


def production_day(con, cfg, pd):
    """Per-loom production for one date - the feed production line's tap-detail (item N)."""
    from ..reports.production import EFF_AGG
    rows = con.execute(f"""SELECT COALESCE(l.loom_name, p.loom_code) AS loom, MAX(p.contract_no), MAX(p.quality),
            COALESCE(SUM(p.meters),0), {EFF_AGG}, COALESCE(SUM(p.first_quality_m),0),
            COALESCE(SUM(p.second_quality_m),0), COALESCE(SUM(p.rejected_m),0)
            FROM loom_production p LEFT JOIN looms l ON l.loom_code = p.loom_code
            WHERE p.prod_date = ? GROUP BY COALESCE(l.loom_name, p.loom_code)
            ORDER BY SUM(p.meters) DESC""", (str(pd),)).fetchall()
    summ = production_summary(con, cfg, pd)
    out = []
    for loom, cno, q, m, eff, a, b, rej in rows:
        tot = m or 0
        ccp = max(0.0, (rej or 0) - (b or 0)) if summ["incl_b"] else None
        out.append({"loom": loom, "contract": cno or "", "quality": q or "", "meters": round(tot),
                    "eff": (round(eff, 1) if eff is not None else None), "a": round(a or 0), "b": round(b or 0),
                    "ccp": (round(ccp) if ccp is not None else None), "rej": round(rej or 0),
                    "rej_pct": (round(100.0 * (rej or 0) / tot, 1) if tot else 0.0)})
    return {"date": str(pd), "label": _dmon(pd), "rows": out, "summary": summ}


def _push_failures(con):
    """The push outbox's recent failures, for the Overview 'pushes not delivered' warning (item B1)."""
    try:
        from .. import outbox
        rows = outbox.failed_recent(con, hours=24)
        return {"count": len(rows), "items": [{"title": r["title"], "attempts": r["attempts"],
                                               "error": r["last_error"]} for r in rows[:8]]}
    except Exception:
        return {"count": 0, "items": []}


def _ui_style(con):
    """Item AE: the dashboard skin saved for this login (classic / ios / samsung), so it follows across devices."""
    from .. import store as _store
    s = _store.get_state(con, "ui_style", "classic")
    if s == "android":
        s = "samsung"                                     # the old skin id, remapped (One UI, not Material You)
    return s if s in ("classic", "ios", "samsung") else "classic"


def _ui_theme(con):
    """Item AE: the saved Auto / Light / Dark choice for this login. Auto (default) follows sunrise/sunset."""
    from .. import store as _store
    t = _store.get_state(con, "ui_theme", "auto")
    return t if t in ("auto", "light", "dark") else "auto"


def _replay(con, as_of):
    """Item AP: the single latest cash movement and the single latest fabric dispatch/receipt booked TODAY, in the
    shape the dashboard's animations expect, so it can replay them once when it opens (the live ones are easy to
    miss). Each is None when there was no such event today."""
    today = (as_of or date.today()).isoformat()
    out = {}
    try:
        c = con.execute("""SELECT l.amount, COALESCE(a.account_name, l.narration)
                FROM ledger_entries l JOIN accounts a USING (account_code)
                WHERE a.account_group = 'cash' AND l.voucher_date = ? AND l.voucher_kind <> 'opening' AND l.amount <> 0
                ORDER BY l.entered_at DESC, l.rowid DESC LIMIT 1""", (today,)).fetchone()
        if c:
            out["cash"] = {"kind": "cash", "delta": round(c[0]), "label": str(c[1] or "")[:40]}
    except Exception:
        pass
    try:
        d = con.execute("""SELECT fd.direction, fd.meters, COALESCE(p.party_name, fd.deliver_to, fd.party_code), fd.quality
                FROM fabric_dispatches fd LEFT JOIN parties p ON p.party_code = fd.party_code
                WHERE fd.dispatch_date = ? AND COALESCE(fd.meters, 0) > 0
                ORDER BY fd.rowid DESC LIMIT 1""", (today,)).fetchone()
        if d:
            out["truck"] = {"kind": ("in" if d[0] == "in" else "out"), "what": "fabric",
                            "meters": round(d[1] or 0), "party": str(d[2] or "")[:24]}
    except Exception:
        pass
    return out


def _mapping_warning(cfg):
    """Item S: a one-line warning when the mill's ERP mapping was built for an older Smart Layer release."""
    try:
        from .. import __version__, erp
        return erp.Mapping(cfg.path(cfg["erp"]["mapping"])).age_warning(__version__)
    except Exception:
        return None


def feed_day(con, cfg, day=None):
    """The live-feed panel's change_log rows: each with its time, a line and the tap-detail payload, newest first.
    With no explicit day it shows the LATEST day that has entries (which may be before today when the ERP has not
    synced new data yet); the caller labels it with that date. Store issues are grouped per sync by destination."""
    import json
    today = date.today().isoformat()
    if day is not None:
        day = day.isoformat() if hasattr(day, "isoformat") else str(day)
    else:                                                 # no date picked: show the most recent day that has entries
        r = con.execute("SELECT MAX(substr(synced_at,1,10)) FROM change_log WHERE substr(synced_at,1,10) <= ?", (today,)).fetchone()
        day = (r[0] if r and r[0] else today)
    rows = con.execute("""SELECT id, synced_at, kind, payload FROM change_log WHERE substr(synced_at,1,10) = ?
                          ORDER BY id DESC LIMIT 400""", (day,)).fetchall()
    latest_at = str(rows[0][1]) if rows else None         # full timestamp of the newest entry, for "from <date> HH:MM"
    grouped, out, prod = {}, [], {}
    for _id, at, kind, payload in rows:
        try:
            e = json.loads(payload)
        except (ValueError, TypeError):
            e = {}
        t = str(at)[11:16]
        if kind == "store":                               # group store issues of one sync by destination
            key = (at, e.get("to") or "?")
            g = grouped.get(key)
            if g is None:
                g = grouped[key] = {"time": t, "kind": "store", "to": e.get("to") or "?", "items": [], "payload": {}}
                out.append(g)
            g["items"].append(e.get("item"))
            continue
        if kind == "woven":                               # item N: one production line PER production date, not "+X m"
            dates = e.get("dates") or {}
            if dates:
                for d in dates:
                    prod.setdefault(str(d), t)            # rows are newest-first, so the first time seen is the newest
                continue
            # legacy payload with no per-date breakdown: fall back to a plain woven line below
        out.append({"time": t, "kind": kind, "payload": e})
    # item N: one production line per production date that gained metres, computed from the current loom_production
    # (so re-syncs of the same date update the line instead of adding another "+X m"); newest time first overall.
    for pdate, t in prod.items():
        out.append({"time": t, "kind": "production", "payload": production_summary(con, cfg, pdate)})
    out.sort(key=lambda r: r["time"], reverse=True)
    # render a line per row
    def line(r):
        if r["kind"] == "store":
            n = len(r["items"])
            head = ", ".join(x for x in r["items"][:3] if x)
            return f"Store → {r['to']} · {head}" + (f" (+{n - 3} more)" if n > 3 else f" ({n} item{'s' if n != 1 else ''})" if n > 1 else "")
        e = r["payload"]
        if r["kind"] == "cash":
            return f"Cash {('+' if e.get('delta',0)>=0 else '')}{e.get('delta',0):,} {e.get('label','') or ''}".strip()
        if r["kind"] == "production":
            parts = [f"Production {_dmon(e['date'])} entered: {e['meters']:,} m"]
            if e.get("eff") is not None:
                parts.append(f"eff {e['eff']:.1f}%")
            lb = e["labels"]
            g = f"{lb['a']} {e['a']:,} / {lb['b']} {e['b']:,}"
            if e.get("incl_b") and e.get("ccp") is not None:
                g += f" / {lb['reject']} {e['ccp']:,}"
            g += f" (rej {e['rej_pct']:.1f}%)"
            parts.append(g)
            parts.append(f"{e['looms']} loom{'s' if e['looms'] != 1 else ''}")
            return " · ".join(parts)
        if r["kind"] == "woven":
            return f"Woven +{e.get('meters',0):,} m"
        if r["kind"] == "out":
            m = e.get("meters")
            return f"{(e.get('what') or 'goods').title()} out" + (f" · {m:,} m" if m else "") + (f" → {e.get('party')}" if e.get('party') else "")
        if r["kind"] == "in":
            return f"{(e.get('what') or 'goods').title()} in" + (f" · {e.get('bags')} bags" if e.get('bags') else "") + (f" ← {e.get('party')}" if e.get('party') else "")
        if r["kind"] == "alert":
            return "Alert: " + str(e.get("title", ""))
        return r["kind"]
    return {"rows": [{"time": r["time"], "kind": r["kind"], "line": line(r),
                      **({"prod_date": r["payload"].get("date")} if r["kind"] == "production" else {})} for r in out],
            "date": day, "is_today": day == today, "latest_at": latest_at}


def fabric_history(con, cfg, d0=None, d1=None):
    """Fabric detail (#24): per contract/quality - produced (woven), dispatched, returned, rejection, and the
    balance in stock now; dispatch lines; totals on top. In-stock uses the ERP's own fabric_stock so it agrees
    with the fabric-stock report. Date range filters produced/dispatched/returned/rejection."""
    d1 = d1 or date.today()
    d0 = d0 or (d1 - timedelta(days=365))
    lo, hi = d0.isoformat(), d1.isoformat()
    prod = {}
    for cno, q, m, rej in con.execute("""SELECT contract_no, quality, SUM(meters), SUM(COALESCE(rejected_m,0))
            FROM loom_production WHERE prod_date BETWEEN ? AND ? GROUP BY contract_no, quality""", (lo, hi)):
        prod[(cno, q)] = {"produced": round(m or 0), "rejection": round(rej or 0)}
    disp = {}
    for cno, q, out_m, in_m in con.execute("""SELECT contract_no, quality,
            SUM(CASE direction WHEN 'out' THEN meters ELSE 0 END), SUM(CASE direction WHEN 'in' THEN meters ELSE 0 END)
            FROM fabric_dispatches WHERE dispatch_date BETWEEN ? AND ? GROUP BY contract_no, quality""", (lo, hi)):
        disp[(cno, q)] = {"dispatched": round(out_m or 0), "returned": round(in_m or 0)}
    stock = {}
    for cno, q, m in con.execute("SELECT contract_no, quality, SUM(meters) FROM fabric_stock GROUP BY contract_no, quality"):
        stock[(cno, q)] = round(m or 0)
    pname = dict(con.execute("SELECT contract_no, party_name FROM contracts"))
    keys = set(prod) | set(disp) | set(stock)
    rows = []
    for k in keys:
        p = prod.get(k, {}); dd = disp.get(k, {})
        rows.append({"contract": k[0], "quality": k[1], "party": pname.get(k[0]),
                     "produced": p.get("produced", 0), "dispatched": dd.get("dispatched", 0),
                     "returned": dd.get("returned", 0), "rejection": p.get("rejection", 0),
                     "stock": stock.get(k, 0)})
    rows.sort(key=lambda r: -r["stock"])
    lines = [{"date": a, "doc_no": b, "party": c, "contract": d, "meters": round(e or 0), "direction": f}
             for a, b, c, d, e, f in con.execute("""SELECT dispatch_date, doc_no, party_code, contract_no, meters, direction
                 FROM fabric_dispatches WHERE dispatch_date BETWEEN ? AND ? ORDER BY dispatch_date DESC LIMIT 500""", (lo, hi))]
    totals = {"produced": sum(r["produced"] for r in rows), "dispatched": sum(r["dispatched"] for r in rows),
              "returned": sum(r["returned"] for r in rows), "rejection": sum(r["rejection"] for r in rows),
              "stock": sum(r["stock"] for r in rows)}
    return {"totals": totals, "rows": rows, "dispatches": lines, "from": lo, "to": hi}


def dispatches_month(con, cfg, as_of=None):
    """Item AL: THIS MONTH's fabric dispatches - one row per gate pass (date, gate pass, party, quality, meters),
    with the month total. Only outward moves ('out'); returns ('in') are excluded."""
    d1 = as_of or date.today()
    lo = d1.replace(day=1).isoformat()
    hi = d1.isoformat()
    rows = [{"date": a, "doc_no": b, "party": (c or d or ""), "quality": (e or ""), "meters": round(f or 0)}
            for a, b, c, d, e, f in con.execute("""
                SELECT fd.dispatch_date, fd.doc_no, p.party_name, fd.deliver_to, fd.quality, fd.meters
                FROM fabric_dispatches fd LEFT JOIN parties p ON p.party_code = fd.party_code
                WHERE fd.dispatch_date BETWEEN ? AND ? AND fd.direction = 'out'
                ORDER BY fd.dispatch_date DESC, fd.doc_no""", (lo, hi))]
    total = sum(r["meters"] for r in rows)
    return {"rows": rows, "total": total, "month": d1.strftime("%Y-%m"), "from": lo, "to": hi}


def _cost_head(con, cfg, as_of):
    """The cost-per-metre headline (last closed month) for the Overview tile's first paint (#25). Cheap enough to
    embed; the tile taps through to /api/finance/cost for the historic table."""
    try:
        from ..reports import finance as _rf
        return _rf.cost_headline(con, cfg, as_of)
    except Exception:
        return {}


def _loom_num(code):
    """The loom number inside a loom code: '10.0' -> 10 (the monitor returns floats), 'L07' -> 7, '6' -> 6,
    'LOOM-12' -> 12. None if none."""
    s = str(code or "").strip()
    try:
        return int(float(s))                             # '10.0', '10', '6' - a bare number (possibly float)
    except ValueError:
        import re
        m = re.findall(r"\d+", s)                         # 'L07', 'LOOM-12' - a number inside a code
        return int(m[-1]) if m else None


def _excluded_looms(cfg):
    """Loom numbers to leave out of shed / mill scoreboard efficiency ([monitor] exclude_looms)."""
    return {int(_loom_num(x)) for x in (cfg["monitor"].get("exclude_looms") or []) if _loom_num(x) is not None}


def _scb_today(con, cfg):
    """The scoreboard's own efficiency for the CURRENT (running) shift, from loom_monitor_live (the live per-shift
    screen) when it is mapped; otherwise the last finished day from loom_monitor. Efficiency = run time / shift
    time across the running looms. Idle looms (no production, stopped the whole shift) and the configured
    exclude_looms are left out, since they would drag a shed average down the way the physical scoreboard does not.
    A genuinely slow but running loom stays in. Empty when there is no monitor at all."""
    excl = _excluded_looms(cfg)
    auto_idle = bool(cfg["monitor"].get("auto_exclude_idle", True))
    has_live = con.execute("SELECT 1 FROM loom_monitor_live LIMIT 1").fetchone()
    if has_live:
        rows = con.execute("""SELECT loom_code, shift, shift_start, COALESCE(elapsed_sec,0), COALESCE(stopped_sec,0),
                                     COALESCE(picks,0), COALESCE(deliveries,1), rpm, picks_per_inch, updated_at
                              FROM loom_monitor_live""").fetchall()
        run_sec = shift_sec = meters = rpm_sum = rpm_n = 0.0
        idle = kept = 0
        shift = shift_start = updated = None
        for code, sh, sstart, el, st, picks, deliv, rpm, ppi, upd in rows:
            shift, shift_start, updated = sh, sstart, (upd or updated)
            n = _loom_num(code)
            is_idle = (picks or 0) <= 0 and (st or 0) >= (el or 0) - 1    # no production and down the whole shift
            if (n in excl) or (auto_idle and is_idle):
                idle += 1
                continue
            if (el or 0) <= 0:
                continue
            kept += 1
            shift_sec += el
            run_sec += max(0.0, el - (st or 0))
            if ppi and ppi > 0:                           # same width/delivery factor as loom_monitor
                meters += (picks or 0) * (deliv or 1) / (ppi * 39.37)
            if rpm:
                rpm_sum += rpm; rpm_n += 1
        if shift_sec > 0:
            return {"scb_eff_today": round(100 * run_sec / shift_sec, 1), "scb_meters_today": round(meters),
                    "scb_rpm_today": round(rpm_sum / rpm_n) if rpm_n else None,
                    "scb_today_date": (str(shift_start)[:10] if shift_start else None),
                    "scb_live": True, "scb_shift": shift, "scb_shift_from": (str(shift_start)[11:16] if shift_start else None),
                    "scb_updated": (str(updated)[11:16] if updated else None), "scb_idle_excluded": idle, "scb_looms": kept}
    # fallback: the last finished day from loom_monitor (RTLSUM), excluding the configured looms
    last = con.execute("SELECT MAX(read_date) FROM loom_monitor").fetchone()[0]
    if not last:
        return {}
    ex_sql, params = "", [last]
    if excl:
        ex_sql = " AND loom_code NOT IN (%s)" % ",".join("?" * len(excl))
        params += ["L%02d" % n for n in excl]            # match the common 'Lnn' coding; raw-number looms fall through
    eff, m, rpm = con.execute(f"""SELECT SUM(efficiency_pct*COALESCE(shift_sec,meters))/NULLIF(SUM(COALESCE(shift_sec,meters)),0),
                                 SUM(meters), AVG(rpm) FROM loom_monitor WHERE read_date = ?{ex_sql}""", params).fetchone()
    return {"scb_eff_today": round(eff, 1) if eff is not None else None, "scb_meters_today": round(m or 0),
            "scb_rpm_today": round(rpm) if rpm else None, "scb_today_date": last, "scb_live": False}


def _sync_health(con, cfg):
    """Freshness of the data for the dashboard header: when the live sync and the daily sync last ran, and whether
    either is overdue. The dashboard shows 'Last synced ...' in red when stale, so a stuck Task Scheduler (the
    tasks silently stop) is noticed at a glance instead of by chance."""
    from .. import store as _store
    rows = dict(con.execute("SELECT entity, MAX(synced_at) FROM sync_log GROUP BY entity"))
    live_at = max((rows[e] for e in _store.LIVE_ENTITIES if rows.get(e)), default=None)
    # the FULL (daily) sync's freshness = the newest sync of an entity the live sync does NOT touch (ledgers,
    # attendance, loans ...), so a running live sync never masks a stalled daily one on the header.
    daily_at = max((v for e, v in rows.items() if e not in _store.LIVE_ENTITIES and v), default=None)
    s = cfg["sync"]
    live_limit = int(s.get("stale_live_minutes", 30))
    daily_limit = int(s.get("stale_daily_hours", 26))

    def _age_min(ts):
        if not ts:
            return None
        try:
            return (datetime.now() - datetime.fromisoformat(str(ts)[:19])).total_seconds() / 60
        except ValueError:
            return None
    live_age, daily_age = _age_min(live_at), _age_min(daily_at)
    live_stale = live_age is None or live_age > live_limit
    daily_stale = daily_age is None or daily_age > daily_limit * 60
    return {"live_at": live_at, "daily_at": daily_at, "live_stale": live_stale, "daily_stale": daily_stale,
            "live_limit_min": live_limit, "daily_limit_h": daily_limit,
            "stale": bool(live_stale or daily_stale)}


def _beam_stock(con, cfg, as_of=None):
    """Counts of beams by where they are now - for the Weaving tab's beam-stock panel.
    on_loom / sized_waiting come from the beam snapshot. 'at sizer' is read from the ERP's own empty-beam-stock
    view (positive balances only). 'empty in mill' = a beam that came OFF a loom (finished or cut) and since then
    has NOT gone to a sizer and has NOT been re-mounted - collapsed by beam aliases, past an ignore list, and aged
    out after [beams].empty_age_out_days so decade-old stale numbers do not pile up."""
    from ..textile.beams import settings
    t = settings(cfg)
    as_of = as_of or date.today()
    counts = dict(con.execute("SELECT status, COUNT(*) FROM beams GROUP BY status"))
    at_sizer = con.execute("SELECT COALESCE(SUM(CASE WHEN beams > 0 THEN beams ELSE 0 END), 0) FROM empty_beam_stock").fetchone()[0] or 0
    alias = {str(k): str(v) for k, v in (t.get("beam_aliases") or {}).items()}
    ignore = {str(b) for b in (t.get("ignore_beams") or [])}
    floor = (as_of - timedelta(days=int(t.get("empty_age_out_days", 120)))).isoformat()
    # the beam snapshot (WV_BEAM_ATLOOM_V / BEAMS_STOCK_V) is authoritative for where a beam IS now: a beam it shows
    # on a loom or sized-and-waiting is not empty, whatever its movement dates say (an old off-loom move can be keyed
    # after a re-mount).
    held = {alias.get(str(b), str(b)) for (b,) in con.execute(
        "SELECT beam_no FROM beams WHERE beam_no IS NOT NULL AND status IN ('on_loom', 'sized_waiting')")}
    latest = {}                                          # beam -> {move_type: latest date}
    for beam, mt, dd in con.execute("""SELECT beam_no, move_type, move_date FROM beam_movements
            WHERE beam_no IS NOT NULL AND move_type IN ('to_loom', 'from_loom_finished', 'from_loom_cut', 'empty_to_sizer')
            ORDER BY move_date, rowid"""):
        latest.setdefault(alias.get(beam, beam), {})[mt] = dd    # ordered: last date per type wins
    empty = 0
    for b, mv in latest.items():
        if b in ignore or b in held:
            continue
        off = max(mv.get("from_loom_finished", ""), mv.get("from_loom_cut", ""))
        # came off a loom, not sent to a sizer since (a tie on the same day means it went to the sizer), not
        # re-mounted since, and recent enough not to be a stale number from years ago
        if off and off > mv.get("empty_to_sizer", "") and off > mv.get("to_loom", "") and off >= floor:
            empty += 1
    return {"on_loom": counts.get("on_loom", 0), "sized_waiting": counts.get("sized_waiting", 0),
            "empty_in_mill": empty or counts.get("empty_in_mill", 0),
            "at_sizer": round(at_sizer),
            "sent_out": counts.get("sent_out", 0)}


def sized_beam_list(con, cfg, as_of=None):
    """Each sized beam waiting for a loom (status sized_waiting) - the Weaving 'Sized, waiting' tile drill-down (item L).
    beams.sizer_name / received_date are often NULL on live, so the sizer and received date are taken from sized_sets
    via set_no (sizer_name, set_date); days waiting = today - received date; quality comes from the contract."""
    as_of = as_of or date.today()
    ss = {}
    for sno, sd, sz in con.execute("SELECT set_no, set_date, sizer_name FROM sized_sets ORDER BY rowid"):
        if sno is not None:
            ss.setdefault(str(sno), (sd, sz))            # first (earliest) row per set wins
    ql = {str(c): q for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    rows = []
    for beam, sno, cno, ends, slen, sizer, recv in con.execute(
            """SELECT beam_no, set_no, contract_no, ends, set_length_m, sizer_name, received_date
               FROM beams WHERE status = 'sized_waiting' ORDER BY set_no, beam_no"""):
        sd, sz = ss.get(str(sno), (None, None))
        recv = recv or sd                                # the snapshot's received date, else the set's sizing date
        sizer = sizer or sz or "—"
        days = None
        if recv:
            try:
                days = (as_of - date.fromisoformat(str(recv)[:10])).days
            except Exception:
                days = None
        rows.append({"set_no": sno or "—", "beam_no": beam or "—", "contract": cno or "—",
                     "ends": round(ends or 0), "quality": ql.get(str(cno), "") or "—",
                     "set_length_m": round(slen or 0), "sizer": sizer,
                     "received": (str(recv)[:10] if recv else "—"),
                     "days_waiting": (days if days is not None else "—")})
    total_len = sum(r["set_length_m"] for r in rows if isinstance(r["set_length_m"], (int, float)))
    return {"rows": rows, "beams": len(rows), "total_length_m": round(total_len)}


def t_look(cfg):
    return {**pilferage.DEFAULTS, **cfg["pilferage"]}["lookback_days"]
