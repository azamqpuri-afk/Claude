"""
Contract closing: the party's yarn account, per contract and yarn.

    yarn received from the party (incl. opening) - yarn returned to the party
  - yarn consumed for the fabric (yarn per meter x meters, per the mill's method)
  = balance  (+ the mill holds the party's yarn, - the party is short / owes yarn)

Yarn per meter, the meters basis, what rejection counts as, allowances and
counts all come from the mill's own method (textile/method.py, config [closing]).
"""

from collections import defaultdict
from datetime import date, timedelta

from ..reports import Report
from .method import KG_PER_LB, Method


def contract_rows(con, method, as_of):
    """The party's yarn account per contract and yarn group. Balance = net yarn received from the party, less the
    party's yarn the mill can account for: fabric delivered to the party (all grades, net of returns) plus - when
    [closing].wip is on - the party's yarn still in the mill in another form (fresh fabric in stock, warp on the
    looms, sized beams waiting, yarn at a sizer, loose yarn in hand). A non-zero balance is the yarn to reconcile."""
    from .beams import learned_shrinkage
    d = as_of.isoformat()
    cfg = method.cfg
    go_live = (cfg.get("erp_start_date") or "").strip() or None
    exclude = {str(c) for c in cfg.get("exclude_contracts", [])}
    cmin, cmax = int(cfg.get("contract_min", 0) or 0), int(cfg.get("contract_max", 0) or 0)
    weft_by_q = {str(k): str(v) for k, v in (cfg.get("weft_yarn_by_quality") or {}).items()}
    owner_ret = {str(k): float(v) for k, v in (cfg.get("owner_return_meters") or {}).items()}
    wip_on = bool(cfg.get("wip", True))
    min_bags = float(cfg.get("at_sizer_min_bags", 3))

    contracts = con.execute("""SELECT contract_no, party_code, COALESCE(c.party_name, p.party_name, c.party_code), quality,
                                      ends_per_inch, picks_per_inch, width_in, warp_yarn, warp_count, weft_yarn, weft_count,
                                      ordered_meters, COALESCE(yarn_owner, 'party'), c.contract_date
                               FROM contracts c LEFT JOIN parties p USING (party_code)""").fetchall()
    names = {code: name for code, name in con.execute(
        "SELECT yarn_code, MAX(yarn_name) FROM yarn_movements WHERE yarn_name IS NOT NULL GROUP BY yarn_code")}
    party_of = {str(r[0]): r[1] for r in contracts}

    # yarn movements per contract + group: net received (receipts + opening - returns - sale), yarn at sizer, yarn in hand
    recv, at_sizer, in_hand = defaultdict(float), defaultdict(float), defaultdict(float)
    for cno, code, mt, kg in con.execute("""SELECT contract_no, yarn_code, move_type, SUM(kg) FROM yarn_movements
                                            WHERE move_date <= ? GROUP BY contract_no, yarn_code, move_type""", (d,)):
        g, kg, key = method.group(code), (kg or 0), (str(cno), method.group(code))
        if mt in ("party_receipt", "opening"):
            recv[key] += kg
        elif mt in ("party_return", "sale"):
            recv[key] -= kg
        if mt in ("party_receipt", "opening", "from_sizer", "from_looms"):
            in_hand[key] += kg
        elif mt in ("party_return", "sale", "to_sizer", "to_looms"):
            in_hand[key] -= kg
        if mt == "to_sizer":
            at_sizer[key] += kg
        elif mt in ("from_sizer", "sized_beams_received"):
            at_sizer[key] -= kg

    # fabric delivered to the PARTY, all grades net of returns: only gate passes billed to the contract's party,
    # plus a GPIN marked "...ADJUS..." (a stock-adjustment return the party made, booked to the mill store).
    party_meters = defaultdict(float)
    for cno, pcode, deliver, direction, m in con.execute("""SELECT contract_no, party_code, deliver_to, direction, SUM(meters)
            FROM fabric_dispatches WHERE dispatch_date <= ? GROUP BY contract_no, party_code, deliver_to, direction""", (d,)):
        cp = party_of.get(str(cno))
        to_party = pcode is not None and cp is not None and str(pcode) == str(cp)
        is_return_adjust = direction == "in" and "ADJUS" in str(deliver or "").upper()
        if to_party or is_return_adjust:
            party_meters[str(cno)] += (m or 0) * (1 if direction == "out" else -1)
    for cno, m in owner_ret.items():
        party_meters[str(cno)] -= m

    # fresh fabric in stock per contract = stock - C - CP (C and CP in stock are mill rejection, not the party's)
    fresh_stock = defaultdict(float)
    for cno, meters, cm, cpm in con.execute("SELECT contract_no, SUM(meters), SUM(c_m), SUM(cp_m) FROM fabric_stock GROUP BY contract_no"):
        fresh_stock[str(cno)] += (meters or 0) - (cm or 0) - (cpm or 0)

    # warp still tied up in beams (fabric-meters-equivalent), per contract; shrinkage is learned per beam set
    by_c, by_q, mill_shrink, qual = learned_shrinkage(con, cfg.get("default_shrinkage_pct", 7))
    bw = {str(c): (w or 1) for c, w in con.execute("SELECT contract_no, MAX(bwidth) FROM beams WHERE bwidth IS NOT NULL GROUP BY contract_no")}

    def shrink_of(cno):
        return by_c.get(cno) if cno in by_c else by_q.get(qual.get(cno), mill_shrink)

    warp_loom_m, sized_m = defaultdict(float), defaultdict(float)
    for cno, length, woven in con.execute("SELECT contract_no, set_length_m, woven_m FROM beams WHERE status = 'on_loom'"):
        warp_loom_m[str(cno)] += max(0.0, (length or 0) * (1 - shrink_of(cno)) - (woven or 0))   # ACTUAL_LENGTH already full
    for cno, length in con.execute("SELECT contract_no, set_length_m FROM beams WHERE status = 'sized_waiting'"):
        sized_m[str(cno)] += (length or 0) * bw.get(str(cno), 1) * (1 - shrink_of(cno))           # BLENGTH x bwidth

    woven = {str(c): last for c, last in con.execute("SELECT contract_no, MAX(prod_date) FROM loom_production WHERE prod_date <= ? GROUP BY contract_no", (d,))}
    rows = []
    for (cno, pcode, party, quality, epi, ppi, width, wy, wc, fy, fc, ordered, owner, cdate) in contracts:
        cno = str(cno)
        if owner == "mill" or cno in exclude:
            continue
        if cno.isdigit() and ((cmin and int(cno) < cmin) or (cmax and int(cno) > cmax)):
            continue
        pre_go_live = bool(go_live and cdate and str(cdate) < go_live)
        fix = method.contract_overrides(cno)
        epi, ppi, width = fix.get("ends_per_inch", epi), fix.get("picks_per_inch", ppi), fix.get("width_in", width)
        wy, fy = fix.get("warp_yarn", wy), fix.get("weft_yarn", fy)
        for qk, ycode in weft_by_q.items():                       # the sub-quality's real weft (ERP's is blank)
            if qk in str(quality or ""):
                fy = ycode
                break
        s = method.settings(cno, party)
        allow = 1 + float(s["wastage_allowance_pct"]) / 100
        warp_lbm = method.weight("warp", s, epi, width, method.closing_count("warp", wy, wc, s))
        weft_lbm = method.weight("weft", s, ppi, width, method.closing_count("weft", fy, fc, s))
        wg, fg = method.group(wy), method.group(fy)
        missing = [side for side, lbm in (("warp", warp_lbm), ("weft", weft_lbm)) if lbm is None]
        pm, fs = party_meters.get(cno, 0.0), fresh_stock.get(cno, 0.0)
        bag = float(s["bag_lb"])

        used = defaultdict(float)                                 # yarn group -> lb the party's yarn turned into
        if warp_lbm is not None:
            used[wg] += pm * warp_lbm * allow                     # warp yarn in the fabric delivered to the party
            if wip_on:
                used[wg] += fs * warp_lbm * allow + (warp_loom_m.get(cno, 0) + sized_m.get(cno, 0)) * warp_lbm
        if weft_lbm is not None:
            used[fg] += pm * weft_lbm * allow
            if wip_on:
                used[fg] += fs * weft_lbm * allow
        groups = {wg, fg} | {g for (c, g) in recv if c == cno} | {g for (c, g) in in_hand if c == cno} | {g for (c, g) in at_sizer if c == cno}
        last = woven.get(cno)
        status = "running" if last and last >= (as_of - timedelta(days=30)).isoformat() else "finished" if last else "no production"
        for g in sorted(groups, key=str):
            if wip_on:
                ah_lb = at_sizer.get((cno, g), 0.0) / KG_PER_LB
                if abs(ah_lb / bag) >= min_bags:
                    used[g] += ah_lb
                used[g] += in_hand.get((cno, g), 0.0) / KG_PER_LB
            rec_lb, used_lb = recv.get((cno, g), 0.0) / KG_PER_LB, used.get(g, 0.0)
            if abs(rec_lb) < 0.5 and abs(used_lb) < 0.5:
                continue
            bal_lb = rec_lb - used_lb
            rate = method.rate(g, s)
            note = "construction missing for " + " and ".join(missing) if missing else ""
            if pre_go_live:
                note = "started in the previous ERP - balance incomplete" + ("; " + note if note else "")
            rows.append(dict(contract=cno, party=party or pcode or "?", quality=quality, status=status, yarn=g,
                             yarn_name=_yarn_label(method, g, names), meters=pm, received_bags=rec_lb / bag,
                             used_bags=used_lb / bag, balance_bags=bal_lb / bag, balance_lb=bal_lb,
                             value=bal_lb * rate if rate else None, pre_go_live=pre_go_live, note=note))
    return rows


