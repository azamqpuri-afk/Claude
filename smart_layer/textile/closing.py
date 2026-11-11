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


def balance_phrase(party, bags, value=None, cur="Rs", yarn=None):
    """A plain sentence for a yarn balance, instead of a signed number the owner has to decode.
    Negative = the party owes the mill; positive = the mill holds the party's yarn."""
    n = abs(bags)
    yq = f"{yarn} " if yarn else ""
    if bags < -0.05:
        s = f"{party} owes the mill {n:,.1f} bags" + (f" of {yarn}" if yarn else " of yarn")
    elif bags > 0.05:
        s = f"The mill holds {n:,.1f} bags of {party}'s {yq}yarn".replace("'s  ", "'s ")
    else:
        s = f"{party} is square ({yq}0 bags)"
    if value:
        s += f" ({cur} {abs(value):,.0f})"
    return s


def balance_side(bags):
    """A short label for the Balance column: which way the yarn is owed."""
    if bags < -0.05:
        return "owes mill"
    if bags > 0.05:
        return "mill holds"
    return "square"


def _fabric_counts(con):
    """Latest physical fabric count per (contract, quality) from manual_inputs scope 'fabric_count'. The key is
    'contract|quality'; fields are grade meters (C, CP, FRESH, B, PRINT, REJECTION ...) plus 'as_on' (a date stored
    as its ordinal). Returns {(contract, quality): {'grades': {GRADE: meters}, 'as_on': 'YYYY-MM-DD' | None}}."""
    from datetime import date as _date

    from .. import manual as _manual
    out = {}
    try:
        latest = _manual.latest(con, "fabric_count")
    except Exception:
        return out
    for key, fields in latest.items():
        cno, _, quality = str(key).partition("|")
        grades, as_on = {}, None
        for f, v in fields.items():
            if f == "as_on":
                try:
                    as_on = _date.fromordinal(int(v)).isoformat()
                except (TypeError, ValueError, OverflowError):
                    as_on = None
            elif v is not None:
                grades[str(f).upper()] = float(v)
        out[(cno, quality)] = {"grades": grades, "as_on": as_on}
    return out


