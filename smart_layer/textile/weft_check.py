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


def _dominant_contract(con):
    """(loom_code, prod_date) -> the contract that wove the most metres on that loom that day, so a scoreboard reading
    can be attributed to one contract (a loom usually runs one contract a day)."""
    best, dom = {}, {}
    for loom, pd, cno, m in con.execute("""SELECT loom_code, prod_date, contract_no, SUM(COALESCE(meters,0))
            FROM loom_production WHERE contract_no IS NOT NULL GROUP BY loom_code, prod_date, contract_no"""):
        k = (str(loom), str(pd))
        if (m or 0) > best.get(k, -1):
            best[k] = m or 0
            dom[k] = str(cno)
    return dom


def _rows(con, cfg):
    """One scoreboard-weft figure per (contract, read_date): (contract, read_date, kg). The read_date lets it roll up
    by week or month; the contract comes from the loom's dominant contract that day."""
    waste, ne, _ = _cfg(cfg)
    dom = _dominant_contract(con)
    cinfo = {str(c): (w, wc, ppi) for c, w, wc, ppi in
             con.execute("SELECT contract_no, width_in, weft_count, picks_per_inch FROM contracts")}
    pk = ", picks" if _has_picks(con) else ", NULL AS picks"
    out = []
    for loom, rd, meters, ppi_m, pieces, picks in con.execute(
            f"SELECT loom_code, read_date, COALESCE(meters,0), picks_per_inch, pieces{pk} FROM loom_monitor"):
        cno = dom.get((str(loom), str(rd)))
        if not cno or cno not in cinfo:
            continue
        width_in, weft_count, ppi_c = cinfo[cno]
        if not width_in or not weft_count:
            continue
        p = picks if picks is not None else ((meters or 0) * float(ppi_m or ppi_c or 0) * PICKS_PER_M)
        if not p:
            continue
        panels = float(pieces or 1)
        weft_len = float(p) * float(width_in) * panels * IN_TO_M * (1 + waste)
        kg = weft_len / (float(weft_count) * ne)
        out.append((cno, str(rd), kg))
    return out


def _erp_weft(con, contract):
    """ERP weft issued to the looms, net = Σ to_looms − Σ from_looms (kg) for the contract."""
    def s(mt):
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
    """One contract: scoreboard weft vs ERP weft, the difference and %, and whether it is beyond tolerance."""
    _, _, tol = _cfg(cfg)
    scb = sum(kg for cno, _, kg in _rows(con, cfg) if cno == str(contract))
    erp = _erp_weft(con, contract)
    diff = scb - erp
    pct = (100.0 * diff / erp) if erp else None
    return {"contract": str(contract), "scoreboard_kg": round(scb), "erp_kg": round(erp),
            "diff_kg": round(diff), "pct": (round(pct, 1) if pct is not None else None),
            "tolerance_pct": tol, "flag": bool(pct is not None and abs(pct) > tol)}


def weft_by_period(con, cfg, contract, period="month"):
    """Per week or month for one contract: scoreboard weft vs ERP weft net issued."""
    scb = {}
    for cno, rd, kg in _rows(con, cfg):
        if cno == str(contract):
            scb[_period_key(rd, period)] = scb.get(_period_key(rd, period), 0) + kg
    erp = {}
    for dd, mt, kg in con.execute("""SELECT move_date, move_type, COALESCE(kg,0) FROM yarn_movements
            WHERE contract_no=? AND move_type IN ('to_looms','from_looms')""", (str(contract),)):
        k = _period_key(str(dd), period)
        erp[k] = erp.get(k, 0) + (kg if mt == "to_looms" else -kg)
    _, _, tol = _cfg(cfg)
    rows = []
    for k in sorted(set(scb) | set(erp)):
        s, e = scb.get(k, 0), erp.get(k, 0)
        d = s - e
        pct = (100.0 * d / e) if e else None
        rows.append({"period": k, "scoreboard_kg": round(s), "erp_kg": round(e), "diff_kg": round(d),
                     "pct": (round(pct, 1) if pct is not None else None),
                     "flag": bool(pct is not None and abs(pct) > tol)})
    return rows


def weft_overview(con, cfg):
    """Every contract that has scoreboard weft or ERP weft: the per-contract comparison, flagged ones first."""
    scb = {}
    for cno, _, kg in _rows(con, cfg):
        scb[cno] = scb.get(cno, 0) + kg
    erp = {}
    for cno, in con.execute("SELECT DISTINCT contract_no FROM yarn_movements WHERE move_type IN ('to_looms','from_looms') AND contract_no IS NOT NULL"):
        erp[str(cno)] = _erp_weft(con, cno)
    _, _, tol = _cfg(cfg)
    names = {str(c): (p or "") for c, p in con.execute("SELECT contract_no, COALESCE(party_name, party_code) FROM contracts")}
    qual = {str(c): (q or "") for c, q in con.execute("SELECT contract_no, quality FROM contracts")}
    rows = []
    for cno in sorted(set(scb) | set(erp)):
        s, e = scb.get(cno, 0), erp.get(cno, 0)
        if round(s) == 0 and round(e) == 0:
            continue
        d = s - e
        pct = (100.0 * d / e) if e else None
        rows.append({"contract": cno, "party": names.get(cno, ""), "quality": qual.get(cno, ""),
                     "scoreboard_kg": round(s), "erp_kg": round(e), "diff_kg": round(d),
                     "pct": (round(pct, 1) if pct is not None else None),
                     "flag": bool(pct is not None and abs(pct) > tol)})
    rows.sort(key=lambda r: (0 if r["flag"] else 1, -abs(r["diff_kg"])))
    return {"rows": rows, "tolerance_pct": tol}
