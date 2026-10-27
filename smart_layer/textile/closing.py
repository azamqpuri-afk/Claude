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
    phys_ih = [(str(x.get("party", "")).lower(), method.group(str(x.get("yarn", ""))), float(x.get("bags", 0)))
               for x in (cfg.get("physical_in_hand") or [])]          # hook a: physical godown count per party+yarn
    rej_stock = {str(k).lower(): float(v) for k, v in (cfg.get("rejection_in_stock_m") or {}).items()}  # hook b
    unentered_on = bool(cfg.get("unentered_weft", False))             # hook c
    pooled_on = bool(cfg.get("pooled_shrinkage", False))              # hook d

    contracts = con.execute("""SELECT contract_no, party_code, COALESCE(c.party_name, p.party_name, c.party_code), quality,
                                      ends_per_inch, picks_per_inch, width_in, warp_yarn, warp_count, weft_yarn, weft_count,
                                      ordered_meters, COALESCE(yarn_owner, 'party'), c.contract_date
                               FROM contracts c LEFT JOIN parties p USING (party_code)""").fetchall()
    names = {code: name for code, name in con.execute(
        "SELECT yarn_code, MAX(yarn_name) FROM yarn_movements WHERE yarn_name IS NOT NULL GROUP BY yarn_code")}
    party_of = {str(r[0]): r[1] for r in contracts}
    pname_of = {str(r[0]): r[2] for r in contracts}               # contract -> party NAME (for party-level overrides)
    acct_of = {str(r[0]): (r[2], method.group(method.contract_overrides(str(r[0])).get("warp_yarn", r[7]))) for r in contracts}

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
    # A closing counts ONLY the fabric billed to the contract's OWN party. Fabric billed to a different party is
    # NOT silently dropped - it is listed as an exception (see third_party_deliveries) for the owner to settle.
    # The mill's own accounts (rejection / store) and any "...ADJUS..." stock-adjustment gate pass (either
    # direction) are the mill's, never the party's.
    mill_accounts = {str(a) for a in cfg.get("mill_accounts", ["200105006", "300308001"])}
    party_meters = defaultdict(float)                     # per contract (for warp)
    pm_subq = defaultdict(float)                          # per (contract, sub-quality) (weft can differ by sub-quality)
    for cno, quality, pcode, deliver, direction, m in con.execute("""SELECT contract_no, quality, party_code, deliver_to, direction, SUM(meters)
            FROM fabric_dispatches WHERE dispatch_date <= ? GROUP BY contract_no, quality, party_code, deliver_to, direction""", (d,)):
        dl = str(deliver or "").upper()
        if "ADJUS" in dl:
            continue                                      # stock adjustment - the mill's, not the party's, either way
        cp = party_of.get(str(cno))
        if pcode is not None and cp is not None and str(pcode) == str(cp):
            signed = (m or 0) * (1 if direction == "out" else -1)
            party_meters[str(cno)] += signed
            pm_subq[(str(cno), str(quality or ""))] += signed
        # billed to a mill rejection/store account or marked REJECTION -> mill's own; a different real party -> an
        # exception the owner settles separately. Neither is counted here.
    for cno, m in owner_ret.items():                     # a genuine party return the owner knows of, not yet in the ERP
        party_meters[str(cno)] -= m
        subs = [k for k in pm_subq if k[0] == str(cno)]
        if subs:
            pm_subq[max(subs, key=lambda k: pm_subq[k])] -= m

    # fresh fabric in stock = stock - C - CP (C and CP in stock are mill rejection, not the party's), per contract and sub-quality
    fresh_stock, fs_subq, full_stock, full_subq = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for cno, quality, meters, cm, cpm in con.execute("SELECT contract_no, quality, SUM(meters), SUM(c_m), SUM(cp_m) FROM fabric_stock GROUP BY contract_no, quality"):
        cno, k = str(cno), (str(cno), str(quality or ""))
        fresh_stock[cno] += (meters or 0) - (cm or 0) - (cpm or 0)
        fs_subq[k] += (meters or 0) - (cm or 0) - (cpm or 0)
        full_stock[cno] += (meters or 0)
        full_subq[k] += (meters or 0)
    # hook b: when a fixed rejection-in-stock is set for a party, use TOTAL fabric stock minus that fixed meters
    # (owner's physical rejection figure) instead of - C - CP
    for psub, rejm in rej_stock.items():
        cs = [c for c in {c for c, pn in pname_of.items() if psub in (pn or "").lower()} if full_stock.get(c)]
        for c in cs:
            fresh_stock[c] = full_stock[c]
            for key in [key for key in fs_subq if key[0] == c]:
                fs_subq[key] = full_subq.get(key, 0.0)
        if cs:
            big = max(cs, key=lambda c: full_stock[c])
            fresh_stock[big] -= rejm
            subs = [key for key in fs_subq if key[0] == big]
            if subs:
                fs_subq[max(subs, key=lambda key: fs_subq[key])] -= rejm

    # warp still tied up in beams (fabric-meters-equivalent), per contract; shrinkage is learned per beam set
    by_c, by_q, mill_shrink, qual = learned_shrinkage(con, cfg.get("default_shrinkage_pct", 7))
    bw = {str(c): (w or 1) for c, w in con.execute("SELECT contract_no, MAX(bwidth) FROM beams WHERE bwidth IS NOT NULL GROUP BY contract_no")}

    pooled = {}                                           # hook d: one shrinkage per (party, warp yarn) account
    if pooled_on:
        num, den = defaultdict(float), defaultdict(float)
        for cno, length, woven in con.execute("""SELECT m.contract_no, COALESCE(m.length_m, s.length_m), m.woven_m FROM beam_movements m
                LEFT JOIN (SELECT beam_no, set_no, MAX(length_m) length_m FROM beam_movements WHERE move_type = 'sized_received'
                           GROUP BY beam_no, set_no) s ON s.beam_no = m.beam_no AND s.set_no = m.set_no
                WHERE m.move_type = 'from_loom_finished' AND m.woven_m > 0"""):
            k = acct_of.get(str(cno))
            L = (length or 0) * bw.get(str(cno), 1)
            if k and L > 0 and woven and 0.01 <= (1 - woven / L) <= 0.20:
                num[k] += woven
                den[k] += L
        pooled = {k: 1 - num[k] / den[k] for k in den if den[k] > 0}

    def shrink_of(cno):
        if pooled_on and acct_of.get(cno) in pooled:
            return pooled[acct_of[cno]]
        return by_c.get(cno) if cno in by_c else by_q.get(qual.get(cno), mill_shrink)

    # hook c: weft for fabric woven on a loom but not yet entered (per contract)
    unentered_m = defaultdict(float)
    if unentered_on:
        since30 = (as_of - timedelta(days=30)).isoformat()
        loom_rate = {str(l): (mm or 0) / 30 for l, mm in con.execute(
            "SELECT loom_code, SUM(meters) FROM loom_production WHERE prod_date > ? AND prod_date <= ? GROUP BY loom_code", (since30, d))}
        for cno, loom, mount in con.execute("SELECT contract_no, loom_code, mounted_date FROM beams WHERE status = 'on_loom' AND loom_code IS NOT NULL"):
            last = con.execute("SELECT MAX(prod_date) FROM loom_production WHERE loom_code = ? AND contract_no = ? AND prod_date <= ?",
                               (loom, str(cno), d)).fetchone()[0] or mount
            if not last:
                continue
            days = (as_of - date.fromisoformat(str(last)[:10])).days
            if days > 0:
                unentered_m[str(cno)] += days * loom_rate.get(str(loom), 0)

    warp_loom_m, sized_m = defaultdict(float), defaultdict(float)
    for cno, length, woven in con.execute("SELECT contract_no, set_length_m, woven_m FROM beams WHERE status = 'on_loom'"):
        warp_loom_m[str(cno)] += max(0.0, (length or 0) * (1 - shrink_of(cno)) - (woven or 0))   # ACTUAL_LENGTH already full
    for cno, length in con.execute("SELECT contract_no, set_length_m FROM beams WHERE status = 'sized_waiting'"):
        sized_m[str(cno)] += (length or 0) * bw.get(str(cno), 1) * (1 - shrink_of(cno))           # BLENGTH x bwidth

    woven = {str(c): last for c, last in con.execute("SELECT contract_no, MAX(prod_date) FROM loom_production WHERE prod_date <= ? GROUP BY contract_no", (d,))}
    rows, phys_applied = [], set()
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

        def sub_weft(subq):                                       # the weft a sub-quality really uses (ERP master blank)
            for qk, ycode in weft_by_q.items():
                if qk in str(subq or ""):
                    return ycode
            return fy
        s = method.settings(cno, party)
        allow = 1 + float(s["wastage_allowance_pct"]) / 100
        warp_lbm = method.weight("warp", s, epi, width, method.closing_count("warp", wy, wc, s))
        wg = method.group(wy)
        pm, fs = party_meters.get(cno, 0.0), fresh_stock.get(cno, 0.0)
        bag = float(s["bag_lb"])

        used = defaultdict(float)                                 # yarn group -> lb the party's yarn turned into
        if warp_lbm is not None:
            used[wg] += pm * warp_lbm * allow                     # warp yarn in the fabric delivered to the party
            if wip_on:
                used[wg] += fs * warp_lbm * allow + (warp_loom_m.get(cno, 0) + sized_m.get(cno, 0)) * warp_lbm
        # weft: charge each sub-quality to the weft it really uses (31/34 -> 125), not one weft for the whole contract
        subqs = {q for (c, q) in pm_subq if c == cno} | {q for (c, q) in fs_subq if c == cno} or {str(quality or "")}
        weft_missing = False
        for subq in subqs:
            sfy = sub_weft(subq)
            wlbm = method.weight("weft", s, ppi, width, method.closing_count("weft", sfy, fc, s))
            if wlbm is None:
                weft_missing = True
                continue
            wm = pm_subq.get((cno, subq), 0.0) + (fs_subq.get((cno, subq), 0.0) if wip_on else 0.0)
            if subq == str(quality or "") and not pm_subq and not fs_subq:
                wm = pm + (fs if wip_on else 0.0)                 # no sub-quality rows: fall back to the contract total
            used[method.group(sfy)] += wm * wlbm * allow
        if unentered_on and unentered_m.get(cno):                 # hook c: weft woven but not yet entered (weft only)
            wlbm_main = method.weight("weft", s, ppi, width, method.closing_count("weft", fy, fc, s))
            if wlbm_main is not None:
                used[method.group(fy)] += unentered_m[cno] * wlbm_main * allow
        missing = (["warp"] if warp_lbm is None else []) + (["weft"] if weft_missing else [])
        groups = {wg} | {method.group(sub_weft(q)) for q in subqs} | {g for (c, g) in recv if c == cno} | {g for (c, g) in in_hand if c == cno} | {g for (c, g) in at_sizer if c == cno}
        last = woven.get(cno)
        status = "running" if last and last >= (as_of - timedelta(days=30)).isoformat() else "finished" if last else "no production"
        for g in sorted(groups, key=str):
            if wip_on:
                ah_lb = at_sizer.get((cno, g), 0.0) / KG_PER_LB
                if abs(ah_lb / bag) >= min_bags:
                    used[g] += ah_lb
                # hook a: a physical godown count for this party+yarn replaces the ERP book, applied once per
                # party+yarn across the party's contracts (the book swings a lot; the physical count is the truth).
                phys = next((b for ps, yg, b in phys_ih if ps in (party or "").lower() and yg == g), None)
                if phys is not None:
                    if (party, g) not in phys_applied:
                        used[g] += phys * bag
                        phys_applied.add((party, g))
                else:
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