def _physical_rejection(con, cno, quality, cnt, rej_grades, as_of):
    """The physical rejection meters for a contract+quality now = the counted rejection rolled forward from the
    count date: + rejection graded in ERP production since, - rejection dispatched (OGP) since. Counted rejection is
    the 'REJECTION' field if entered, else the sum of the configured rejection grades (default C + CP)."""
    grades = cnt.get("grades") or {}
    counted = grades["REJECTION"] if "REJECTION" in grades else sum(grades.get(g, 0.0) for g in rej_grades)
    as_on = cnt.get("as_on")
    if not as_on:
        return max(0.0, counted)
    qcl, qp = ((" AND COALESCE(quality,'') = ?", [quality]) if quality else ("", []))
    prod = con.execute(f"""SELECT SUM(COALESCE(rejected_m, 0)) FROM loom_production
                          WHERE contract_no = ?{qcl} AND prod_date > ? AND prod_date <= ?""",
                       [cno, *qp, as_on, as_of.isoformat()]).fetchone()[0] or 0
    disp = con.execute(f"""SELECT SUM(COALESCE(rejected_m, 0)) FROM fabric_dispatches
                          WHERE contract_no = ?{qcl} AND direction = 'out' AND dispatch_date > ? AND dispatch_date <= ?""",
                       [cno, *qp, as_on, as_of.isoformat()]).fetchone()[0] or 0
    return max(0.0, counted + prod - disp)


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

    # fresh fabric in stock = stock - rejection in stock, per contract and sub-quality. Rejection is normally the
    # ERP's C + CP, BUT for a contract+quality with an owner PHYSICAL COUNT (item 3) it is the counted rejection
    # rolled forward from the count date (+ produced since, - dispatched since) - the physical count is the truth.
    counts = _fabric_counts(con)
    rej_grades = [str(g).upper() for g in (cfg.get("rejection_grades") or ["C", "CP"])]
    counted_contracts = {c for (c, q) in counts}
    cdate_of = {str(r[0]): r[13] for r in contracts}
    fresh_stock, fs_subq, full_stock, full_subq = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    for cno, quality, meters, cm, cpm in con.execute("SELECT contract_no, quality, SUM(meters), SUM(c_m), SUM(cp_m) FROM fabric_stock GROUP BY contract_no, quality"):
        cno, q = str(cno), str(quality or "")
        k, meters = (cno, q), (meters or 0)
        cnt = counts.get((cno, q)) or counts.get((cno, ""))        # exact quality, else a contract-level count
        if cnt:
            rej = _physical_rejection(con, cno, (q if (cno, q) in counts else ""), cnt, rej_grades, as_of)
            fresh = meters - rej
        else:
            fresh = meters - (cm or 0) - (cpm or 0)
        fresh_stock[cno] += fresh
        fs_subq[k] += fresh
        full_stock[cno] += meters
        full_subq[k] += meters
    # hook b (back-compat): a single fixed rejection-in-stock figure per party. Kept only as a fallback for a simple
    # single-contract party - it is SKIPPED for any contract that now has a per-contract count, for pre-go-live or
    # negative-stock contracts, and for multi-contract parties (where one figure landed on the wrong contract).
    stock_contracts = {c for c in full_stock if full_stock.get(c, 0) > 0}
    party_contracts = defaultdict(set)
    for c in stock_contracts:
        party_contracts[(pname_of.get(c) or "").lower()].add(c)
    for psub, rejm in rej_stock.items():
        cs = [c for c in {c for c, pn in pname_of.items() if psub in (pn or "").lower()}
              if full_stock.get(c, 0) > 0 and c not in counted_contracts
              and not (go_live and cdate_of.get(c) and str(cdate_of[c]) < go_live)]
        parties = {(pname_of.get(c) or "").lower() for c in cs}
        multi = any(len(party_contracts.get(pn, set())) > 1 for pn in parties)
        if not cs or multi:
            continue                                               # multi-contract party: use per-contract counts instead
        for c in cs:
            fresh_stock[c] = full_stock[c]
            for key in [key for key in fs_subq if key[0] == c]:
                fs_subq[key] = full_subq.get(key, 0.0)
        big = max(cs, key=lambda c: full_stock[c])
        fresh_stock[big] -= rejm
        subs = [key for key in fs_subq if key[0] == big]
        if subs:
            # take the rejection out of the contract's OWN-weft cloth, not a sub-quality remapped to another
            # weft (e.g. 31/34 -> 125), so it reduces the right yarn's stock
            own = [key for key in subs if not any(qk in key[1] for qk in weft_by_q)]
            fs_subq[max(own or subs, key=lambda key: fs_subq[key])] -= rejm

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
        if unentered_on and wip_on and unentered_m.get(cno):      # hook c: weft woven but not yet entered (weft only; WIP)
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
    store_accounts = {str(a) for a in cfg.get("store_accounts", ["200105006"])}   # billed to a store, not a party
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
        # key by the BILLED party's name (who it is really for); only for a store account, where many customers
        # share one account, fall back to the DELIVER_TO name so each customer lists separately
        if pcode is not None and str(pcode) in store_accounts:
            who = str(deliver).strip() if deliver else (pname.get(str(pcode)) or "store account")
        else:
            who = pname.get(str(pcode)) or (str(deliver).strip() if deliver else "?")
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
        rows.append(dict(contract=cno, own_party=own_name, party=who, deliver_to=(deliver or "").strip(),
                         out_m=out, back_m=back, net_m=net, bags=(net * lbm / bag if lbm else None)))
    return rows


