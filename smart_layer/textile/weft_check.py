"""
Scoreboard vs ERP WEFT-yarn check (item J).

The loom scoreboard counts picks (weft insertions). From the picks, the reed width and the weft count we can estimate
the weft yarn the looms actually consumed, and compare it to the weft the ERP issued to those looms (net to_looms
minus from_looms). A gap beyond tolerance means weft was issued but not woven, or woven but not issued - worth a check.

Formula (locked against live contract 74):

    scoreboard weft kg = Σ over loom/shift of [ picks × width_in × panels × 0.0254 × (1 + weft_waste) ]
                                               / (weft_count × ne_m_per_kg)

  - picks: the scoreboard's own pick count (loom_monitor.picks, e.g. Dost RTLSUM PPROD). When the monitor does not
    report picks, they are derived from the monitor's metres and picks/inch: picks = metres × ppi × 39.37.
  - width_in: the contract's PER-PANEL fabric width (inches); the weft traverses the whole reed, so the reed width is
    width_in × panels. panels = the scoreboard pieces for that loom/day, else 1.
  - weft_count: contracts.weft_count (Ne).
  - weft_waste, ne_m_per_kg, tolerance: all in config [weaving], so a denier/tex mill can change the constants.

Generic: it reads only the canonical entities (loom_monitor, loom_production, contracts, yarn_movements); which ERP
columns feed them lives in the mapping, never here.
"""

IN_TO_M = 0.0254
PICKS_PER_M = 39.37          # inches per metre - converts metres woven to picks when the monitor gives no pick count


def _cfg(cfg):
    w = cfg["weaving"]
    return (float(w.get("weft_waste_pct", 3.0)) / 100.0,
            float(w.get("ne_m_per_kg", 1693.3)),
            float(w.get("weft_tolerance_pct", 5.0)))


def _has_picks(con):
    try:
        return any(r[1] == "picks" for r in con.execute("PRAGMA table_info(loom_monitor)"))
    except Exception:
        return False

# ---- attribution: which contract was on a loom on a given date ----------------------------------
# ERP production is keyed in lumps (one per roll cut, every few days), so most days a loom has no ERP row. Attributing
# a scoreboard reading to "the day's dominant ERP contract" therefore drops every gap day. Instead attribute by what
# is physically ON the loom: the beam mounted on it that date (beam_movements to_loom -> from_loom run window), and
# only when no beam run covers the date, carry forward the loom's last ERP production contract on or before the date.


def _beam_runs(con):
    """Per loom: [[start_date, end_date_or_None, contract], ...] from each beam's to_loom -> from_loom, in order."""
    ev = {}
    for loom, cno, mt, dd in con.execute("""SELECT loom_code, contract_no, move_type, move_date FROM beam_movements
            WHERE move_type IN ('to_loom','from_loom_finished','from_loom_cut')
              AND loom_code IS NOT NULL AND move_date IS NOT NULL ORDER BY move_date, rowid"""):
        ev.setdefault(str(loom), []).append((str(dd), mt, (str(cno) if cno is not None else None)))
    runs = {}
    for loom, evs in ev.items():
        lst = []
        for dd, mt, cno in evs:
            if mt == "to_loom":
                lst.append([dd, None, cno])
            else:                                        # from_loom: close the most recent still-open run
                for r in reversed(lst):
                    if r[1] is None:
                        r[1] = dd
                        break
        runs[loom] = lst
    return runs


def _erp_carry(con):
    """Per loom: [(prod_date, dominant contract), ...] in date order, for carrying a contract forward over gap days."""
    best, dom = {}, {}
    for loom, pd, cno, m in con.execute("""SELECT loom_code, prod_date, contract_no, SUM(COALESCE(meters,0))
            FROM loom_production WHERE contract_no IS NOT NULL GROUP BY loom_code, prod_date, contract_no"""):
        k = (str(loom), str(pd))
        if (m or 0) > best.get(k, -1):
            best[k] = m or 0
            dom[k] = str(cno)
    seq = {}
    for (loom, pd), cno in sorted(dom.items()):
        seq.setdefault(loom, []).append((pd, cno))
    return seq


def _resolver(con):
    runs, carry = _beam_runs(con), _erp_carry(con)

    def resolve(loom, d):
        loom, d = str(loom), str(d)
        best = None                                      # the latest-starting beam run that covers this date
        for s, e, c in runs.get(loom, []):
            if c and s <= d and (e is None or d <= e) and (best is None or s > best[0]):
                best = (s, c)
        if best:
            return best[1]
        last = None                                      # else carry forward the last ERP contract on/before the date
        for pd, c in carry.get(loom, []):
            if pd <= d:
                last = c
            else:
                break
        return last
    return resolve


