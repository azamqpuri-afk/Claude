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
    rows = _q(con, """SELECT contract_no, COALESCE(party_name, party_code), quality, contract_date, party_code
                      FROM contracts ORDER BY contract_date DESC, contract_no DESC""")
    out = []
    for cno, party, quality, cdate, pcode in rows:
        out.append({"contract": str(cno), "party": party or "", "party_code": str(pcode or ""), "quality": quality or "",
                    "date": cdate, "label": f"{cno} · {party or ''}" + (f" · {quality}" if quality else "")})
    return out


def party_list(con):
    """Parties that have contracts, for the party-first picker: code, name, and how many contracts each has."""
    rows = _q(con, """SELECT party_code, COALESCE(MAX(party_name), party_code) AS name, COUNT(*) AS n
                      FROM contracts GROUP BY party_code ORDER BY name""")
    return [{"party_code": str(pc or ""), "party": nm or str(pc or ""), "contracts": int(n or 0),
             "label": f"{nm or pc} ({int(n or 0)})"} for pc, nm, n in rows]


def party_scope(con, party_code):
    """One party's contracts and the distinct qualities among them - the contract + quality dropdowns are filled from
    this when a party is picked, so only that party's contracts and qualities ever show."""
    pc = str(party_code)
    contracts = [c for c in contract_list(con) if c["party_code"] == pc]
    quals, seen = [], set()
    for c in contracts:
        q = c["quality"]
        if q and q not in seen:
            seen.add(q)
            quals.append(q)
    party = contracts[0]["party"] if contracts else pc
    return {"party_code": pc, "party": party, "contracts": contracts, "qualities": sorted(quals)}


def _party_of(con, contract):
    r = _q(con, "SELECT party_code, COALESCE(party_name, party_code) FROM contracts WHERE contract_no = ?", (str(contract),))
    return (str(r[0][0] or ""), r[0][1]) if r else ("", "")


def _other_party(dp_code, contract_pcode, otherlist, samelist):
    """Is this fabric OGP to ANOTHER party (rejection sale / sold elsewhere), by ACCOUNT CODE - not deliver_to text?
    A deliver-to dyer/godown under the contract party's own code stays with the contract; a different account code is
    'another party'. Config lists can pin a code either way."""
    p = _norm(dp_code)
    if not p:
        return False
    if p in samelist:
        return False
    if p in otherlist:
        return True
    return bool(contract_pcode and p != _norm(contract_pcode))


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


