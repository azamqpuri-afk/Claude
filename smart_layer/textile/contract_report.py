"""
One contract, end to end: everything the ERP holds about a weaving contract gathered into one report - yarn in,
sizing, sets, beams, production, weft, fabric out and the balances - with a picks check that flags the same quality
run at different picks per inch.

Generic: it reads only the canonical entities (contracts, yarn_movements, sized_sets, beams, loom_production,
loom_monitor, fabric_dispatches); which ERP columns feed those lives in the mapping, never here. Output is a set of
sections, each rendered to the dashboard, to one Excel sheet, and to a section of the PDF. Also on the CLI:
`report contract --contract <no>`.
"""

from pathlib import Path


def _q(con, sql, params=()):
    try:
        return con.execute(sql, params).fetchall()
    except Exception:
        return []


def _has(con, table):
    return bool(_q(con, f"SELECT 1 FROM {table} LIMIT 1"))


def contract_list(con, include_closed=True):
    """Contracts for the picker: open and closed, newest first, with party and quality."""
    rows = _q(con, """SELECT contract_no, COALESCE(party_name, party_code), quality, contract_date
                      FROM contracts ORDER BY contract_date DESC, contract_no DESC""")
    out = []
    for cno, party, quality, cdate in rows:
        out.append({"contract": str(cno), "party": party or "", "quality": quality or "", "date": cdate,
                    "label": f"{cno} · {party or ''}" + (f" · {quality}" if quality else "")})
    return out


def _norm(s):
    return str(s or "").strip().upper()


def _side(code, name, warp_yarn, warp_count, weft_yarn, weft_count):
    """warp or weft for a received yarn. The warp is the yarn that gets sized, so match it on the contract's warp
    yarn code OR warp count; anything else that is a real yarn receipt is weft (its count usually matches weft_count,
    but even when it does not we default to weft, never blank - so the weft balance is never left at zero)."""
    c, n = _norm(code), _norm(name)
    wy, wc = _norm(warp_yarn), _norm(warp_count)
    if c and wy and c == wy:
        return "warp"
    if wc and (wc == c or wc == n or (wc in n)):
        return "warp"
    return "weft"