def _ply_fn(cfg):
    """Returns plies(text) -> number of plies read from a yarn name / style. '36/2', '2/40' -> 2; a configured text
    marker (DOUB, DBL, PLY) -> 2; otherwise 1. A 2-ply 36s weighs like an 18s single, so the effective weft count is
    count / plies."""
    import re
    markers = [str(m).upper() for m in (cfg["weaving"].get("weft_ply_markers") or [])]
    small_max = int(cfg["weaving"].get("weft_ply_max_single", 6))

    def plies(text):
        if not text:
            return 1
        t = str(text).upper()
        if any(m and m in t for m in markers):
            return 2
        best = 1
        for a, b in re.findall(r"(\d+)\s*/\s*(\d+)", t):   # "36/2" or "2/40": the small side is the ply
            a, b = int(a), int(b)
            s, big = min(a, b), max(a, b)
            if 2 <= s <= small_max and big >= 10:
                best = max(best, s)
        return best
    return plies


def _contract_plies(con, cfg):
    """Plies per contract, from the contract's weft yarn name (the yarn master, else the weft_yarn field) and quality.
    The scoreboard style of the reading is folded in per row in _attributed."""
    plies = _ply_fn(cfg)
    name_by_code = {}
    try:
        for code, name in con.execute("SELECT yarn_code, yarn_name FROM yarn_movements WHERE yarn_name IS NOT NULL"):
            if code is not None:
                name_by_code.setdefault(str(code), str(name))
    except Exception:
        pass
    try:
        rows = con.execute("SELECT contract_no, weft_yarn, quality FROM contracts").fetchall()
    except Exception:                                    # a store without a weft_yarn column: fall back to quality
        rows = [(c, None, q) for c, q in con.execute("SELECT contract_no, quality FROM contracts")]
    out = {}
    for c, wy, q in rows:
        texts = [wy or "", name_by_code.get(str(wy or ""), ""), q or ""]
        out[str(c)] = max((plies(t) for t in texts), default=1)
    return out, plies


def _attributed(con, cfg):
    """(contract, read_date, kg) per scoreboard reading, attributed by the beam on the loom (carry-forward fallback).
    The weft count is divided by the yarn's plies, and the panels are capped to a sane reed width (a stale 3-panel
    count on a 118" loom would otherwise treble the weft)."""
    waste, ne, _ = _cfg(cfg)
    max_reed = float(cfg["weaving"].get("max_reed_width_in", 150))
    resolve = _resolver(con)
    cplies, plies = _contract_plies(con, cfg)
    cinfo = {str(c): (w, wc, ppi) for c, w, wc, ppi in
             con.execute("SELECT contract_no, width_in, weft_count, picks_per_inch FROM contracts")}
    pk = ", picks" if _has_picks(con) else ", NULL AS picks"
    out = []
    for loom, rd, meters, ppi_m, pieces, style, picks in con.execute(
            f"SELECT loom_code, read_date, COALESCE(meters,0), picks_per_inch, pieces, style{pk} FROM loom_monitor"):
        cno = resolve(loom, rd)
        if not cno or cno not in cinfo:
            continue
        width_in, weft_count, ppi_c = cinfo[cno]
        if not width_in or not weft_count:
            continue
        p = picks if picks is not None else ((meters or 0) * float(ppi_m or ppi_c or 0) * PICKS_PER_M)
        if not p:
            continue
        panels = float(pieces or 1)
        if width_in and panels * float(width_in) > max_reed:   # cap impossible panel counts to the real reed width
            panels = max(1.0, float(int(max_reed / float(width_in))))
        ply = max(cplies.get(cno, 1), plies(style))            # the scoreboard style can also carry the ply (DOUB)
        eff_count = float(weft_count) / float(ply or 1)
        kg = float(p) * float(width_in) * panels * IN_TO_M * (1 + waste) / (eff_count * ne)
        out.append((cno, str(rd), kg))
    return out


def _windows(rows):
    """Per contract from attributed rows: total scoreboard kg and the first/last scoreboard date seen for it."""
    scb, first, last = {}, {}, {}
    for cno, rd, kg in rows:
        scb[cno] = scb.get(cno, 0) + kg
        if cno not in first or rd < first[cno]:
            first[cno] = rd
        if cno not in last or rd > last[cno]:
            last[cno] = rd
    return scb, first, last


def _erp_weft(con, contract, since=None):
    """ERP weft net = Σ to_looms − Σ from_looms (kg). With `since`, only movements on or after that date are counted,
    so the ERP side spans the SAME window as the scoreboard (which only starts when the monitor came online)."""
    def s(mt):
        if since:
            r = con.execute("""SELECT COALESCE(SUM(COALESCE(kg,0)),0) FROM yarn_movements
                    WHERE contract_no=? AND move_type=? AND move_date>=?""", (str(contract), mt, since)).fetchone()
        else:
            r = con.execute("SELECT COALESCE(SUM(COALESCE(kg,0)),0) FROM yarn_movements WHERE contract_no=? AND move_type=?",
                            (str(contract), mt)).fetchone()
        return (r[0] or 0) if r else 0
    return s("to_looms") - s("from_looms")


def _period_key(d, period):
    d = str(d)
    if period == "week":
        try:
            import datetime
            iso = datetime.date.fromisoformat(d[:10]).isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            return d[:10]
    return d[:7]                                          # month