def _pieces_across(con, contract):
    """Panels across the beam for this contract (3 for a 3-panel 46" beam, 1 otherwise). Production meters count every
    panel woven, while the sized length is the warp length of one panel - so shrinkage must divide the woven meters by
    this. Take the dominant (mode) scoreboard pieces for the contract's looms, else the beam bwidth, else 1."""
    r = _q(con, """SELECT pieces FROM loom_monitor WHERE pieces IS NOT NULL AND loom_code IN
                     (SELECT DISTINCT loom_code FROM loom_production WHERE contract_no = ?)
                   GROUP BY pieces ORDER BY COUNT(*) DESC, pieces DESC LIMIT 1""", (contract,))
    if r and r[0][0]:
        return float(r[0][0])
    r = _q(con, """SELECT bwidth FROM beams WHERE contract_no = ? AND bwidth IS NOT NULL
                   GROUP BY bwidth ORDER BY COUNT(*) DESC LIMIT 1""", (contract,))
    if r and r[0][0]:
        return float(r[0][0])
    return 1.0


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
    crcfg = cfg["contract_report"] or {}
    otherlist = {_norm(x) for x in (crcfg.get("other_party_codes") or [])}
    samelist = {_norm(x) for x in (crcfg.get("same_party_codes") or [])}
    header = {"contract": cno, "party": pname or pcode, "quality": quality, "warp_count": warp_count,
              "weft_count": weft_count, "ends_per_inch": epi, "picks_per_inch": ppi, "width_in": width,
              "ordered_meters": omtr, "rate_per_meter": rate_m, "contract_date": cdate, "yarn_owner": owner}
    sections = []

    def sec(key, title, columns, rows, totals=None, note=None, num_cols=None):
        sections.append({"key": key, "title": title, "columns": columns, "rows": rows,
                         "totals": totals, "note": note, "num_cols": num_cols or []})

    pieces_x = _pieces_across(con, contract)              # panels across for this contract (woven meters count them all)

    def loom_pieces(loom):
        r = _q(con, """SELECT pieces FROM loom_monitor WHERE loom_code = ? AND pieces IS NOT NULL
                       GROUP BY pieces ORDER BY COUNT(*) DESC, pieces DESC LIMIT 1""", (loom,))
        return float(r[0][0]) if r and r[0][0] else pieces_x

    # ---- yarn received (IGP), split warp vs weft ----
    # When the contract's warp and weft are the SAME count (e.g. 54/54, 36/36), warp and weft cannot be told apart, so
    # we COMBINE them into one "Yarn received / to sizing / to looms / balance". We do NOT use the receipt warp/weft
    # tag to split: the ERP tags the party's whole yarn lot "warp" even when the same yarn is woven as weft, which gave
    # false 0 weft / negative balances. When the counts DIFFER we split by yarn count (the tag is only a tie-break).
    yr = _q(con, """SELECT move_date, COALESCE(gatepass_no, doc_no), yarn_name, yarn_code, party_name,
                    COALESCE(bags,0), COALESCE(kg,0), warp_weft FROM yarn_movements
                    WHERE contract_no = ? AND move_type = 'party_receipt' ORDER BY move_date""", (contract,))
    try:
        counts_differ = bool(warp_count and weft_count and abs(float(warp_count) - float(weft_count)) > 0.001)
    except (TypeError, ValueError):
        counts_differ = bool(warp_count and weft_count and str(warp_count) != str(weft_count))
    combined = not counts_differ
    rows, tb, tk, warp_k, weft_k = [], 0.0, 0.0, 0.0, 0.0
    for md, gp, yn, yc, party, bags, kg, ww in yr:
        if combined:
            side = "yarn"
        else:
            side = _side(yc, yn, warp_yarn, warp_count, weft_yarn, weft_count)
            if side == "weft" and ww and _norm(ww) == "WARP" and _norm(yc) != _norm(weft_yarn):
                side = "warp"                            # count was ambiguous - fall back to the ERP's tag as a tie-break
        rows.append([md, gp, yn, party, round(bags), round(kg), side])
        tb += bags; tk += kg
        if side == "weft":
            weft_k += kg
        else:
            warp_k += kg                                 # "warp" or combined "yarn" -> the warp/combined bucket
    sec("yarn_in", "Yarn received (IGP)",
        ["Date", "IGP no", "Count", "Mill / party", "Bags", "Kg", ("Yarn" if combined else "Warp/Weft")],
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
    _st, _sb = sum(s["to"] for s in siz.values()), sum(s["back"] for s in siz.values())
    _sloss = (100.0 * (_st - _sb) / _st) if _st else 0
    sec("sizing", "Yarn to / from sizing (per sizer)",
        ["Sizer", "Yarn sent kg", "Sized-beam yarn kg", "Gully kg", "Waste kg", "Loss %"], rows,
        totals=["TOTAL", round(_st), round(_sb), round(sum(s["gully"] for s in siz.values()), 1),
                round(sum(s["waste"] for s in siz.values()), 1), f"{_sloss:.1f}%"], num_cols=[1, 2, 3, 4])

    # ---- sets sized ----
    ss = _q(con, """SELECT set_no, set_date, sizer_name, COALESCE(ends,0), COALESCE(set_length_m,0), COALESCE(bags,0),
                    COALESCE(gullay_kg,0), COALESCE(waste_kg,0), COALESCE(yarn_sent_kg,0) FROM sized_sets
                    WHERE contract_no = ? ORDER BY set_date, set_no""", (contract,))
    set_woven = {}
    for sno, wv in _q(con, "SELECT set_no, SUM(COALESCE(woven_m,0)) FROM beams WHERE contract_no = ? GROUP BY set_no", (contract,)):
        set_woven[_norm(sno)] = wv or 0
    rows = []
    for sn, sd, sz, ends, length, bags, gully, waste, ysent in ss:
        count = (ends * 1.0936 * length / (840.0 * ysent * 2.2046)) if (ysent and length) else 0
        bw = set_woven.get(_norm(sn), 0)                 # fabric woven from this set's beams (counts every panel)
        shrink = (100.0 * (1 - (bw / (pieces_x or 1)) / length)) if (length and bw) else None
        rows.append([sn, sd, sz, round(ends), round(length), round(bags), round(ysent), round(gully, 1),
                     round(waste, 1), round(bw), (f"{shrink:.1f}%" if shrink is not None else "—"),
                     (f"{count:.1f}" if count else "—")])

    def _col(i):
        return sum(r[i] for r in rows if isinstance(r[i], (int, float)))
    sets_tot = (["", "", "TOTAL", round(_col(3)), round(_col(4)), round(_col(5)), round(_col(6)),
                 round(_col(7), 1), round(_col(8), 1), round(_col(9)), "", ""] if rows else None)
    sec("sets", "Sets sized",
        ["Set", "Date", "Sizer", "Ends", "Length m", "Beams/bags", "Yarn kg", "Gully kg", "Waste kg", "Woven m", "Shrink %", "Calc count"],
        rows, totals=sets_tot, num_cols=[3, 4, 5, 6, 7, 8, 9])

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
            lp = loom_pieces(loom)                        # woven counts every panel; compare one panel's woven to the set
            ew = (woven or 0) / (lp or 1)
            fin_set += length or 0; fin_woven += woven or 0
            pct = (f"{100.0 * (ew - length) / length:+.1f}%" if length else "—")
            state = "finished"
        else:
            state = "on record"
            pct = "—"
        rows.append([beam, nm.get(loom, loom), "—", round(length), round(woven), state, pct])
    bnote = None
    if running:
        bnote = f"{running} beam(s) still running - their 'to come' meters are warp not yet woven, not a shortfall."
    b_setlen = sum(r[3] for r in rows if isinstance(r[3], (int, float)))
    b_woven = sum(r[4] for r in rows if isinstance(r[4], (int, float)))
    sec("beams", "Beams", ["Beam", "Loom", "Mounted", "Set length m", "Woven m", "State", "Short/extra %"],
        rows, totals=(["", "", "TOTAL", round(b_setlen), round(b_woven), "", ""] if rows else None),
        note=bnote, num_cols=[3, 4])

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
            totals=["TOTAL", round(sum(w[1] for w in within)), round(sum(w[2] for w in within))],
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
        totals=(["TOTAL", "", "", round(sum(r[3] for r in rows))] if rows else None), note=note, num_cols=[3])

    # ---- weft issued to / returned from looms ----
    wf = _q(con, """SELECT move_date, COALESCE(gatepass_no, doc_no), move_type, COALESCE(bags,0), COALESCE(kg,0)
                    FROM yarn_movements WHERE contract_no = ? AND move_type IN ('to_looms','from_looms') ORDER BY move_date""", (contract,))
    rows = [[md, gp, ("issued" if mt == "to_looms" else "returned"), round(bags), round(kg)] for md, gp, mt, bags, kg in wf]
    sec("weft", "Weft yarn issued / returned", ["Date", "Doc", "Type", "Bags", "Kg"], rows,
        totals=(["", "", "TOTAL", round(sum(r[3] for r in rows)), round(sum(r[4] for r in rows))] if rows else None),
        num_cols=[3, 4])

    # ---- fabric dispatched (OGP fabric), split: to the contract party (counts) vs to ANOTHER party (memo only) ----
    fd = _q(con, """SELECT dispatch_date, doc_no, COALESCE(meters,0), COALESCE(fresh_m,0), COALESCE(rejected_m,0),
                    direction, thaan, party_code, deliver_to FROM fabric_dispatches
                    WHERE contract_no = ? ORDER BY dispatch_date""", (contract,))
    rows, out_m, in_m, t_th, any_th = [], 0.0, 0.0, 0.0, False
    oth_rows, oth_m, oth_th, oth_any = [], 0.0, 0.0, False
    for dd, doc, m, fresh, rej, direction, thaan, dpc, dto in fd:
        th_cell = (round(thaan) if thaan is not None else "—")
        if direction == "out" and _other_party(dpc, pcode, otherlist, samelist):
            # a NULL thaan means the ERP simply did not record it - show "—", not a false 0.
            oth_rows.append([dd, doc, f"{dpc or ''}", dto or "", round(fresh), round(rej), round(m), th_cell])
            oth_m += m or 0
            if thaan is not None:
                oth_any = True
                oth_th += thaan or 0
            continue
        rows.append([dd, doc, ("out" if direction == "out" else "in"), round(m), round(fresh), round(rej), th_cell])
        if direction == "out":
            out_m += m or 0
        else:
            in_m += m or 0
        if thaan is not None:
            any_th = True
            t_th += thaan or 0
    sec("fabric_out", "Fabric dispatched to the party (OGP)", ["Date", "OGP no", "Dir", "Meters", "Fresh m", "Rejection m", "Thaan"], rows,
        totals=["", "", "net out", round(out_m - in_m), "", "", (round(t_th) if any_th else "—")], num_cols=[3, 4, 5, 6])
    # Always show this section (even empty, with a 0 total) so the owner knows it was checked: fabric of this
    # contract/quality that went to ANOTHER account (rejection sale / sold elsewhere), NOT counted in the summary or
    # the fabric-to-party balance - a memo line below the summary.
    sec("fabric_other", "Fabric to other parties (not in the contract balance)",
        ["Date", "OGP no", "Party code", "Deliver to", "Fresh m", "Rejection m", "Total m", "Thaan"],
        oth_rows or [["—", "none", "", "", "", "", 0, "—"]],
        totals=["", "", "", "TOTAL", "", "", round(oth_m), (round(oth_th) if oth_any else "—")],
        note="Any gate pass here went to a different account than the contract party, so it is shown here only and "
             "left out of the contract summary and the fabric-to-party balance." if oth_rows else
             "No fabric of this contract/quality went to another party - checked.", num_cols=[4, 5, 6, 7])

    # ---- balances ----
    woven = _q(con, "SELECT SUM(meters) FROM loom_production WHERE contract_no = ?", (contract,))
    woven = (woven[0][0] or 0) if woven else 0
    gully_total = _q(con, "SELECT SUM(COALESCE(gullay_kg,0)) FROM sized_sets WHERE contract_no = ?", (contract,))
    gully_total = (gully_total[0][0] or 0) if gully_total else 0
    sized_total = _q(con, "SELECT SUM(COALESCE(set_length_m,0)) FROM sized_sets WHERE contract_no = ?", (contract,))
    sized_total = (sized_total[0][0] or 0) if sized_total else 0
    to_sizing = sum(s["to"] for s in siz.values())
    weft_issued = _q(con, "SELECT SUM(COALESCE(kg,0)) FROM yarn_movements WHERE contract_no=? AND move_type='to_looms'", (contract,))
    weft_issued = (weft_issued[0][0] or 0) if weft_issued else 0
    weft_ret = _q(con, "SELECT SUM(COALESCE(kg,0)) FROM yarn_movements WHERE contract_no=? AND move_type='from_looms'", (contract,))
    weft_ret = (weft_ret[0][0] or 0) if weft_ret else 0
    # Shrinkage: finished beams only (a running beam's warp is partly unwoven, so set-length vs woven would read as a
    # huge false shrinkage). The woven meters count EVERY panel across the loom, while the set length is one panel's
    # warp length, so divide the woven meters by the panels-across before comparing (else a 3-panel beam reads ~-200%).
    pieces_x = _pieces_across(con, contract)
    shrink_pct = (100.0 * (fin_set - fin_woven / (pieces_x or 1)) / fin_set) if (finished and fin_set) else None
    if shrink_pct is not None:
        px = f", {pieces_x:g} panels" if pieces_x and pieces_x != 1 else ""
        shrink_item = f"Shrinkage % (finished beams only: {finished}{px})"
        shrink_val = f"{shrink_pct:.1f}%"
    elif running:
        shrink_item, shrink_val = "Shrinkage %", f"n/a - all {running} beam(s) still running"
    else:
        shrink_item, shrink_val = "Shrinkage %", "n/a - no finished beams yet"
    weft_net = weft_issued - weft_ret
    if combined:                                         # warp and weft are the same yarn - one combined balance
        brows = [
            ["Yarn received kg", round(tk)],
            ["Yarn sent to sizing kg", round(to_sizing)],
            ["Yarn issued to looms kg", round(weft_net)],
            ["Yarn balance kg", round(tk - to_sizing - weft_net)],
        ]
    else:
        brows = [
            ["Warp yarn received kg", round(warp_k)],
            ["Warp yarn sent to sizing kg", round(to_sizing)],
            ["Warp yarn balance kg", round(warp_k - to_sizing)],
            ["Weft yarn received kg", round(weft_k)],
            ["Weft yarn issued to looms kg", round(weft_net)],
            ["Weft yarn balance kg", round(weft_k - weft_net)],
        ]
    brows += [
        ["Fabric woven m", round(woven)],
        ["Fabric dispatched to the party (net) m", round(out_m - in_m)],
        ["Fabric balance in stock m", round(woven - (out_m - in_m))],
        ["Gully returned kg", round(gully_total, 1)],
        [shrink_item, shrink_val],
    ]
    if oth_m:
        brows.append(["Fabric to other parties (memo, not in the balance) m", round(oth_m)])
    sec("balances", "Summary", ["Item", "Value"], brows)

    summary = {"contract": cno, "combined": combined,
               "warp_recv": round(tk if combined else warp_k), "warp_bal": round((tk - to_sizing - weft_net) if combined else (warp_k - to_sizing)),
               "weft_recv": (0 if combined else round(weft_k)), "weft_bal": (0 if combined else round(weft_k - weft_net)),
               "woven": round(woven), "dispatched": round(out_m - in_m), "to_other": round(oth_m),
               "stock": round(woven - (out_m - in_m)), "sized": round(sized_total),
               "shrink": (round(shrink_pct, 1) if shrink_pct is not None else None)}
    sections.sort(key=lambda s: 0 if s["key"] == "balances" else 1)   # Summary on top (stable: keeps the rest in order)
    return {"found": True, "contract": contract, "header": header, "currency": cur, "mode": "contract",
            "quality": quality, "party": pname or pcode, "party_code": str(pcode or ""),
            "summary": summary, "sections": sections}