def fabric_count_review(con, cfg, as_of=None):
    """ERP vs the last physical count per contract + quality (item 3 review): ERP total stock, the ERP's own
    rejection (C+CP), the physical rejection rolled forward from the count date, the 'as on' date, and the
    difference. One row per contract+quality that has stock or a count, biggest difference first."""
    as_of = as_of or date.today()
    ccfg = cfg["closing"]
    rej_grades = [str(g).upper() for g in (ccfg.get("rejection_grades") or ["C", "CP"])]
    counts = _fabric_counts(con)
    pname = {str(c): n for c, n in con.execute("SELECT contract_no, COALESCE(party_name, party_code) FROM contracts")}
    stock = {}
    for cno, quality, meters, cm, cpm in con.execute(
            "SELECT contract_no, quality, SUM(meters), SUM(c_m), SUM(cp_m) FROM fabric_stock GROUP BY contract_no, quality"):
        stock[(str(cno), str(quality or ""))] = ((meters or 0), (cm or 0) + (cpm or 0))
    rows = []
    for (cno, quality) in sorted(set(stock) | set(counts)):
        meters, erp_rej = stock.get((cno, quality), (0, 0))
        cnt = counts.get((cno, quality))
        phys = _physical_rejection(con, cno, quality, cnt, rej_grades, as_of) if cnt else None
        rows.append({"contract": cno, "party": pname.get(cno), "quality": quality,
                     "erp_stock": round(meters), "erp_rejection": round(erp_rej),
                     "physical_rejection": (round(phys) if phys is not None else None),
                     "as_on": (cnt or {}).get("as_on"),
                     "difference": (round(phys - erp_rej) if phys is not None else None)})
    rows.sort(key=lambda r: -(r["difference"] if r["difference"] is not None else -1e9))
    return {"date": as_of.isoformat(), "rejection_grades": rej_grades, "rows": rows,
            "counted": sum(1 for r in rows if r["physical_rejection"] is not None)}


def _yarn_label(method, code, names):
    for g in method.cfg["yarn_groups"]:
        if str(g[0]) == str(code) and len(g) > 1:
            # name every member we can; never show a bare numeric code (e.g. "52/1 CTN + 18") - drop unknowns.
            parts = [names[str(c)] for c in g if str(c) in names and names[str(c)] != str(c)]
            if parts:
                return " + ".join(dict.fromkeys(parts))
    return names.get(str(code), names.get(code, code))


def closing_counts(con, cfg, as_of=None, party=None, quality=None):
    """The warp/weft closing count (and rate) in effect for each yarn in a party's closing, prefilled so the owner
    can edit and recompute. One row per yarn group. For each row:
      warp/weft_closing_count  the effective count (party count if one applies, else the yarn default)
      warp/weft_default        the yarn-level default (what a party count would fall back to if cleared)
      source                   where the effective count comes from: "party", "yarn" (an owner-saved default) or
                               "config" (config.toml, or derived from the yarn count when blank)
      party_clearable          a party-specific count for the selected party is saved in the side file (can be cleared)
    A blank count means the closing derives it from the yarn's own count."""
    from .. import config as _config
    as_of = as_of or date.today()
    method = Method(cfg["closing"])
    rows = contract_rows(con, method, as_of)
    if party:
        rows = [r for r in rows if party.lower() in (r["party"] or "").lower()]
    qualities = sorted({r["quality"] for r in rows if r.get("quality")})   # the qualities this party runs
    if quality:
        rows = [r for r in rows if str(r.get("quality") or "") == str(quality)]
    # party counts are per EXACT party name; resolve the filter to the real party name(s) in view. A party-level
    # count / clear is only meaningful (and only offered) when the filter picks out exactly one real party.
    matched = sorted({r["party"] for r in rows if r.get("party")})
    exact = matched[0] if len(matched) == 1 else None
    pov = method.settings(party=exact).get("_yarns", {}) if exact else {}
    try:
        side = _config.read_closing_sidecar(cfg.base_dir)
    except Exception:
        side = {}
    saved_yarns = {str(c) for c in (side.get("yarns") or {})}
    saved_party = {str(c) for name, pv in (side.get("parties") or {}).items()
                   if exact and exact.strip().lower() == str(name).strip().lower()
                   for c in ((pv or {}).get("yarns") or {})}
    seen = {}
    for r in rows:
        if r.get("pre_go_live"):
            continue
        g = str(r["yarn"])
        if g in seen:
            continue
        default = method.yarn(g)                              # yarn-level (config.yarns + saved [yarns])
        eff = {**default, **pov.get(g, {})}                   # party count wins
        has_party = g in pov and any(k in pov[g] for k in ("warp_closing_count", "weft_closing_count", "closing_count"))
        has_default = any(k in default for k in ("warp_closing_count", "weft_closing_count", "closing_count"))
        source = "party" if has_party else ("yarn" if g in saved_yarns else ("config" if has_default else "derived"))
        seen[g] = {"yarn": g, "yarn_name": r["yarn_name"], "rate_per_lb": eff.get("rate_per_lb"),
                   "warp_closing_count": eff.get("warp_closing_count", eff.get("closing_count")),
                   "weft_closing_count": eff.get("weft_closing_count", eff.get("closing_count")),
                   "warp_default": default.get("warp_closing_count", default.get("closing_count")),
                   "weft_default": default.get("weft_closing_count", default.get("closing_count")),
                   "source": source, "party_override": has_party, "party_clearable": g in saved_party}
    # fabric of these contracts billed to ANOTHER party (shown as a separate table, not lumped into the balance).
    # When a quality is selected, restrict it to that quality's contracts (not every contract of the party).
    sel_contracts = {str(r["contract"]) for r in rows}
    tp = [e for e in third_party_deliveries(con, method, as_of)
          if ((not party) or party.lower() in str(e.get("own_party") or "").lower())
          and ((not quality) or str(e["contract"]) in sel_contracts)]
    third_party = [{"contract": e["contract"], "own_party": e["own_party"], "party": e["party"],
                    "deliver_to": e["deliver_to"], "out_m": round(e["out_m"]), "back_m": round(e["back_m"]),
                    "net_m": round(e["net_m"]), "bags": (round(e["bags"], 1) if e["bags"] is not None else None)}
                   for e in tp]
    return {"party": party or "", "quality": quality or "", "date": as_of.isoformat(),
            "matched_parties": matched, "exact_party": exact or "", "qualities": qualities,
            "third_party": third_party,
            "yarns": sorted(seen.values(), key=lambda x: (x["yarn_name"] or x["yarn"]))}