def _yarn_label(method, code, names):
    for g in method.cfg["yarn_groups"]:
        if str(g[0]) == str(code) and len(g) > 1:
            # name every member we can; never show a bare numeric code (e.g. "52/1 CTN + 18") - drop unknowns.
            parts = [names[str(c)] for c in g if str(c) in names and names[str(c)] != str(c)]
            if parts:
                return " + ".join(dict.fromkeys(parts))
    return names.get(str(code), names.get(code, code))


def contract_closing(con, cfg, as_of=None, party=None):
    as_of = as_of or date.today()
    method = Method(cfg["closing"])
    rows = contract_rows(con, method, as_of)
    if party:
        rows = [r for r in rows if party.lower() in (r["party"] or "").lower()]
    cur = cfg["company"]["currency"]
    incomplete = [r for r in rows if r.get("pre_go_live")]
    by_party = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        if r.get("pre_go_live"):
            continue                                # started in the old ERP: history incomplete, keep out of the totals
        by_party[r["party"]][0] += r["balance_bags"]
        by_party[r["party"]][1] += r["value"] or 0
    summary = [f"Party yarn balances as at {as_of:%d-%b-%Y} (+ = we hold the party's yarn, - = party is short):"]
    if incomplete:
        summary.append(f"{len({r['contract'] for r in incomplete})} contract(s) started in the previous ERP are listed "
                       "but NOT counted in the totals above - their yarn/fabric history is incomplete here.")
    summary += [f"  {p}: {b:+,.1f} bags" + (f" ({cur} {v:+,.0f})" if v else "") for p, (b, v) in sorted(by_party.items())]
    s = method.settings()
    summary.append(f"Method: {s['basis']} meters, rejection charged to {s['rejection_charged_to']}, divisor {s['divisor']:g}, "
                   f"bag {s['bag_lb']:g} lb, allowance {s['wastage_allowance_pct']:g}% (party / contract overrides apply).")
    return Report(f"Contract closing - {as_of:%d-%b-%Y}",
                  ["Contract", "Party", "Status", "Yarn", "Meters", "Received bags", "Used bags", "Balance bags",
                   f"Value {cur}", "Note"],
                  [[r["contract"], r["party"], r["status"], r["yarn_name"], round(r["meters"]), round(r["received_bags"], 1),
                    round(r["used_bags"], 1), round(r["balance_bags"], 1), r["value"], r["note"]] for r in rows], summary)
