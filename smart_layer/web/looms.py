"""
Loom efficiency by date, and the live/historic scoreboard view (items 29 & 30a).

Everything here reads data Smart Layer already syncs: ERP production (loom_production) and the loom monitor /
scoreboard (loom_monitor for finished shifts/days, loom_monitor_live for the running shift, loom_stops for the
sensor duplicate-timings check). All generic - no ERP-specific SQL. A mill with no monitor still gets the ERP
columns; the scoreboard columns just read blank.

ERP efficiency uses the same weighting as the daily report (EFF_AGG). Scoreboard efficiency is weighted by shift
time (run-time / shift-time), matching the Overview tile and the Weaving tab. Day vs night on the SCOREBOARD side
needs the monitor to report per shift (loom_monitor.shift); where it only gives a whole-day figure that column is
left blank rather than guessed.
"""

from datetime import date, timedelta

from ..reports.production import EFF_AGG

# scoreboard efficiency = run-time / shift-time across the rows, weighted by shift_sec (falling back to meters)
SCB_EFF = ("SUM(efficiency_pct * COALESCE(shift_sec, meters)) / "
           "NULLIF(SUM(CASE WHEN efficiency_pct IS NOT NULL THEN COALESCE(shift_sec, meters) END), 0)")


def _date_clause(col, d0, d1, dates):
    """(sql_fragment, params) for either a BETWEEN range or an explicit list of ticked dates."""
    if dates:
        ds = [d if isinstance(d, str) else d.isoformat() for d in dates]
        return f"{col} IN ({','.join('?' * len(ds))})", list(ds)
    return f"{col} BETWEEN ? AND ?", [d0.isoformat(), d1.isoformat()]


def _resolve_range(d0, d1, dates):
    if dates:
        ds = sorted(d if isinstance(d, str) else d.isoformat() for d in dates)
        return ds[0], ds[-1]
    return (d0 or (date.today() - timedelta(days=29))).isoformat(), (d1 or date.today()).isoformat()


def efficiency(con, cfg, d0=None, d1=None, dates=None):
    """Per-loom ERP vs scoreboard efficiency over a date range (or a set of ticked dates): efficiency %, metres,
    RPM and stops on each side. Sortable in the UI. Looms are the ones that actually wove or were monitored in the
    window (never the full LOOMS master, never phantom monitor-only channels)."""
    lo, hi = _resolve_range(d0, d1, dates)
    pc, pp = _date_clause("prod_date", d0 or date.fromisoformat(lo), d1 or date.fromisoformat(hi), dates)
    mc, mp = _date_clause("read_date", d0 or date.fromisoformat(lo), d1 or date.fromisoformat(hi), dates)
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    erp = {r[0]: r for r in con.execute(
        f"SELECT loom_code, {EFF_AGG} AS eff, SUM(meters) AS m, MAX(prod_date) AS last, COUNT(DISTINCT prod_date) AS days "
        f"FROM loom_production WHERE {pc} GROUP BY loom_code", pp)}
    scb = {r[0]: r for r in con.execute(
        f"SELECT loom_code, {SCB_EFF} AS eff, SUM(meters) AS m, AVG(rpm) AS rpm, SUM(stops) AS stops "
        f"FROM loom_monitor WHERE {mc} GROUP BY loom_code", mp)}
    std = _standard_rpm(con)                                # loom_code -> standard RPM (latest period), item 30c
    real = set(erp)                                        # real looms = those with ERP production in the window
    monitor_only = sorted(set(scb) - real, key=str)        # monitor channels with no ERP production (shown, flagged)
    rows = []
    for code in sorted(real | set(scb), key=str):
        e, s = erp.get(code), scb.get(code)
        rows.append({
            "loom": names.get(code, code), "code": code,
            "erp_eff": round(e[1], 1) if e and e[1] is not None else None,
            "scb_eff": round(s[1], 1) if s and s[1] is not None else None,
            "meters": round(e[2] or 0) if e else 0,
            "scb_meters": round(s[2] or 0) if s else 0,
            "rpm": round(s[3]) if s and s[3] else None,
            "stops": round(s[4]) if s and s[4] else 0,
            "std_rpm": round(std[code]) if std.get(code) else None,
            "days": e[4] if e else 0, "last": e[3] if e else None,
            "monitor_only": code in set(monitor_only),
        })
    scb_avail = bool(scb)
    shed_erp = con.execute(f"SELECT {EFF_AGG} FROM loom_production WHERE {pc}", pp).fetchone()[0]
    real_only = " AND loom_code IN (SELECT DISTINCT loom_code FROM loom_production)"
    shed_scb = con.execute(f"SELECT {SCB_EFF} FROM loom_monitor WHERE {mc}{real_only}", mp).fetchone()[0] if scb_avail else None
    totals = {"erp_eff": round(shed_erp, 1) if shed_erp is not None else None,
              "scb_eff": round(shed_scb, 1) if shed_scb is not None else None,
              "meters": sum(r["meters"] for r in rows), "scb_meters": sum(r["scb_meters"] for r in rows)}
    return {"from": lo, "to": hi, "dates": (sorted(dates) if dates else None), "rows": rows, "totals": totals,
            "scoreboard_available": scb_avail, "monitor_only": monitor_only}