def contract_closing(con, cfg, as_of=None, party=None, count_overrides=None, quality=None):
    as_of = as_of or date.today()
    method = Method(cfg["closing"])
    if count_overrides:                                        # dashboard what-if: these counts win over config
        method.count_overrides = {str(k): dict(v) for k, v in count_overrides.items() if isinstance(v, dict)}
    rows = contract_rows(con, method, as_of)
    if party:
        rows = [r for r in rows if party.lower() in (r["party"] or "").lower()]
    if quality:                                               # close one quality across the party's contracts
        rows = [r for r in rows if str(r.get("quality") or "") == str(quality)]
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
    summary = [f"Party yarn balances as at {as_of:%d-%b-%Y} - \"owes the mill\" = the party is short; "
               "\"the mill holds\" = we are keeping the party's yarn:"]
    if incomplete:
        summary.append(f"{len({r['contract'] for r in incomplete})} contract(s) started in the previous ERP are listed "
                       "but NOT counted in the totals above - their yarn/fabric history is incomplete here.")
    last_party = None
    for (p, y), (b, v) in sorted(by_py.items()):
        if p != last_party:
            tot_v = sum(vv for (pp, _), (_, vv) in by_py.items() if pp == p)
            summary.append(f"  {p}:" + (f"  (net {cur} {tot_v:+,.0f})" if tot_v else ""))
            last_party = p
        summary.append("      " + balance_phrase(p, b, v, cur, yarn=y))
    # Fabric of a contract billed to ANOTHER party: not in the closing above; listed so the owner can settle each.
    exc = third_party_deliveries(con, method, as_of)
    if party:
        exc = [e for e in exc if party.lower() in (str(e.get("own_party") or "")).lower()]
    if quality:                                           # only this quality's contracts, matching the filtered rows
        sel = {str(r["contract"]) for r in rows}
        exc = [e for e in exc if str(e["contract"]) in sel]
    if exc:
        summary.append("Fabric of a contract delivered/billed to ANOTHER party (not counted above - settle separately):")
        for e in exc:
            line = f"      c{e['contract']} ({e['own_party']}) -> {e['party']}: {e['out_m']:,.0f} m out"
            if e["back_m"]:
                line += f", {e['back_m']:,.0f} m back"
            if e["bags"] is not None:
                line += f"  (~{e['bags']:+,.1f} bags of yarn)"
            if e.get("deliver_to") and e["deliver_to"].lower() not in (e["party"] or "").lower():
                line += f"  [to {e['deliver_to']}]"
            summary.append(line)
    s = method.settings()
    summary.append(f"Method: dispatched meters, divisor {s['divisor']:g}, bag {s['bag_lb']:g} lb, "
                   f"allowance {s['wastage_allowance_pct']:g}%, WIP {'on' if method.cfg.get('wip', True) else 'off'} "
                   f"(party / contract overrides apply).")
    whatif = ""
    if method.count_overrides:                                 # a dashboard recompute: mark it clearly, never hand it out as the real closing
        names = {code: nm for code, nm in con.execute(
            "SELECT yarn_code, MAX(yarn_name) FROM yarn_movements WHERE yarn_name IS NOT NULL GROUP BY yarn_code")}
        parts = []
        for code, ov in method.count_overrides.items():
            bits = [f"{side} {float(ov[k]):g}" for side, k in (("warp", "warp_closing_count"), ("weft", "weft_closing_count"))
                    if ov.get(k) is not None]
            if bits:
                parts.append(f"{_yarn_label(method, method.group(code), names)} {', '.join(bits)}")
        scope = f" for {party}" if party else " (all parties)"
        summary.insert(0, f"WHAT-IF counts, NOT the official closing{scope}: " + "; ".join(parts))
        whatif = " (what-if)"
    return Report(f"Contract closing - {as_of:%d-%b-%Y}{whatif}",
                  ["Contract", "Party", "Status", "Yarn", "Meters", "Received bags", "Used bags", "Balance bags",
                   "Balance", f"Value {cur}", "Note"],
                  [[r["contract"], r["party"], r["status"], r["yarn_name"], round(r["meters"]), round(r["received_bags"], 1),
                    round(r["used_bags"], 1), round(r["balance_bags"], 1), balance_side(r["balance_bags"]),
                    r["value"], r["note"]] for r in rows], summary)