def third_party_deliveries(con, method, as_of):
    """Fabric of a contract delivered/billed to a DIFFERENT party than the contract's own (not the mill's
    rejection/store accounts, not an ADJUS stock correction). These are kept OUT of the party's closing and listed
    so the owner can settle each one (e.g. move yarn to that party's account). Returns rows with a bag equivalent."""
    d = as_of.isoformat()
    cfg = method.cfg
    # A line is the MILL's only when it is a rejection (DELIVER_TO contains REJECT) or the rejection posting account.
    # A store-account line (e.g. 200105006) that names a real customer in DELIVER_TO is that customer's, and is
    # listed as an exception under the DELIVER_TO name - not dropped as the mill's.
    reject_accounts = {str(a) for a in cfg.get("mill_accounts", ["300308001"])}
    info = {}                                             # contract -> (own party code, warp+weft lb/m, bag_lb, own party name)
    pname = {str(c): n for c, n in con.execute("SELECT party_code, COALESCE(party_name, party_code) FROM parties")}
    for cno, pcode, pn, epi, ppi, width, wy, wc, fy, fc in con.execute("""SELECT contract_no, party_code,
            COALESCE(c.party_name, p.party_name, c.party_code), ends_per_inch, picks_per_inch, width_in,
            warp_yarn, warp_count, weft_yarn, weft_count FROM contracts c LEFT JOIN parties p USING (party_code)"""):
        cno = str(cno)
        s = method.settings(cno, pn)
        fix = method.contract_overrides(cno)
        epi, ppi, width = fix.get("ends_per_inch", epi), fix.get("picks_per_inch", ppi), fix.get("width_in", width)
        wlbm = method.weight("warp", s, epi, width, method.closing_count("warp", fix.get("warp_yarn", wy), wc, s)) or 0
        flbm = method.weight("weft", s, ppi, width, method.closing_count("weft", fix.get("weft_yarn", fy), fc, s)) or 0
        info[cno] = (str(pcode) if pcode is not None else None, wlbm + flbm, float(s["bag_lb"]), pn)
    agg = defaultdict(lambda: [0.0, 0.0, ""])             # (contract, other party) -> [out m, back m, deliver_to]
    for cno, pcode, deliver, direction, m in con.execute("""SELECT contract_no, party_code, deliver_to, direction, SUM(meters)
            FROM fabric_dispatches WHERE dispatch_date <= ? GROUP BY contract_no, party_code, deliver_to, direction""", (d,)):
        cno, dl = str(cno), str(deliver or "").upper()
        own = info.get(cno, (None,))[0]
        if "ADJUS" in dl or "REJECT" in dl or str(pcode) in reject_accounts:
            continue                                      # the mill's own stock adjustment / rejection
        if own is not None and pcode is not None and str(pcode) == own:
            continue                                      # the contract's own party - that is the closing itself
        # key by the delivered-to name so a store account billed for several customers lists each one separately
        who = str(deliver).strip() if deliver else (str(pcode) if pcode is not None else "?")
        a = agg[(cno, who)]
        a[0 if direction == "out" else 1] += m or 0
        if deliver:
            a[2] = deliver
    rows = []
    for (cno, who), (out, back, deliver) in sorted(agg.items(), key=lambda kv: (-abs(kv[1][0] - kv[1][1]), kv[0])):
        if out < 1 and back < 1:
            continue
        _, lbm, bag, own_name = info.get(cno, (None, 0, 100, None))
        net = out - back
        rows.append(dict(contract=cno, own_party=own_name, party=pname.get(who, who),
                         out_m=out, back_m=back, net_m=net, bags=(net * lbm / bag if lbm else None)))
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
    # one balance line per PARTY and YARN - never add different yarns into one bag total (bags of 30/1 and 12/1 are
    # not the same thing). A rupee total across a party's yarns is fine and is shown as a sub-total per party.
    by_py = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        if r.get("pre_go_live"):
            continue                                # started in the old ERP: history incomplete, keep out of the totals
        by_py[(r["party"], r["yarn_name"])][0] += r["balance_bags"]
        by_py[(r["party"], r["yarn_name"])][1] += r["value"] or 0
    summary = [f"Party yarn balances as at {as_of:%d-%b-%Y} (+ = we hold the party's yarn, - = party is short):"]
    if incomplete:
        summary.append(f"{len({r['contract'] for r in incomplete})} contract(s) started in the previous ERP are listed "
                       "but NOT counted in the totals above - their yarn/fabric history is incomplete here.")
    last_party = None
    for (p, y), (b, v) in sorted(by_py.items()):
        if p != last_party:
            tot_v = sum(vv for (pp, _), (_, vv) in by_py.items() if pp == p)
            summary.append(f"  {p}:" + (f"  (net {cur} {tot_v:+,.0f})" if tot_v else ""))
            last_party = p
        summary.append(f"      {y}: {b:+,.1f} bags" + (f" ({cur} {v:+,.0f})" if v else ""))
    # Fabric of a contract billed to ANOTHER party: not in the closing above; listed so the owner can settle each.
    exc = third_party_deliveries(con, method, as_of)
    if party:
        exc = [e for e in exc if party.lower() in (str(e.get("own_party") or "")).lower()]
    if exc:
        summary.append("Fabric of a contract delivered/billed to ANOTHER party (not counted above - settle separately):")
        for e in exc:
            line = f"      c{e['contract']} ({e['own_party']}) -> {e['party']}: {e['out_m']:,.0f} m out"
            if e["back_m"]:
                line += f", {e['back_m']:,.0f} m back"
            if e["bags"] is not None:
                line += f"  (~{e['bags']:+,.1f} bags of yarn)"
            summary.append(line)
    s = method.settings()
    summary.append(f"Method: dispatched meters, divisor {s['divisor']:g}, bag {s['bag_lb']:g} lb, "
                   f"allowance {s['wastage_allowance_pct']:g}%, WIP {'on' if method.cfg.get('wip', True) else 'off'} "
                   f"(party / contract overrides apply).")
    return Report(f"Contract closing - {as_of:%d-%b-%Y}",
                  ["Contract", "Party", "Status", "Yarn", "Meters", "Received bags", "Used bags", "Balance bags",
                   f"Value {cur}", "Note"],
                  [[r["contract"], r["party"], r["status"], r["yarn_name"], round(r["meters"]), round(r["received_bags"], 1),
                    round(r["used_bags"], 1), round(r["balance_bags"], 1), r["value"], r["note"]] for r in rows], summary)