def efficiency_report(con, cfg, d0=None, d1=None, dates=None):
    """The per-loom efficiency table as a downloadable Report (CSV / PDF / xlsx)."""
    from ..reports import Report
    data = efficiency(con, cfg, d0, d1, dates)
    cols = ["Loom", "ERP eff %", "Scoreboard eff %", "Metres (ERP)", "Metres (scb)", "RPM", "Stops", "Days"]
    rows = [[r["loom"], r["erp_eff"], r["scb_eff"], r["meters"], r["scb_meters"], r["rpm"], r["stops"], r["days"]]
            for r in data["rows"]]
    t = data["totals"]
    span = ("dates " + ", ".join(data["dates"])) if data.get("dates") else f"{data['from']} to {data['to']}"
    summary = [f"Loom efficiency — {span}",
               f"Shed ERP {t['erp_eff'] if t['erp_eff'] is not None else '—'}% · "
               f"scoreboard {t['scb_eff'] if t['scb_eff'] is not None else '—'}% · "
               f"{t['meters']:,} m woven (ERP)"]
    return Report("Loom efficiency", cols, rows, summary)


def _standard_rpm(con):
    """loom_code -> the latest standard RPM from loom_standards (item 30c), or {} when not mapped. The ERP's
    loom-detail table often has only RPM filled (no real target), so this is shown as 'standard RPM', not a target."""
    try:
        out = {}
        for code, rpm in con.execute(
                "SELECT loom_code, standard_rpm FROM loom_standards WHERE standard_rpm IS NOT NULL "
                "ORDER BY from_date"):
            out[code] = rpm                                # later period wins (ordered by from_date)
        return out
    except Exception:
        return {}


def loom_days(con, cfg, code, d0=None, d1=None, dates=None):
    """One loom's day-by-day figures: ERP eff / metres (with a per-shift breakdown where the ERP records shift) and
    the scoreboard eff / metres / RPM / stops (per shift when the monitor reports it, else whole day)."""
    lo, hi = _resolve_range(d0, d1, dates)
    pc, pp = _date_clause("prod_date", d0 or date.fromisoformat(lo), d1 or date.fromisoformat(hi), dates)
    mc, mp = _date_clause("read_date", d0 or date.fromisoformat(lo), d1 or date.fromisoformat(hi), dates)
    name = dict(con.execute("SELECT loom_code, loom_name FROM looms")).get(code, code)
    erp = {}
    for d, sh, eff, m in con.execute(
            f"SELECT prod_date, COALESCE(shift,'-'), {EFF_AGG}, SUM(meters) FROM loom_production "
            f"WHERE loom_code = ? AND {pc} GROUP BY prod_date, shift", [code, *pp]):
        erp.setdefault(d, {"shifts": {}})["shifts"][sh] = {"eff": round(eff, 1) if eff is not None else None, "m": round(m or 0)}
    scb = {}
    for d, sh, eff, m, rpm, st in con.execute(
            f"SELECT read_date, COALESCE(shift,'-'), {SCB_EFF}, SUM(meters), AVG(rpm), SUM(stops) FROM loom_monitor "
            f"WHERE loom_code = ? AND {mc} GROUP BY read_date, shift", [code, *mp]):
        scb.setdefault(d, {})[sh] = {"eff": round(eff, 1) if eff is not None else None, "m": round(m or 0),
                                     "rpm": round(rpm) if rpm else None, "stops": round(st) if st else 0}
    out = []
    for d in sorted(set(erp) | set(scb), reverse=True):
        e, s = erp.get(d, {"shifts": {}}), scb.get(d, {})
        erp_eff = _agg_shift_eff(e["shifts"])
        scb_eff = _agg_shift_eff({k: {"eff": v["eff"], "m": v["m"]} for k, v in s.items()})
        out.append({"date": d, "erp_eff": erp_eff, "erp_m": sum(v["m"] for v in e["shifts"].values()),
                    "erp_shifts": e["shifts"],
                    "scb_eff": scb_eff, "scb_m": sum(v["m"] for v in s.values()),
                    "scb_rpm": next((v["rpm"] for v in s.values() if v.get("rpm")), None),
                    "scb_stops": sum(v["stops"] for v in s.values()), "scb_shifts": s})
    return {"loom": name, "code": code, "from": lo, "to": hi, "rows": out}