# ---- #6 yarn-closing sheet (party + yarn + contract; IGP yarn and OGP fabric date-wise) -----------------------
def contract_list(con, party=None):
    """Contracts for the closing picker: [{contract, party, quality, date}], optionally filtered to one party
    (substring, case-insensitive). Most recent first."""
    p = (party or "").strip().lower()
    out = []
    for cno, pn, q, cd in con.execute(
            "SELECT contract_no, party_name, quality, contract_date FROM contracts ORDER BY contract_date DESC"):
        if p and p not in str(pn or "").lower():
            continue
        out.append({"contract": cno, "party": pn, "quality": q, "date": cd})
    return out


def closing_sheet(con, cfg, party=None, contract=None, quality=None):
    """A neat per-contract closing sheet: IGP yarn date-wise (date, doc, bags, kg), OGP fabric date-wise (date,
    doc, metres), the warp/weft weight & count used, and totals. mode is implied by the arguments:
      contract set            -> that one contract
      party + quality set     -> every contract of that party and quality, combined (lines still per contract)
      party set only          -> every contract of that party
    Returns a dict (served at /api/closing/sheet); a live-formula workbook is built by the caller if exporting."""
    # choose contracts
    conds, params = [], []
    if contract:
        conds.append("contract_no = ?"); params.append(contract)
    if party:
        conds.append("LOWER(party_name) LIKE ?"); params.append(f"%{party.strip().lower()}%")
    if quality:
        conds.append("quality = ?"); params.append(quality)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    meta = con.execute(f"""SELECT contract_no, party_name, quality, ends_per_inch, picks_per_inch, width_in,
                           warp_count, weft_count, rate_per_meter FROM contracts{where}""", params).fetchall()
    if not meta:
        return {"contracts": [], "igp": [], "ogp": [], "counts": {}, "totals": {}}
    cnos = [m[0] for m in meta]
    qmarks = ",".join("?" * len(cnos))
    igp = [{"date": a, "doc_no": b, "bags": round(c or 0, 1), "kg": round(d or 0)} for a, b, c, d in con.execute(
        f"""SELECT move_date, doc_no, SUM(COALESCE(bags,0)), SUM(kg) FROM yarn_movements
            WHERE contract_no IN ({qmarks}) AND move_type IN ('party_receipt','sized_beams_received')
            GROUP BY move_date, doc_no ORDER BY move_date""", cnos)]
    ogp = [{"date": a, "doc_no": b, "meters": round(c or 0), "grade": "fresh"} for a, b, c in con.execute(
        f"""SELECT dispatch_date, doc_no, SUM(CASE direction WHEN 'out' THEN 1 ELSE -1 END * meters) FROM fabric_dispatches
            WHERE contract_no IN ({qmarks}) GROUP BY dispatch_date, doc_no ORDER BY dispatch_date""", cnos)]
    m0 = meta[0]
    # the mill's own method balance for the selected contracts (same engine as `report closing`), plus the CLOSING
    # counts actually used (config overrides the nominal count), so the screen shows the authoritative figure.
    method = Method(cfg["closing"])
    cset = {str(x) for x in cnos}
    mrows = [r for r in contract_rows(con, method, date.today()) if str(r["contract"]) in cset]
    # the CLOSING count used for each yarn GROUP across the selected contracts (warp group -> warp count, weft
    # group -> weft count); config [yarns]/[parties] override the nominal count.
    cbg = {}
    for cc in cset:
        cr = con.execute("SELECT warp_yarn, warp_count, weft_yarn, weft_count FROM contracts WHERE contract_no = ?", (cc,)).fetchone()
        if not cr:
            continue
        st = method.settings(cc, None)
        cbg.setdefault(method.group(cr[0]), method.closing_count("warp", cr[0], cr[1], st))
        cbg.setdefault(method.group(cr[2]), method.closing_count("weft", cr[2], cr[3], st))
    st0 = method.settings(m0[0], None)
    crow = con.execute("SELECT warp_yarn, warp_count, weft_yarn, weft_count FROM contracts WHERE contract_no = ?", (m0[0],)).fetchone()
    warp_cc = method.closing_count("warp", crow[0], crow[1], st0) if crow else None
    weft_cc = method.closing_count("weft", crow[2], crow[3], st0) if crow else None
    # per-yarn balance rows (one per yarn group, summed across the selected contracts), as the closing report shows.
    per = {}
    for r in mrows:
        gkey = r["yarn"]
        a = per.setdefault(gkey, {"yarn": gkey, "yarn_name": r["yarn_name"], "received_bags": 0.0, "used_bags": 0.0,
                                  "balance_lb": 0.0})
        a["received_bags"] += r["received_bags"] or 0
        a["used_bags"] += r["used_bags"] or 0
        a["balance_lb"] += r["balance_lb"] or 0
    bag_lb = float(st0["bag_lb"])
    balance_rows = [{"yarn": a["yarn"], "yarn_name": a["yarn_name"], "count": cbg.get(a["yarn"]),
                     "received_bags": round(a["received_bags"], 1), "used_bags": round(a["used_bags"], 1),
                     "balance_bags": round(a["balance_lb"] / bag_lb, 1),
                     "balance_kg": round(a["balance_lb"] / KG_PER_LB)}
                    for a in per.values()]
    balance_rows.sort(key=lambda x: x["yarn_name"] or "")
    balance = {"bags": round(sum(r["balance_bags"] or 0 for r in mrows), 1),
               "kg": round(sum((r["balance_lb"] or 0) for r in mrows) / KG_PER_LB),
               "received_bags": round(sum(r["received_bags"] or 0 for r in mrows), 1),
               "used_bags": round(sum(r["used_bags"] or 0 for r in mrows), 1),
               "rows": balance_rows}
    counts = {"warp_count": m0[6], "weft_count": m0[7], "ends_per_inch": m0[3], "picks_per_inch": m0[4],
              "width_in": m0[5], "rate_per_meter": m0[8],
              "warp_closing_count": warp_cc, "weft_closing_count": weft_cc}
    counts_used = {"warp": warp_cc, "weft": weft_cc, "warp_nominal": m0[6], "weft_nominal": m0[7]}
    totals = {"yarn_in_kg": round(sum(r["kg"] for r in igp)), "yarn_in_bags": round(sum(r["bags"] for r in igp), 1),
              "fabric_out_m": round(sum(r["meters"] for r in ogp)), "contracts": len(cnos)}
    return {"contracts": [{"contract": m[0], "party": m[1], "quality": m[2]} for m in meta],
            "party": m0[1], "quality": m0[2], "igp": igp, "ogp": ogp, "counts": counts, "counts_used": counts_used,
            "totals": totals, "balance": balance}


