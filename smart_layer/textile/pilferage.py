"""
Yarn pilferage check (generalised from Dost Textiles' 54_/58_ scripts), for every contract:

 1. Weft issued to looms vs weft needed for the fabric woven, over a rolling window
    (production is often keyed at roll check, so single days are noisy).
 2. Entry pattern: issues with no production nearby, very large issues, issues keyed
    long after their date or outside working hours.
 3. Sizing: yarn sent to each sizer vs the weight of sized beams that came back.

Weft per meter uses the mill's own closing method (config [closing]).
Thresholds are in config [pilferage].
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from ..reports import Report
from .method import KG_PER_LB, Method

DEFAULTS = {"lookback_days": 90, "window_days": 7, "excess_kg": 400, "excess_ratio": 1.3,
            "no_production_kg": 300, "no_production_days": 3, "large_issue_kg": 700,
            "late_entry_days": 7, "work_start_hour": 6, "work_end_hour": 21, "sizing_loss_pct": 6,
            "compare_by": "yarn",
            # yarns that are not weft (selvedge / border / kinara) - left out of the weft pilferage check entirely
            "non_weft_keywords": ["kinara", "leno", "selvedge", "selved", "border"]}


def weft_kg_per_m(method, row):
    cno, party, ppi, width, fy, fc = row
    fix = method.contract_overrides(cno)
    s = method.settings(cno, party)
    w = method.weight("weft", s, fix.get("picks_per_inch", ppi), fix.get("width_in", width),
                      method.closing_count("weft", fix.get("weft_yarn", fy), fc, s))
    return w * KG_PER_LB if w else None


def analyse(con, cfg, d0, d1):
    """Flags for issue days in [d0, d1]; looks back a window before d0 for the rolling comparison."""
    t = {**DEFAULTS, **cfg["pilferage"]}
    method = Method(cfg["closing"])
    win = int(t["window_days"])
    start = d0 - timedelta(days=win + int(t["no_production_days"]))
    end_p = d1 + timedelta(days=int(t["no_production_days"]))
    # Weft is compared per yarn account (party + weft yarn group) by default: mills often book weft issues to one
    # contract while the same yarn is woven on several contracts of that party. compare_by = "contract" is stricter.
    by_contract = str(t.get("compare_by", "yarn")) == "contract"
    cinfo = {}
    for row in con.execute("""SELECT contract_no, COALESCE(c.party_name, p.party_name), picks_per_inch, width_in, weft_yarn, weft_count
                              FROM contracts c LEFT JOIN parties p USING (party_code)"""):
        wg = method.group(method.contract_overrides(row[0]).get("weft_yarn", row[4]))
        cinfo[row[0]] = (weft_kg_per_m(method, row), row[1], wg, row[5] or (str(wg) if wg is not None else "?"))

    def account(cno, yarn=None):
        info = cinfo.get(cno, (None, None, None, None))
        return (cno,) if by_contract else (info[1], method.group(yarn) if yarn is not None else info[2])

    non_weft = [k.lower() for k in t.get("non_weft_keywords", DEFAULTS["non_weft_keywords"])]

    def is_non_weft(name):
        n = (name or "").lower()
        return any(k in n for k in non_weft)

    issues, needed, woven = defaultdict(float), defaultdict(float), defaultdict(float)
    lines = defaultdict(list)
    members = defaultdict(set)
    weft_differs = {}                               # contract -> {issued names, the contract's own weft, kg}
    for d, cno, yarn, yname, mt, kg, doc, ea, user in con.execute("""
            SELECT move_date, contract_no, yarn_code, yarn_name, move_type, kg, doc_no, entered_at, entered_by FROM yarn_movements
            WHERE move_type IN ('to_looms', 'from_looms') AND move_date BETWEEN ? AND ?""", (start.isoformat(), d1.isoformat())):
        nm = yname or yarn
        if is_non_weft(nm):
            continue                                # selvedge / kinara is not weft - keep it out of the weft check
        # compare each issue to ITS OWN contract's weft count; a different count is a booking mismatch, not pilferage
        cwg, cwn = cinfo.get(cno, (None, None, None, None))[2], cinfo.get(cno, (None, None, None, None))[3]
        ig = method.group(yarn)
        if cwg is not None and ig is not None and ig != cwg:
            if mt == "to_looms":
                w = weft_differs.setdefault(cno, {"issued": set(), "contract": cwn, "kg": 0.0})
                w["issued"].add(nm); w["kg"] += kg or 0
            continue                                # excluded from the kg comparison (would be apples-to-oranges)
        a = account(cno, yarn)
        members[a].add(cno)
        issues[(a, d)] += (kg or 0) * (1 if mt == "to_looms" else -1)
        if mt == "to_looms":
            lines[(a, d)].append((doc, kg or 0, ea, user, cno))
    unknown = set()                                 # accounts where some woven contract has no weft per meter
    for cno, d, m in con.execute("""SELECT contract_no, prod_date, SUM(meters) FROM loom_production
                                    WHERE prod_date BETWEEN ? AND ? GROUP BY contract_no, prod_date""",
                                 (start.isoformat(), end_p.isoformat())):
        a = account(cno)
        members[a].add(cno)
        woven[(a, d)] += m or 0
        k = cinfo.get(cno, (None,))[0]
        if k:
            needed[(a, d)] += (m or 0) * k
        else:
            unknown.add(a)

    def days(a, b):
        return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]

    def label(a):
        cs = sorted(members[a], key=lambda x: str(x))
        return ", ".join(str(c) for c in cs if c is not None) or "?"

    flags, weekly = [], []
    for a in sorted(members, key=lambda x: tuple(str(v) for v in x)):
        party = cinfo.get(a[0], (None, None))[1] if by_contract else a[0]
        for d in days(d0, d1):
            dd = date.fromisoformat(d)
            w = days(dd - timedelta(days=win - 1), dd)
            iss = sum(issues[(a, x)] for x in w)
            need = None if a in unknown else sum(needed[(a, x)] for x in w)
            if dd == d1 and (iss or need):
                weekly.append([label(a), party, round(iss), round(need) if need is not None else None,
                               round(iss - need) if need is not None else None])
            L = lines.get((a, d))
            if not L:
                continue
            tot = sum(x[1] for x in L)
            near = sum(woven[(a, x)] for x in days(dd - timedelta(days=int(t["no_production_days"])),
                                                     dd + timedelta(days=int(t["no_production_days"]))))
            why = []
            if tot >= float(t["no_production_kg"]) and near == 0:
                why.append(f"{tot:,.0f} kg issued but no production with this yarn within {t['no_production_days']} days")
            if need is not None and iss - need > float(t["excess_kg"]) and iss > float(t["excess_ratio"]) * max(need, 1):
                why.append(f"last {win} days: {iss:,.0f} kg issued vs {need:,.0f} kg needed for fabric woven (+{iss - need:,.0f} kg)")
            if tot >= float(t["large_issue_kg"]):
                why.append(f"very large day: {tot:,.0f} kg issued")
            for doc, kg, ea, user, cno in L:
                if not ea:
                    continue
                e = datetime.fromisoformat(ea)
                if (e.date() - dd).days >= int(t["late_entry_days"]):
                    why.append(f"issue {doc} keyed {(e.date() - dd).days} days after its date (by {user or '?'})")
                if e.hour >= int(t["work_end_hour"]) or e.hour < int(t["work_start_hour"]):
                    why.append(f"issue {doc} keyed at {e:%H:%M}, outside working hours (by {user or '?'})")
            if why:
                flags.append(dict(contract=label(a), party=party, date=d, kg=tot, reasons=list(dict.fromkeys(why))))

    # one "weft differs from contract" flag per contract (not per day), with the yarn NAMES, not codes.
    for cno, w in weft_differs.items():
        party = cinfo.get(cno, (None, None, None, None))[1]
        issued = ", ".join(sorted(w["issued"]))
        flags.append(dict(contract=str(cno), party=party, date=d1.isoformat(), kg=round(w["kg"]),
                          reasons=[f"weft {issued} issued, but contract {cno} calls for {w['contract']} - "
                                   f"check which count is right"]))

    # Sizing loss per sizer and contract over the contract's whole history (so beams are matched with the yarn
    # they were made from); yarn sent after the last sized beams came back is still at the sizer and left out.
    sizing = []
    moves = con.execute("""SELECT COALESCE(party_name, party_code), contract_no, move_type, move_date, kg FROM yarn_movements
                           WHERE move_type IN ('to_sizer', 'from_sizer', 'sized_beams_received') AND move_date <= ?""",
                        (d1.isoformat(),)).fetchall()
    last_back = {}
    for sizer, cno, mt, d, kg in moves:
        if mt == "sized_beams_received":
            last_back[(sizer, cno)] = max(d, last_back.get((sizer, cno), d))
    sent, back = defaultdict(float), defaultdict(float)
    for sizer, cno, mt, d, kg in moves:
        key = (sizer, cno)
        if mt == "sized_beams_received":
            back[key] += kg or 0
        elif key in last_back and d <= last_back[key]:
            sent[key] += (kg or 0) * (1 if mt == "to_sizer" else -1)
    for key, s in sent.items():
        b = back.get(key, 0)
        if s > 0 and b > 0:
            sizing.append(dict(sizer=key[0], contract=key[1], sent=s, back=b, loss_pct=100 * (s - b) / s))
    return flags, weekly, sizing, t


def episodes(flags, gap_days=3):
    """Consecutive flagged days of one contract merged into one episode (worst day's reasons kept)."""
    out = []
    for f in sorted(flags, key=lambda f: (str(f["contract"]), f["date"])):
        e = out[-1] if out else None
        if e and e["contract"] == f["contract"] and (date.fromisoformat(f["date"]) - date.fromisoformat(e["last"])).days <= gap_days:
            e["last"], e["kg"], e["days"] = f["date"], e["kg"] + f["kg"], e["days"] + 1
            if len(f["reasons"]) >= e["n"]:
                e["why"], e["n"] = "; ".join(f["reasons"]), len(f["reasons"])
        else:
            out.append(dict(contract=f["contract"], party=f["party"], first=f["date"], last=f["date"], kg=f["kg"], days=1,
                            why="; ".join(f["reasons"]), n=len(f["reasons"])))
    for e in out:
        if e["days"] > 1:
            e["why"] = f"{e['days']} days flagged; worst: {e['why']}"
    return sorted(out, key=lambda e: (-e["n"], -e["days"], e["first"]))


def pilferage(con, cfg, as_of=None):
    as_of = as_of or (date.today() - timedelta(days=1))
    look = int({**DEFAULTS, **cfg["pilferage"]}["lookback_days"])
    flags, weekly, sizing, t = analyse(con, cfg, as_of - timedelta(days=look), as_of)
    rows = [["Days" if e["first"] != e["last"] else "Day", e["first"] if e["first"] == e["last"] else f"{e['first']} to {e['last']}",
             e["contract"], e["party"], round(e["kg"]), e["why"]] for e in episodes(flags)]
    rows += [["Sizing", "", s["contract"], s["sizer"], round(s["sent"] - s["back"]),
              f"sent {s['sent']:,.0f} kg, sized beams back {s['back']:,.0f} kg: loss {s['loss_pct']:.1f}%"]
             for s in sorted(sizing, key=lambda s: -s["loss_pct"]) if s["loss_pct"] > float(t["sizing_loss_pct"])]
    over = [w for w in weekly if w[4] is not None and w[4] > float(t["excess_kg"])]
    summary = [f"Last {look} days to {as_of:%d-%b-%Y}: {len(rows) - sum(1 for r in rows if r[0] == 'Sizing')} episodes "
               f"({len(flags)} issue days) to check, "
               f"{sum(1 for s in sizing if s['loss_pct'] > float(t['sizing_loss_pct']))} sizer/contract with loss over "
               f"{t['sizing_loss_pct']}%."]
    summary += [f"  Contracts {w[0]} ({w[1]}): last {t['window_days']} days {w[2]:,} kg weft issued vs {w[3]:,} kg needed (+{w[4]:,} kg)"
                for w in over]
    return Report(f"Yarn pilferage check - {as_of:%d-%b-%Y}", ["Check", "Date", "Contract", "Party / sizer", "Kg", "Why"],
                  rows, summary)