def _agg_shift_eff(shifts):
    """Metre-weighted efficiency across a loom's shifts for one day (a simple, robust combine for the drill)."""
    num = den = 0.0
    for v in shifts.values():
        if v.get("eff") is not None and v.get("m"):
            num += v["eff"] * v["m"]; den += v["m"]
    return round(num / den, 1) if den else (next((v["eff"] for v in shifts.values() if v.get("eff") is not None), None))


def scoreboard_date(con, cfg, day):
    """The scoreboard for one past date: per-loom efficiency split by shift where recorded, the mill day/night and
    whole-day totals, and the sensor duplicate-timings verdict for the day and night shifts of that date."""
    day = day or (date.today() - timedelta(days=1))
    ds = day.isoformat()
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    erp_shifts = _shift_set(con, "loom_production", "prod_date", ds)
    scb_shifts = _shift_set(con, "loom_monitor", "read_date", ds)
    looms = []
    erp = {(c, sh): (eff, m) for c, sh, eff, m in con.execute(
        f"SELECT loom_code, COALESCE(shift,'-'), {EFF_AGG}, SUM(meters) FROM loom_production WHERE prod_date = ? GROUP BY loom_code, shift", (ds,))}
    scb = {(c, sh): (eff, m, rpm, st) for c, sh, eff, m, rpm, st in con.execute(
        f"SELECT loom_code, COALESCE(shift,'-'), {SCB_EFF}, SUM(meters), AVG(rpm), SUM(stops) FROM loom_monitor WHERE read_date = ? GROUP BY loom_code, shift", (ds,))}
    codes = sorted({c for (c, _s) in erp} | {c for (c, _s) in scb}, key=str)
    for c in codes:
        row = {"loom": names.get(c, c), "code": c, "erp": {}, "scb": {}}
        for sh in erp_shifts:
            v = erp.get((c, sh))
            if v:
                row["erp"][sh] = {"eff": round(v[0], 1) if v[0] is not None else None, "m": round(v[1] or 0)}
        for sh in scb_shifts:
            v = scb.get((c, sh))
            if v:
                row["scb"][sh] = {"eff": round(v[0], 1) if v[0] is not None else None, "m": round(v[1] or 0),
                                  "rpm": round(v[2]) if v[2] else None, "stops": round(v[3]) if v[3] else 0}
        looms.append(row)
    totals = {"erp": {sh: _shed(con, "loom_production", "prod_date", ds, sh, EFF_AGG) for sh in erp_shifts},
              "scb": {sh: _shed(con, "loom_monitor", "read_date", ds, sh, SCB_EFF, real_only=True) for sh in scb_shifts},
              "erp_all": _shed(con, "loom_production", "prod_date", ds, None, EFF_AGG),
              "scb_all": _shed(con, "loom_monitor", "read_date", ds, None, SCB_EFF, real_only=True)}
    return {"date": ds, "erp_shifts": erp_shifts, "scb_shifts": scb_shifts, "looms": looms, "totals": totals,
            "scoreboard_available": bool(scb), "scb_has_shifts": scb_shifts != ["-"],
            "sensor": _sensor_for_date(con, cfg, day)}