def closing_sheet_report(con, cfg, party=None, contract=None, quality=None, as_of=None):
    """The closing sheet as a downloadable Report. The BALANCE is the mill's own method balance (contract_rows,
    the same engine as `report closing`): net yarn received less the party's yarn the mill can account for, using
    the mill's CLOSING counts, rejection rule, basis and WIP - so it ties out to the closing report. The sheet adds
    the date-wise IGP yarn and OGP fabric that the closing report does not show, and a live-formula workbook whose
    warp/weft lb-per-metre are built from the ends/picks/width/count/divisor cells, so a count edit recalculates."""
    from ..reports.xlsx import Fx, Inp
    as_of = as_of or date.today()
    s = closing_sheet(con, cfg, party, contract, quality)
    counts, tot = s["counts"], s["totals"]
    method = Method(cfg["closing"])

    # authoritative balance from the mill's method (same as the closing report), PER YARN plus the combined total.
    bl = s.get("balance", {})
    bal_rows = bl.get("rows", [])
    recv_bags = bl.get("received_bags", 0)
    used_bags = bl.get("used_bags", 0)
    bal_bags = bl.get("bags", 0)
    bal_kg = bl.get("kg", 0)

    # the CLOSING counts actually used (config [yarns]/[parties] override the nominal count) + lb/m, for the first
    # selected contract - shown so the owner sees the counts the balance was built on.
    warp_cc = weft_cc = warp_lbm = weft_lbm = None
    divisor = float(method.cfg.get("divisor", 840) or 840)
    wc_crimp = fc_crimp = 0.0
    c0 = next(iter(s["contracts"]), None)
    if c0:
        row = con.execute("""SELECT ends_per_inch, picks_per_inch, width_in, warp_yarn, warp_count, weft_yarn, weft_count
                             FROM contracts WHERE contract_no = ?""", (c0["contract"],)).fetchone()
        if row:
            epi, ppi, width, wy, wc, fy, fc = row
            st = method.settings(c0["contract"], None)
            divisor = float(st["divisor"])
            wc_crimp, fc_crimp = float(st["warp_crimp_pct"]), float(st["weft_crimp_pct"])
            warp_cc = method.closing_count("warp", wy, wc, st)
            weft_cc = method.closing_count("weft", fy, fc, st)
            warp_lbm = method.weight("warp", st, epi, width, warp_cc)
            weft_lbm = method.weight("weft", st, ppi, width, weft_cc)

    who = (contract and f"contract {contract}") or (party and quality and f"{party} · {quality}") or (party or "all contracts")
    title = f"Closing sheet - {who}"
    # table: the per-yarn balance first (as the closing report shows it), then the date-wise yarn-in and fabric-out.
    columns = ["Section", "Date / Yarn", "Doc / Count", "Bags", "Qty / Balance"]
    rows = [["Balance", f"{r['yarn_name']}", (f"count {r['count']:g}" if r.get('count') else ""),
             f"{r['balance_bags']:+,.1f}", f"{r['balance_kg']:+,} kg"] for r in bal_rows]
    rows += [["Yarn in", r["date"], r["doc_no"], r["bags"], f"{r['kg']:,} kg"] for r in s["igp"]]
    rows += [["Fabric out", r["date"], r["doc_no"], "", f"{r['meters']:,} m"] for r in s["ogp"]]
    summary = [
        f"{tot.get('contracts', 0)} contract(s): {who}.",
        f"Yarn in {tot.get('yarn_in_kg', 0):,} kg ({tot.get('yarn_in_bags', 0):,} bags); fabric out {tot.get('fabric_out_m', 0):,} m.",
        (f"Closing counts used: warp {warp_cc:g}, weft {weft_cc:g} (warp {warp_lbm:.4f} + weft {weft_lbm:.4f} lb/m, "
         f"{counts.get('ends_per_inch')}/{counts.get('picks_per_inch')} @ {counts.get('width_in')}\", divisor {divisor:g})."
         if warp_cc and weft_cc else "Closing counts not available (missing construction)."),
        (f"Closing balance (mill's method, = closing report) PER YARN above; combined {bal_bags:+,.1f} bags "
         f"({bal_kg:+,} kg) - {'the mill holds the party yarn' if bal_bags >= 0 else 'the party owes the mill'}. "
         f"Received {recv_bags:,.1f} bags, accounted {used_bags:,.1f} bags."),
    ]
    rep = Report(title, columns, rows, summary)
    # live-formula workbook. Summary is ONE row so every formula uses only same-row ({r}) references (robust to the
    # sheet's title/note offset): lb/m is BUILT from the count cells, so editing a count, the ends/picks/width or
    # the divisor recalculates the per-metre weights (standard weight formula:
    # threads x width x 1.0936 / divisor / count x (1 + crimp/100)) and the what-if yarn-needed and balance.
    # A B C D E F G H  = ends, picks, width, warp count, weft count, divisor, warp crimp%, weft crimp%
    # I warp lb/m  J weft lb/m  K fabric out  L yarn needed(what-if)  M yarn received  N what-if balance  O closing balance
    summ = {"name": "Summary", "title": title,
            "note": [who, "Edit a count, the ends/picks/width or the divisor and the lb/m, what-if yarn-needed and "
                     "what-if balance recalculate. The Closing balance (last column) is the mill's method balance "
                     "(= the closing report) and does not change with these what-if edits."],
            "headers": ["Ends/in", "Picks/in", "Width in", "Warp count", "Weft count", "Divisor", "Warp crimp %",
                        "Weft crimp %", "Warp lb/m", "Weft lb/m", "Fabric out m", "Yarn needed kg (what-if)",
                        "Yarn received kg", "What-if balance kg", "Closing balance kg (mill method)"],
            "money_cols": [11, 12, 13, 14],
            "rows": [[Inp(counts.get("ends_per_inch") or 0), Inp(counts.get("picks_per_inch") or 0),
                      Inp(counts.get("width_in") or 0), Inp(warp_cc or counts.get("warp_count") or 0),
                      Inp(weft_cc or counts.get("weft_count") or 0), Inp(divisor),
                      Inp(round(wc_crimp, 2)), Inp(round(fc_crimp, 2)),
                      Fx("=A{r}*C{r}*1.0936/F{r}/D{r}*(1+G{r}/100)"),
                      Fx("=B{r}*C{r}*1.0936/F{r}/E{r}*(1+H{r}/100)"),
                      Inp(tot.get("fabric_out_m", 0)), Fx("=K{r}*(I{r}+J{r})/2.2046"),
                      Inp(tot.get("yarn_in_kg", 0)), Fx("=M{r}-L{r}"), bal_kg]]}
    # per-yarn balance sheet (one row per yarn, as the closing report shows), with the combined total
    ybal = {"name": "Yarn balance", "title": "Closing balance per yarn (mill's method)",
            "headers": ["Yarn", "Closing count", "Received bags", "Accounted bags", "Balance bags", "Balance kg"],
            "money_cols": [5],
            "rows": [[r["yarn_name"], r.get("count"), r["received_bags"], r["used_bags"], r["balance_bags"], r["balance_kg"]]
                     for r in bal_rows] + [["TOTAL", "", round(recv_bags, 1), round(used_bags, 1), bal_bags, bal_kg]]}
    yin = {"name": "Yarn in (IGP)", "title": "Yarn received - date-wise", "headers": ["Date", "Doc", "Bags", "Kg"],
           "money_cols": [3], "rows": [[r["date"], r["doc_no"], r["bags"], Inp(r["kg"])] for r in s["igp"]]}
    fout = {"name": "Fabric out (OGP)", "title": "Fabric dispatched - date-wise", "headers": ["Date", "Doc", "Meters"],
            "money_cols": [2], "rows": [[r["date"], r["doc_no"], Inp(r["meters"])] for r in s["ogp"]]}
    rep.xlsx_sheets = [summ, ybal, yin, fout]
    return rep