def weft_compare(con, cfg, contract):
    """One contract: scoreboard weft vs ERP weft (counted from the first scoreboard date), the difference and %, and
    whether it is beyond tolerance. no_scoreboard=True when the monitor has no reading for this contract's looms."""
    _, _, tol = _cfg(cfg)
    contract = str(contract)
    scb, first, _ = _windows(_attributed(con, cfg))
    fs = first.get(contract)
    if not fs:
        erp = _erp_weft(con, contract)
        return {"contract": contract, "scoreboard_kg": 0, "erp_kg": round(erp), "diff_kg": round(-erp),
                "pct": None, "tolerance_pct": tol, "flag": False, "no_scoreboard": True, "since": None}
    s = scb.get(contract, 0)
    erp = _erp_weft(con, contract, since=fs)
    diff = s - erp
    pct = (100.0 * diff / erp) if erp else None
    return {"contract": contract, "scoreboard_kg": round(s), "erp_kg": round(erp), "diff_kg": round(diff),
            "pct": (round(pct, 1) if pct is not None else None), "tolerance_pct": tol,
            "flag": bool(pct is not None and abs(pct) > tol), "no_scoreboard": False, "since": fs}


def weft_by_period(con, cfg, contract, period="month"):
    """Per week or month for one contract, over the scoreboard window: scoreboard weft vs ERP weft net issued."""
    _, _, tol = _cfg(cfg)
    contract = str(contract)
    rows = [(rd, kg) for cno, rd, kg in _attributed(con, cfg) if cno == contract]
    if not rows:
        return []
    fs = min(rd for rd, _ in rows)
    scb = {}
    for rd, kg in rows:
        scb[_period_key(rd, period)] = scb.get(_period_key(rd, period), 0) + kg
    erp = {}
    for dd, mt, kg in con.execute("""SELECT move_date, move_type, COALESCE(kg,0) FROM yarn_movements
            WHERE contract_no=? AND move_type IN ('to_looms','from_looms') AND move_date>=?""", (contract, fs)):
        k = _period_key(str(dd), period)
        erp[k] = erp.get(k, 0) + (kg if mt == "to_looms" else -kg)
    out = []
    for k in sorted(set(scb) | set(erp)):
        sc, e = scb.get(k, 0), erp.get(k, 0)
        d = sc - e
        pct = (100.0 * d / e) if e else None
        out.append({"period": k, "scoreboard_kg": round(sc), "erp_kg": round(e), "diff_kg": round(d),
                    "pct": (round(pct, 1) if pct is not None else None),
                    "flag": bool(pct is not None and abs(pct) > tol)})
    return out


def weft_overview(con, cfg):
    """Every contract with scoreboard or ERP weft: the per-contract comparison. Contracts whose looms have scoreboard
    data in the last `weft_active_days` are 'active' and listed first; older ones are marked no_scoreboard so the UI
    can show 'no scoreboard data' instead of a false −100%."""
    _, _, tol = _cfg(cfg)
    active_days = int(cfg["weaving"].get("weft_active_days", 60))
    scb, first, last = _windows(_attributed(con, cfg))
    latest = max(last.values()) if last else None
    import datetime
    cutoff = None
    if latest:
        try:
            cutoff = (datetime.date.fromisoformat(latest[:10]) - datetime.timedelta(days=active_days)).isoformat()
        except Exception:
            cutoff = None
    erp_contracts = {str(c) for (c,) in con.execute(
        "SELECT DISTINCT contract_no FROM yarn_movements WHERE move_type IN ('to_looms','from_looms') AND contract_no IS NOT NULL")}
    names = {str(c): (p or "") for c, p in con.execute("SELECT contract_no, COALESCE(party_name, party_code) FROM contracts")}
    qual = {str(c): (q or "") for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    rows = []
    for cno in sorted(set(scb) | erp_contracts):
        fs = first.get(cno)
        if fs:
            s = scb.get(cno, 0)
            e = _erp_weft(con, cno, since=fs)
            no_scb = False
        else:
            s, e, no_scb = 0, _erp_weft(con, cno), True
        if round(s) == 0 and round(e) == 0:
            continue
        d = s - e
        pct = (100.0 * d / e) if (e and not no_scb) else None
        active = bool(cutoff and last.get(cno) and last[cno] >= cutoff)
        rows.append({"contract": cno, "party": names.get(cno, ""), "quality": qual.get(cno, ""),
                     "scoreboard_kg": round(s), "erp_kg": round(e), "diff_kg": round(d),
                     "pct": (round(pct, 1) if pct is not None else None),
                     "flag": bool(pct is not None and abs(pct) > tol),
                     "no_scoreboard": no_scb, "active": active})
    # active + flagged first, then active, then the rest; within each by the biggest gap
    rows.sort(key=lambda r: (0 if (r["active"] and r["flag"]) else 1 if r["active"] else 2, -abs(r["diff_kg"])))
    return {"rows": rows, "tolerance_pct": tol, "active_days": active_days}