def contract_report(con, cfg, contract):
    """All sections for one contract. Returns {header, sections:[{key,title,columns,rows,totals,note}], found}."""
    contract = str(contract)
    c = _q(con, """SELECT contract_no, party_code, party_name, contract_date, quality, ends_per_inch, picks_per_inch,
                   width_in, warp_yarn, warp_count, weft_yarn, weft_count, ordered_meters, rate_per_meter,
                   rate_per_pick, yarn_owner FROM contracts WHERE contract_no = ?""", (contract,))
    if not c:
        return {"found": False, "contract": contract, "header": {}, "sections": []}
    (cno, pcode, pname, cdate, quality, epi, ppi, width, warp_yarn, warp_count, weft_yarn, weft_count,
     omtr, rate_m, rate_pick, owner) = c[0]
    cur = cfg["company"]["currency"]
    header = {"contract": cno, "party": pname or pcode, "quality": quality, "warp_count": warp_count,
              "weft_count": weft_count, "ends_per_inch": epi, "picks_per_inch": ppi, "width_in": width,
              "ordered_meters": omtr, "rate_per_meter": rate_m, "contract_date": cdate, "yarn_owner": owner}
    sections = []

    def sec(key, title, columns, rows, totals=None, note=None, num_cols=None):
        sections.append({"key": key, "title": title, "columns": columns, "rows": rows,
                         "totals": totals, "note": note, "num_cols": num_cols or []})

    # ---- yarn received (IGP) ----
    yr = _q(con, """SELECT move_date, COALESCE(gatepass_no, doc_no), yarn_name, yarn_code, party_name,
                    COALESCE(bags,0), COALESCE(kg,0) FROM yarn_movements
                    WHERE contract_no = ? AND move_type = 'party_receipt' ORDER BY move_date""", (contract,))
    rows, tb, tk, warp_k, weft_k = [], 0.0, 0.0, 0.0, 0.0
    for md, gp, yn, yc, party, bags, kg in yr:
        side = _side(yc, yn, warp_yarn, warp_count, weft_yarn, weft_count)
        rows.append([md, gp, yn, party, round(bags), round(kg), side])
        tb += bags; tk += kg
        if side == "warp":
            warp_k += kg
        elif side == "weft":
            weft_k += kg
    sec("yarn_in", "Yarn received (IGP)", ["Date", "IGP no", "Count", "Mill / party", "Bags", "Kg", "Warp/Weft"],
        rows, totals=["", "", "", "TOTAL", round(tb), round(tk), ""], num_cols=[4, 5])

    # ---- yarn returned / sold (OGP yarn) ----
    yrr = _q(con, """SELECT move_date, COALESCE(gatepass_no, doc_no), yarn_name, move_type, COALESCE(bags,0), COALESCE(kg,0)
                     FROM yarn_movements WHERE contract_no = ? AND move_type IN ('party_return','sale') ORDER BY move_date""", (contract,))
    rows = [[md, gp, yn, ("sold" if mt == "sale" else "returned"), round(bags), round(kg)] for md, gp, yn, mt, bags, kg in yrr]
    sec("yarn_out", "Yarn returned / sold (OGP)", ["Date", "OGP no", "Count", "Type", "Bags", "Kg"], rows,
        totals=["", "", "", "TOTAL", round(sum(r[4] for r in rows)), round(sum(r[5] for r in rows))], num_cols=[4, 5])

    # ---- to / from sizing, per sizer, with the REAL loss % ----
    # yarn sent = the to-sizer movements; yarn back = the warp yarn embodied in the sized beams (sized_sets.yarn_sent_kg),
    # with gully and waste shown separately. Loss % = (sent - sized-beam yarn) / sent. Comparing the sent kg to the
    # from-sizer kg gave a false 0% because they are the same figure routed back.
    siz = {}
    for party, kg in _q(con, """SELECT COALESCE(party_name, party_code), SUM(COALESCE(kg,0)) FROM yarn_movements
            WHERE contract_no = ? AND move_type = 'to_sizer' GROUP BY party_name""", (contract,)):
        siz.setdefault(party or "—", {"to": 0.0, "back": 0.0, "gully": 0.0, "waste": 0.0})["to"] += kg or 0
    for party, back, gully, waste in _q(con, """SELECT COALESCE(sizer_name,'—'), SUM(COALESCE(yarn_sent_kg,0)),
            SUM(COALESCE(gullay_kg,0)), SUM(COALESCE(waste_kg,0)) FROM sized_sets WHERE contract_no = ? GROUP BY sizer_name""", (contract,)):
        s = siz.setdefault(party or "—", {"to": 0.0, "back": 0.0, "gully": 0.0, "waste": 0.0})
        s["back"] += back or 0; s["gully"] += gully or 0; s["waste"] += waste or 0
    rows = []
    for party, s in sorted(siz.items()):
        loss = (100.0 * (s["to"] - s["back"]) / s["to"]) if s["to"] else 0
        rows.append([party, round(s["to"]), round(s["back"]), round(s["gully"], 1), round(s["waste"], 1), f"{loss:.1f}%"])
    sec("sizing", "Yarn to / from sizing (per sizer)",
        ["Sizer", "Yarn sent kg", "Sized-beam yarn kg", "Gully kg", "Waste kg", "Loss %"], rows, num_cols=[1, 2, 3, 4])

    # ---- sets sized ----
    ss = _q(con, """SELECT set_no, set_date, sizer_name, COALESCE(ends,0), COALESCE(set_length_m,0), COALESCE(bags,0),
                    COALESCE(gullay_kg,0), COALESCE(waste_kg,0), COALESCE(yarn_sent_kg,0) FROM sized_sets
                    WHERE contract_no = ? ORDER BY set_date, set_no""", (contract,))
    rows = []
    for sn, sd, sz, ends, length, bags, gully, waste, ysent in ss:
        count = (ends * 1.0936 * length / (840.0 * ysent * 2.2046)) if (ysent and length) else 0
        rows.append([sn, sd, sz, round(ends), round(length), round(bags), round(ysent), round(gully, 1), round(waste, 1),
                     (f"{count:.1f}" if count else "—")])
    sec("sets", "Sets sized", ["Set", "Date", "Sizer", "Ends", "Length m", "Beams/bags", "Yarn kg", "Gully kg", "Waste kg", "Calc count"],
        rows, num_cols=[3, 4, 5, 6, 7, 8])

    # ---- beams, per loom, running vs finished ----
    # A running beam still has warp on it (status on_loom, or length_left_m > 0): short/extra % has no meaning yet,
    # so we show "running, X m to come" instead of a false shortfall. Short/extra % is only for finished beams.
    # Some beams appear only on loom_production (beam_no there) and not in the beams snapshot - union them in so e.g.
    # a beam the ERP recorded against a loom but not in the beam view still shows, with its woven meters.
    nm = dict(_q(con, "SELECT loom_code, loom_name FROM looms"))
    bm = _q(con, """SELECT beam_no, status, loom_code, mounted_date, COALESCE(set_length_m,0),
                    COALESCE(woven_m,0), length_left_m FROM beams WHERE contract_no = ? ORDER BY mounted_date, beam_no""", (contract,))
    seen = set()
    rows, fin_set, fin_woven, running, finished = [], 0.0, 0.0, 0, 0
    for beam, status, loom, md, length, woven, left in bm:
        seen.add(_norm(beam))
        st = _norm(status)
        is_running = (st == "ON_LOOM") or (left is not None and (left or 0) > 0.5)
        if is_running:
            running += 1
            to_come = round(left) if left is not None else (round(length - woven) if length else 0)
            state = f"running, {to_come:,} m to come" if to_come > 0 else "running"
        elif st == "SIZED_WAITING":
            state = "sized, waiting"
        else:
            state = status or "—"
        rows.append([beam, nm.get(loom, loom), md, round(length), round(woven), state, "—"])
    # Finished / historical beams: the current snapshot only holds beams still on a loom or waiting, so a beam that
    # has come off the loom has left it. beam_movements keeps the REAL beam number (painted), its set length and
    # woven meters - pull in any not already shown. We never source beam numbers from loom_production.beam_no: on
    # some ERPs that is an internal transaction id, not the beam number. Short/extra % and shrinkage are finished-only.
    mv = _q(con, """SELECT beam_no, MAX(loom_code), MAX(COALESCE(length_m,0)), MAX(COALESCE(woven_m,0)),
                    MAX(CASE WHEN move_type IN ('from_loom_finished','from_loom_cut') THEN 1 ELSE 0 END)
                    FROM beam_movements WHERE contract_no = ? AND beam_no IS NOT NULL AND beam_no <> ''
                    GROUP BY beam_no ORDER BY beam_no""", (contract,))
    for beam, loom, length, woven, came_off in mv:
        if _norm(beam) in seen:
            continue
        seen.add(_norm(beam))
        if came_off:
            finished += 1
            fin_set += length or 0; fin_woven += woven or 0
            pct = (f"{100.0 * (woven - length) / length:+.1f}%" if length else "—")
            state = "finished"
        else:
            state = "on record"
            pct = "—"
        rows.append([beam, nm.get(loom, loom), "—", round(length), round(woven), state, pct])
    bnote = None
    if running:
        bnote = f"{running} beam(s) still running - their 'to come' meters are warp not yet woven, not a shortfall."
    sec("beams", "Beams", ["Beam", "Loom", "Mounted", "Set length m", "Woven m", "State", "Short/extra %"],
        rows, note=bnote, num_cols=[3, 4])

    # ---- production by loom, with ppi ----
    pr = _q(con, """SELECT loom_code, SUM(meters), SUM(COALESCE(first_quality_m,0)), SUM(COALESCE(second_quality_m,0)),
                    SUM(COALESCE(rejected_m,0)) FROM loom_production WHERE contract_no = ? GROUP BY loom_code""", (contract,))
    praw, tM, tA, tB, tR = [], 0.0, 0.0, 0.0, 0.0
    for loom, m, a, b, rej in pr:
        praw.append((loom, m or 0, a or 0, b or 0, rej or 0))
        tM += m or 0; tA += a or 0; tB += b or 0; tR += rej or 0
    # Make the grade columns tie to the total. Some ERPs (e.g. Dost) count rejection as B + C + CP, so the rejection
    # total ALREADY includes the B/2nd-quality meters and Total = A + Rejection. Others keep the three separate
    # (Total = A + B + Rejection). [weaving] rejection_includes_second picks which; "auto" detects it per contract.
    wv = cfg["weaving"]
    gl = wv.get("grade_labels") or {}
    la, lb, lr = gl.get("a", "A-grade"), gl.get("b", "B-grade"), gl.get("reject", "Rejection")
    mode = wv.get("rejection_includes_second", "auto")
    tol = max(1.0, 0.01 * tM) if tM else 1.0
    incl_b = (bool(tM and abs((tA + tR) - tM) <= tol and tR + tol >= tB) if mode == "auto" else bool(mode))
    ppi_cell = (f"{ppi:g}" if ppi else "—")
    if incl_b:
        rows = [[nm.get(l, l), round(m), round(a), round(b), round(max(0.0, rej - b)), round(rej), ppi_cell]
                for l, m, a, b, rej in praw]
        cols = ["Loom", "Total m", f"{la} m", f"{lb} m", "C/CP m", f"{lr} m", "PPI (ERP)"]
        totals = [f"TOTAL ({len(rows)} looms)", round(tM), round(tA), round(tB), round(max(0.0, tR - tB)), round(tR), ""]
        pnote = f"Total m = {la} + {lr}. {lr} m already includes {lb} ({lr} = {lb} + C/CP), so do not add {lb} again."
        numc = [1, 2, 3, 4, 5]
    else:
        rows = [[nm.get(l, l), round(m), round(a), round(b), round(rej), ppi_cell] for l, m, a, b, rej in praw]
        cols = ["Loom", "Total m", f"{la} m", f"{lb} m", f"{lr} m", "PPI (ERP)"]
        totals = [f"TOTAL ({len(rows)} looms)", round(tM), round(tA), round(tB), round(tR), ""]
        pnote = (f"{la} + {lb} + {lr} = total meters (separate grades)."
                 if (tM and abs((tA + tB + tR) - tM) <= tol) else None)
        numc = [1, 2, 3, 4]
    sec("production", "Production by loom", cols, rows, totals=totals, note=pnote, num_cols=numc)

    # ---- picks check ----
    # Two flags: (a) WITHIN this contract - the ERP recorded different picks/inch on different looms/dates for the same
    # cloth (loom_production.picks_per_inch, when present); (b) ACROSS contracts - the same quality ran at a different
    # ppi elsewhere. Both point at a meters/pick discrepancy worth checking.
    within = []
    for lp_ppi, lcount, lmeters in _q(con, """SELECT picks_per_inch, COUNT(*), SUM(COALESCE(meters,0))
            FROM loom_production WHERE contract_no = ? AND picks_per_inch IS NOT NULL
            GROUP BY picks_per_inch ORDER BY picks_per_inch""", (contract,)):
        within.append([f"{lp_ppi:g}", round(lcount or 0), round(lmeters or 0)])
    within_note = None
    if len(within) > 1:
        within_note = ("This contract was woven at more than one picks/inch in the ERP entries - "
                       + ", ".join(w[0] for w in within) + " ppi. Check which is right.")
        sec("picks_within", "Picks check - within this contract", ["PPI (ERP entry)", "Entries", "Meters"], within,
            note=within_note, num_cols=[1, 2])

    rows = []
    if quality:
        for ocno, oppi, od, om in _q(con, """SELECT c.contract_no, c.picks_per_inch, c.contract_date,
                (SELECT SUM(meters) FROM loom_production p WHERE p.contract_no = c.contract_no)
                FROM contracts c WHERE c.quality = ? AND c.contract_no <> ?""", (quality, contract)):
            if oppi is not None and ppi is not None and abs((oppi or 0) - (ppi or 0)) > 0.01:
                rows.append([ocno, od, f"{oppi:g}", round(om or 0)])
    note = None
    if rows:
        note = f"Quality '{quality}' ran at {ppi:g} ppi on this contract but at a different ppi elsewhere - check the picks."
    sec("picks_check", "Picks check (same quality, different PPI)", ["Other contract", "Date", "PPI", "Meters"], rows,
        note=note, num_cols=[3])

    # ---- weft issued to / returned from looms ----
    wf = _q(con, """SELECT move_date, COALESCE(gatepass_no, doc_no), move_type, COALESCE(bags,0), COALESCE(kg,0)
                    FROM yarn_movements WHERE contract_no = ? AND move_type IN ('to_looms','from_looms') ORDER BY move_date""", (contract,))
    rows = [[md, gp, ("issued" if mt == "to_looms" else "returned"), round(bags), round(kg)] for md, gp, mt, bags, kg in wf]
    sec("weft", "Weft yarn issued / returned", ["Date", "Doc", "Type", "Bags", "Kg"], rows, num_cols=[3, 4])

    # ---- fabric dispatched (OGP fabric) ----
    fd = _q(con, """SELECT dispatch_date, doc_no, COALESCE(meters,0), COALESCE(fresh_m,0), COALESCE(rejected_m,0),
                    direction, thaan FROM fabric_dispatches WHERE contract_no = ? ORDER BY dispatch_date""", (contract,))
    rows, out_m, in_m, t_th, any_th = [], 0.0, 0.0, 0.0, False
    for dd, doc, m, fresh, rej, direction, thaan in fd:
        # a NULL thaan means the ERP simply did not record it on that gate pass - show "—", not a false 0. Keep a
        # real 0 as 0, and leave the total blank if every gate pass had no thaan.
        rows.append([dd, doc, ("out" if direction == "out" else "in"), round(m), round(fresh), round(rej),
                     (round(thaan) if thaan is not None else "—")])
        if direction == "out":
            out_m += m or 0
        else:
            in_m += m or 0
        if thaan is not None:
            any_th = True
            t_th += thaan or 0
    sec("fabric_out", "Fabric dispatched (OGP)", ["Date", "OGP no", "Dir", "Meters", "Fresh m", "Rejection m", "Thaan"], rows,
        totals=["", "", "net out", round(out_m - in_m), "", "", (round(t_th) if any_th else "—")], num_cols=[3, 4, 5, 6])

    # ---- balances ----
    woven = _q(con, "SELECT SUM(meters) FROM loom_production WHERE contract_no = ?", (contract,))
    woven = (woven[0][0] or 0) if woven else 0
    gully_total = _q(con, "SELECT SUM(COALESCE(gullay_kg,0)) FROM sized_sets WHERE contract_no = ?", (contract,))
    gully_total = (gully_total[0][0] or 0) if gully_total else 0
    to_sizing = sum(s["to"] for s in siz.values())
    weft_issued = _q(con, "SELECT SUM(COALESCE(kg,0)) FROM yarn_movements WHERE contract_no=? AND move_type='to_looms'", (contract,))
    weft_issued = (weft_issued[0][0] or 0) if weft_issued else 0
    weft_ret = _q(con, "SELECT SUM(COALESCE(kg,0)) FROM yarn_movements WHERE contract_no=? AND move_type='from_looms'", (contract,))
    weft_ret = (weft_ret[0][0] or 0) if weft_ret else 0
    # Shrinkage = how much shorter the fabric is than the warp it was woven from. It only means anything on a beam
    # that is FINISHED (fully woven off the loom) - on a running beam the warp is partly unwoven, so set-length vs
    # woven would read as a huge false "shrinkage". So compute it on finished beams only; while none are finished say
    # so, rather than print a meaningless number.
    if finished and fin_set:
        shrink_item = f"Shrinkage % (finished beams only: {finished})"
        shrink_val = f"{100.0 * (fin_set - fin_woven) / fin_set:.1f}%"
    elif running:
        shrink_item, shrink_val = "Shrinkage %", f"n/a - all {running} beam(s) still running"
    else:
        shrink_item, shrink_val = "Shrinkage %", "n/a - no finished beams yet"
    brows = [
        ["Warp yarn received kg", round(warp_k)],
        ["Warp yarn sent to sizing kg", round(to_sizing)],
        ["Warp yarn balance kg", round(warp_k - to_sizing)],
        ["Weft yarn received kg", round(weft_k)],
        ["Weft yarn issued to looms kg", round(weft_issued - weft_ret)],
        ["Weft yarn balance kg", round(weft_k - (weft_issued - weft_ret))],
        ["Fabric woven m", round(woven)],
        ["Fabric dispatched (net) m", round(out_m - in_m)],
        ["Fabric balance in stock m", round(woven - (out_m - in_m))],
        ["Gully returned kg", round(gully_total, 1)],
        [shrink_item, shrink_val],
    ]
    sec("balances", "Balances", ["Item", "Value"], brows)

    return {"found": True, "contract": contract, "header": header, "currency": cur,
            "quality": quality, "party": pname or pcode, "sections": sections}