def _shift_set(con, table, datecol, ds):
    rows = [r[0] or "-" for r in con.execute(f"SELECT DISTINCT shift FROM {table} WHERE {datecol} = ? ORDER BY 1", (ds,))]
    return rows or ["-"]


def _shed(con, table, datecol, ds, shift, agg, real_only=False):
    where = f"{datecol} = ?"
    params = [ds]
    if shift is not None:
        where += " AND COALESCE(shift,'-') = ?"; params.append(shift)
    if real_only:
        where += " AND loom_code IN (SELECT DISTINCT loom_code FROM loom_production)"
    v = con.execute(f"SELECT {agg} FROM {table} WHERE {where}", params).fetchone()[0]
    return round(v, 1) if v is not None else None


def trend(con, cfg, d0=None, d1=None):
    """Per-date shed ERP vs scoreboard efficiency over a range (default 30 days) - for the Scoreboard tab's compare
    panel, where the owner ticks which dates to line up."""
    d1 = d1 or (date.today() - timedelta(days=1))
    d0 = d0 or (d1 - timedelta(days=29))
    lo, hi = d0.isoformat(), d1.isoformat()
    erp = dict(con.execute(f"SELECT prod_date, {EFF_AGG} FROM loom_production WHERE prod_date BETWEEN ? AND ? GROUP BY prod_date", (lo, hi)))
    real_only = " AND loom_code IN (SELECT DISTINCT loom_code FROM loom_production)"
    scb = dict(con.execute(f"SELECT read_date, {SCB_EFF} FROM loom_monitor WHERE read_date BETWEEN ? AND ?{real_only} GROUP BY read_date", (lo, hi)))
    days = sorted(set(erp) | set(scb))
    return {"from": lo, "to": hi, "rows": [{"date": d, "erp": round(erp[d], 1) if erp.get(d) is not None else None,
                                            "scb": round(scb[d], 1) if scb.get(d) is not None else None} for d in days]}


def _sensor_for_date(con, cfg, day):
    """Duplicate-timings (sensor-swap) verdict for the day shift (06:00-18:00) and night shift (18:00 d -> 06:00 d+1)
    of a date, from the raw stop log. 'all clear' / 'possible' / 'confirmed' per shift. None when no stop log."""
    if not con.execute("SELECT 1 FROM loom_stops LIMIT 1").fetchone():
        return None
    from ..audit import sensor
    t = sensor.settings(cfg)
    names = dict(con.execute("SELECT loom_code, loom_name FROM looms"))
    nm = lambda c: names.get(c) or names.get(str(sensor._loom_num(c))) or f"loom {c}"   # noqa: E731
    rows = con.execute("""SELECT loom_code, stop_time, restart_time FROM loom_stops
                          WHERE stop_date >= ? AND stop_date <= ? AND stop_time IS NOT NULL AND restart_time IS NOT NULL
                          ORDER BY stop_time""",
                       ((day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat())).fetchall()
    out = {}
    windows = {"day": (f"{day.isoformat()} 06:00:00", f"{day.isoformat()} 18:00:00"),
               "night": (f"{day.isoformat()} 18:00:00", f"{(day + timedelta(days=1)).isoformat()} 06:00:00")}
    for label, (lo, hi) in windows.items():
        sub = [r for r in rows if lo <= str(r[1])[:19] <= hi]
        eps = [e for e in sensor.episodes(sub, t) if e["twins"] >= 3]
        conf = [e for e in eps if e["confirmed"]]
        verdict = "confirmed" if conf else ("possible" if eps else "all clear")
        pairs = [{"a": nm(e["a"]), "b": nm(e["b"]), "twins": e["twins"], "share": round(e["share"], 2)}
                 for e in (conf or eps)[:6]]
        out[label] = {"verdict": verdict, "pairs": pairs}
    return out


def sensor_live(con, cfg):
    """Duplicate-timings over the most recent stop activity (the running shift) - the Scoreboard tab's live panel."""
    if not con.execute("SELECT 1 FROM loom_stops LIMIT 1").fetchone():
        return None
    last = con.execute("SELECT MAX(stop_date) FROM loom_stops").fetchone()[0]
    if not last:
        return None
    return _sensor_for_date(con, cfg, date.fromisoformat(str(last)[:10]))