def quality_report(con, cfg, party_code, quality):
    """'Closing by quality': all of ONE party's contracts of the SAME quality, combined - a per-contract summary table
    on top, then each detail section with a sub-row per contract and a grand total. Same section shape as a single
    contract, so the same renderers serve it."""
    pc = str(party_code)
    cons = [c["contract"] for c in contract_list(con) if c["party_code"] == pc and c["quality"] == quality]
    reps = [r for r in (contract_report(con, cfg, cn) for cn in cons) if r.get("found")]
    if not reps:
        return {"found": False, "mode": "quality", "party_code": pc, "party": pc, "quality": quality,
                "header": {}, "sections": []}
    party = reps[0]["party"]
    h0 = reps[0]["header"]
    header = {"contract": f"{len(reps)} contracts", "party": party, "quality": quality,
              "warp_count": h0.get("warp_count"), "weft_count": h0.get("weft_count"),
              "ends_per_inch": h0.get("ends_per_inch"), "picks_per_inch": h0.get("picks_per_inch")}
    sections = []
    # per-contract summary table on top
    s_cols = ["Contract", "Warp recv kg", "Warp bal kg", "Weft recv kg", "Weft bal kg", "Woven m",
              "Dispatched m", "To other m", "Stock m", "Sized m", "Shrinkage %"]
    s_rows, tot = [], [0.0] * 9
    for r in reps:
        s = r["summary"]
        vals = [s["warp_recv"], s["warp_bal"], s["weft_recv"], s["weft_bal"], s["woven"], s["dispatched"],
                s["to_other"], s["stock"], s["sized"]]
        s_rows.append([s["contract"], *[round(v) for v in vals],
                       (f"{s['shrink']:.1f}%" if s["shrink"] is not None else "—")])
        for i, v in enumerate(vals):
            tot[i] += v or 0
    sections.append({"key": "summary", "title": "Per-contract summary", "columns": s_cols, "rows": s_rows,
                     "totals": ["TOTAL", *[round(x) for x in tot], ""], "note": None, "num_cols": list(range(1, 10))})
    # detail sections, merged by key with a sub-row per contract and a grand total
    order = ["yarn_in", "yarn_out", "sizing", "sets", "beams", "production", "weft", "fabric_out", "fabric_other",
             "picks_within", "picks_check"]
    by_key = {}
    for r in reps:
        for s in r["sections"]:
            if s["key"] == "balances":
                continue                                 # the per-contract balances roll up into the summary table
            by_key.setdefault(s["key"], []).append((r["summary"]["contract"], s))
    for key in order:
        blocks = by_key.get(key)
        if not blocks:
            continue
        cols = blocks[0][1]["columns"]
        numc = blocks[0][1].get("num_cols") or []
        rows, note = [], blocks[0][1].get("note")
        grand = [""] * len(cols)
        grand[0] = "GRAND TOTAL"
        for cn, s in blocks:
            rows.append([f"— contract {cn} —"] + [""] * (len(cols) - 1))
            rows.extend(s["rows"])
            if s.get("totals"):
                rows.append(s["totals"])
            for ci in numc:
                acc = 0.0
                for rr in s["rows"]:
                    if ci < len(rr) and isinstance(rr[ci], (int, float)):
                        acc += rr[ci]
                grand[ci] = round((grand[ci] if isinstance(grand[ci], (int, float)) else 0) + acc)
        sections.append({"key": key, "title": blocks[0][1]["title"], "columns": cols, "rows": rows,
                         "totals": grand, "note": note, "num_cols": numc, "flat": False})
    return {"found": True, "mode": "quality", "party": party, "party_code": pc, "quality": quality,
            "currency": cfg["company"]["currency"], "header": header, "sections": sections}


