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
    d = as_of.isoformat()
    contracts = con.execute("""SELECT contract_no, party_code, COALESCE(c.party_name, p.party_name, c.party_code), quality,
                                      ends_per_inch, picks_per_inch, width_in, warp_yarn, warp_count, weft_yarn, weft_count,
                                      ordered_meters, COALESCE(yarn_owner, 'party')
                               FROM contracts c LEFT JOIN parties p USING (party_code)""").fetchall()
    received = defaultdict(float)
    names = {}
    for cno, code, name, mt, kg in con.execute("""
            SELECT contract_no, yarn_code, MAX(yarn_name), move_type, SUM(kg) FROM yarn_movements
            WHERE move_type IN ('party_receipt', 'party_return', 'opening') AND move_date <= ?
            GROUP BY contract_no, yarn_code, move_type""", (d,)):
        received[(cno, code)] += (kg or 0) * (-1 if mt == "party_return" else 1)
        names[code] = name or code
    disp = {cno: (o_all or 0, o_fresh or 0) for cno, o_all, o_fresh in con.execute("""
            SELECT contract_no, SUM(CASE direction WHEN 'out' THEN meters ELSE -meters END),
                   SUM(CASE direction WHEN 'out' THEN COALESCE(fresh_m, meters) ELSE -COALESCE(fresh_m, meters) END)
            FROM fabric_dispatches WHERE dispatch_date <= ? GROUP BY contract_no""", (d,))}
    woven = {cno: (m or 0, f or 0, last) for cno, m, f, last in con.execute("""
            SELECT contract_no, SUM(meters), SUM(COALESCE(first_quality_m, meters - COALESCE(rejected_m, 0))), MAX(prod_date)
            FROM loom_production WHERE prod_date <= ? GROUP BY contract_no""", (d,))}

    rows = []
    for (cno, pcode, party, quality, epi, ppi, width, wy, wc, fy, fc, ordered, owner) in contracts:
        if owner == "mill":
            continue
        fix = method.contract_overrides(cno)
        epi, ppi, width = fix.get("ends_per_inch", epi), fix.get("picks_per_inch", ppi), fix.get("width_in", width)
        wy, fy = fix.get("warp_yarn", wy), fix.get("weft_yarn", fy)
        s = method.settings(cno, party)
        all_m, fresh_m = disp.get(cno, (0, 0)) if s["basis"] == "dispatched" else woven.get(cno, (0, 0, None))[:2]
        meters = fresh_m if s["rejection_charged_to"] == "mill" else all_m
        allow = 1 + float(s["wastage_allowance_pct"]) / 100
        need = defaultdict(float)                   # yarn code -> lb consumed
        missing = []
        for side, threads, ycode, count in (("warp", epi, wy, wc), ("weft", ppi, fy, fc)):
            cc = method.closing_count(side, ycode, count, s)
            w = method.weight(side, s, threads, width, cc)
            if w is None:
                missing.append(side)
                continue
            need[ycode] += w * meters * allow
        last = woven.get(cno, (0, 0, None))[2]
        status = "running" if last and last >= (as_of - timedelta(days=30)).isoformat() else "finished" if last else "no production"
        for ycode in sorted(set(need) | {y for (c, y) in received if c == cno}, key=str):
            rec_lb = received.get((cno, ycode), 0) / KG_PER_LB
            used_lb = need.get(ycode, 0)
            bal_lb = rec_lb - used_lb
            rate = method.rate(ycode)
            bag = float(s["bag_lb"])
            rows.append(dict(contract=cno, party=party or pcode or "?", quality=quality, status=status, yarn=ycode,
                             yarn_name=names.get(ycode, ycode), meters=meters, received_bags=rec_lb / bag,
                             used_bags=used_lb / bag, balance_bags=bal_lb / bag, balance_lb=bal_lb,
                             value=bal_lb * rate if rate else None,
                             note=("construction missing for " + " and ".join(missing)) if missing else ""))
    return rows


def contract_closing(con, cfg, as_of=None, party=None):
    as_of = as_of or date.today()
    method = Method(cfg["closing"])
    rows = contract_rows(con, method, as_of)
    if party:
        rows = [r for r in rows if party.lower() in (r["party"] or "").lower()]
    cur = cfg["company"]["currency"]
    by_party = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        by_party[r["party"]][0] += r["balance_bags"]
        by_party[r["party"]][1] += r["value"] or 0
    summary = [f"Party yarn balances as at {as_of:%d-%b-%Y} (+ = we hold the party's yarn, - = party is short):"]
    summary += [f"  {p}: {b:+,.1f} bags" + (f" ({cur} {v:+,.0f})" if v else "") for p, (b, v) in sorted(by_party.items())]
    s = method.settings()
    summary.append(f"Method: {s['basis']} meters, rejection charged to {s['rejection_charged_to']}, divisor {s['divisor']:g}, "
                   f"bag {s['bag_lb']:g} lb, allowance {s['wastage_allowance_pct']:g}% (party / contract overrides apply).")
    return Report(f"Contract closing - {as_of:%d-%b-%Y}",
                  ["Contract", "Party", "Status", "Yarn", "Meters", "Received bags", "Used bags", "Balance bags",
                   f"Value {cur}", "Note"],
                  [[r["contract"], r["party"], r["status"], r["yarn_name"], round(r["meters"]), round(r["received_bags"], 1),
                    round(r["used_bags"], 1), round(r["balance_bags"], 1), r["value"], r["note"]] for r in rows], summary)