# ---- exports ---------------------------------------------------------------------------------

def _xlsx_sheets(rep):
    sheets = []
    h = rep["header"]
    hv = [["Contract", h.get("contract")], ["Party", h.get("party")], ["Quality", h.get("quality")],
          ["Warp count", h.get("warp_count")], ["Weft count", h.get("weft_count")],
          ["Ends/inch", h.get("ends_per_inch")], ["Picks/inch", h.get("picks_per_inch")],
          ["Width in", h.get("width_in")], ["Ordered meters", h.get("ordered_meters")],
          ["Rate / m", h.get("rate_per_meter")], ["Contract date", h.get("contract_date")]]
    sheets.append({"name": "Header", "title": f"Contract {h.get('contract')}", "headers": ["Field", "Value"], "rows": hv})
    for s in rep["sections"]:
        rows = list(s["rows"])
        if s.get("totals"):
            rows = rows + [s["totals"]]
        note = [s["note"]] if s.get("note") else None
        sheets.append({"name": s["key"][:28], "title": s["title"], "headers": s["columns"], "rows": rows,
                       "note": note, "money_cols": s.get("num_cols")})
    return sheets


def write_xlsx(con, cfg, contract, folder, name):
    from ..reports import xlsx
    rep = contract_report(con, cfg, contract)
    return xlsx.write(Path(folder) / name, _xlsx_sheets(rep), cfg["company"]["name"])