# ---- exports ---------------------------------------------------------------------------------

def _xlsx_sheets(rep):
    from ..reports import xlsx
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
        totals = s.get("totals")
        if totals:
            n = len(s["rows"])
            # LIVE Excel =SUM() totals for the numeric columns of a FLAT section (one row = one record). The merged
            # by-quality detail sections interleave sub-rows and per-contract sub-totals, so a plain SUM would double
            # count - those keep their pre-computed numeric totals (flat is False).
            if n and s.get("flat", True):
                totals = list(totals)
                for ci in (s.get("num_cols") or []):
                    if ci < len(totals) and isinstance(totals[ci], (int, float)):
                        L = xlsx.colref(ci, "")
                        totals[ci] = xlsx.Fx(f"=SUM({L}{{r-{n}}}:{L}{{r-1}})")
            rows = rows + [totals]
        note = [s["note"]] if s.get("note") else None
        sheets.append({"name": s["key"][:28], "title": s["title"], "headers": s["columns"], "rows": rows,
                       "note": note, "money_cols": s.get("num_cols")})
    return sheets


def write_xlsx(con, cfg, contract, folder, name, rep=None):
    from ..reports import xlsx
    rep = rep or contract_report(con, cfg, contract)
    return xlsx.write(Path(folder) / name, _xlsx_sheets(rep), cfg["company"]["name"])


def write_pdf(con, cfg, contract, folder, name, product="", rep=None):
    """A multi-section PDF: the contract header, then one table per section."""
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    from ..reports import fmt
    rep = rep or contract_report(con, cfg, contract)
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