def write_pdf(con, cfg, contract, folder, name, product=""):
    """A multi-section PDF: the contract header, then one table per section."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    from ..reports import fmt
    rep = contract_report(con, cfg, contract)
    path = Path(folder) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    navy = colors.HexColor("#1F3B57")
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7.5, leading=9.5)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    company = cfg["company"]["name"]
    h = rep["header"]
    story = [Paragraph(escape(company), ParagraphStyle("co", parent=styles["Normal"], textColor=navy, fontSize=9)),
             Paragraph(escape(f"Contract {h.get('contract','')} - {h.get('party','')}"),
                       ParagraphStyle("t", parent=styles["Title"], alignment=0, textColor=navy, fontSize=15))]
    hl = (f"Quality {h.get('quality') or '-'} · warp {h.get('warp_count') or '-'} / weft {h.get('weft_count') or '-'} · "
          f"{h.get('ends_per_inch') or '-'} epi / {h.get('picks_per_inch') or '-'} ppi · ordered "
          f"{h.get('ordered_meters') or 0:,.0f} m · rate {h.get('rate_per_meter') or 0}")
    story.append(Paragraph(escape(hl), styles["BodyText"]))
    story.append(Spacer(1, 3 * mm))

    def table(columns, rows):
        data = [[Paragraph(escape(str(c)), head) for c in columns]]
        numeric = [all(isinstance(r[i], (int, float)) or r[i] in (None, "") for r in rows) for i in range(len(columns))]
        for row in rows:
            data.append([Paragraph(escape(fmt(v)), ParagraphStyle(f"c{i}", parent=cell, alignment=2 if numeric[i] else 0))
                         for i, v in enumerate(row)])
        avail = landscape(A4)[0] - 24 * mm
        t = LongTable(data, repeatRows=1, colWidths=[avail / len(columns)] * len(columns))
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), navy), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9dde3")),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f8")])]))
        return t

    for s in rep["sections"]:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(escape(s["title"]), ParagraphStyle("sh", parent=styles["Heading3"], textColor=navy)))
        if s.get("note"):
            story.append(Paragraph(escape(s["note"]), ParagraphStyle("n", parent=cell, textColor=colors.HexColor("#b05a00"))))
        rows = list(s["rows"]) + ([s["totals"]] if s.get("totals") else [])
        if rows:
            story.append(table(s["columns"], rows))
        else:
            story.append(Paragraph("(none)", cell))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(landscape(A4)[0] / 2, 8 * mm, f"{product} · {company} · page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=14 * mm, title=f"Contract {h.get('contract','')}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
