"""
Gully / rewind-yarn tracking.

Background (generic to any mill, driven entirely by the mapping and [gully] config):
  A sized beam comes back from the sizer with some yarn still on the returned baby cones - the "gully". The ERP
  records that gully weight on the sized-beam receipt (it is the per-set `gullay_kg` the sizer ledger already uses),
  but it does NOT record what happens next: the mill rewinds the gully and re-uses it. For some yarns the rewound
  gully goes back to SIZING as a small lot in the next set; for others it is used as WEFT on the looms. The ERP
  never separates that rewound yarn from fresh yarn, so the gully quietly goes untraced.

This module keeps a running *book* balance per yarn (and per contract) from the ERP movements plus a few owner-
entered, dated figures that live only in Smart Layer (never written back to the ERP):
    book = opening + gully returned - rewound re-used - waste
    difference = book - a later physical count
The reuse route is per-yarn ([gully].reuse_to): "sizing" subtracts the rewound lots re-sent to sizing; "weft"
subtracts the rewound cones the owner marks as issued to the looms. Both routes share the same opening entry.

Nothing here is mill-specific: the yarn labels, the move_type values and the route all come from config / the
mapping. This module does NOT touch the contract-closing code.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from .. import manual
from ..reports import Report


# ---- small helpers ---------------------------------------------------------------------------

def _norm(s):
    return " ".join(str(s or "").strip().upper().split())


def route_for(gcfg, yarn_label):
    """The reuse route for a yarn: "sizing" or "weft". reuse_to maps a yarn label -> route; "default" is the
    fallback. Matched case-insensitively, exact first, then as a substring either way, so "30/1 PC" in config
    matches a yarn named "30/1 PC CARDED"."""
    reuse = {_norm(k): str(v).strip().lower() for k, v in (gcfg.get("reuse_to") or {}).items() if k != "default"}
    default = str((gcfg.get("reuse_to") or {}).get("default", "weft")).strip().lower()
    y = _norm(yarn_label)
    if y in reuse:
        return reuse[y]
    for k, v in reuse.items():
        if k and (k in y or y in k):
            return v
    return default or "weft"


def cone_kg(gcfg, yarn_label, cone_pbag=None):
    """Weight of one cone (kg), for an opening the owner enters as bags + cones. cone kg = bag_kg / cones-per-bag;
    cones-per-bag comes from [gully].cone_pbag per yarn, else the value passed from the yarn stock."""
    bag_kg = float(gcfg.get("bag_kg", 45.359237) or 45.359237)
    per = (gcfg.get("cone_pbag") or {})
    cpb = None
    yk = _norm(yarn_label)
    for k, v in per.items():
        if _norm(k) == yk or _norm(k) in yk or yk in _norm(k):
            cpb = v
            break
    if cpb is None:
        cpb = cone_pbag
    try:
        cpb = float(cpb)
    except (TypeError, ValueError):
        cpb = 0
    return (bag_kg / cpb) if cpb else 0.0


def _per_yarn(mapping, yarn_label, default):
    """Look up a per-yarn config value (exact, then substring either way), else the default."""
    yk = _norm(yarn_label)
    for k, v in (mapping or {}).items():
        if _norm(k) == yk or _norm(k) in yk or yk in _norm(k):
            return v
    return default


def cone_weight(gcfg, yarn_label):
    """Weight (kg) of one empty gully cone/tube for this yarn - subtracted from the gully weight to leave the net
    rewind yarn (the gully weight the sizer books includes the tubes)."""
    try:
        return float(_per_yarn(gcfg.get("cone_weight_by_yarn"), yarn_label, gcfg.get("cone_weight_kg", 0.060)) or 0)
    except (TypeError, ValueError):
        return 0.060


def _contract_yarn(con, gcfg):
    """{contract_no: yarn_label} - the dominant yarn on a contract's sizing / receipt movements, so a set's gully
    (which carries only a contract) can be attributed to a yarn. Falls back to the contract's warp count."""
    types = set(gcfg.get("to_sizing_types") or []) | set(gcfg.get("party_receipt_types") or [])
    out, best = {}, {}
    if types:
        q = "SELECT contract_no, yarn_name, SUM(COALESCE(kg,0)) FROM yarn_movements WHERE move_type IN (%s) " \
            "AND contract_no IS NOT NULL GROUP BY contract_no, yarn_name" % ",".join("?" * len(types))
        for cno, yname, kg in con.execute(q, tuple(types)):
            c = str(cno)
            if yname and (c not in best or (kg or 0) > best[c]):
                best[c], out[c] = (kg or 0), yname
    # warp-count fallback for contracts with no sizing/receipt line
    try:
        for cno, warp in con.execute("SELECT contract_no, COALESCE(warp_yarn, warp_count) FROM contracts"):
            out.setdefault(str(cno), warp)
    except Exception:
        pass
    return out


# ---- per-set: slip gully vs the gully weighed at the gate ------------------------------------

def gully_sets(con, cfg, as_of=None):
    """Per sized set: the slip gully (ERP `gullay_kg`) vs the owner's 'gully weighed at gate', and the difference.
    The owner suspects the sizer's slip gully is overstated; this shows it set by set and in total."""
    as_of = as_of or date.today()
    gcfg = cfg["gully"]
    cmap = _contract_yarn(con, gcfg)
    manset = manual.latest(con, "gully_set")         # {set_no: {"gate_kg": kg, "cones": gate cross-check count, ...}}
    rows, t_slip, t_gate, t_net = [], 0.0, 0.0, 0.0
    for sno, sdate, cno, slip, bags, cpb, loose in con.execute(
            "SELECT set_no, set_date, contract_no, COALESCE(gullay_kg,0), COALESCE(bags,0), "
            "COALESCE(cones_per_bag,0), COALESCE(loose_cones,0) FROM sized_sets "
            "WHERE set_date <= ? ORDER BY set_date, set_no", (as_of.isoformat(),)):
        slip = float(slip or 0)
        yarn = cmap.get(str(cno))
        m = manset.get(str(sno), {}) or {}
        g = m.get("gate_kg")
        g = (float(g) if g is not None else None)
        # the gully-cone (tube) count comes from the set record itself: bags x cones/bag + loose cones
        cones = float(bags or 0) * float(cpb or 0) + float(loose or 0)
        if not cones:                                                       # old sets with no cone detail: per-yarn fallback
            fb = _per_yarn(gcfg.get("default_gully_cones"), yarn, None)
            cones = (float(fb) if fb not in (None, "") else 0.0)
        gate_cones = m.get("cones")                                         # the gate app's count, kept only as a cross-check
        gate_cones = (float(gate_cones) if gate_cones not in (None, "") else None)
        cw = cone_weight(gcfg, yarn)
        tube = cones * cw
        net = max(0.0, (g if g is not None else slip) - tube)              # net rewind yarn, off the gate weight if weighed
        rows.append({"set_no": sno, "set_date": sdate, "contract": cno, "yarn": yarn,
                     "slip_kg": round(slip, 1), "gate_kg": (round(g, 1) if g is not None else None),
                     "cones": round(cones), "gate_cones": (round(gate_cones) if gate_cones is not None else None),
                     "cones_diff": (round(cones - gate_cones) if gate_cones is not None else None),
                     "tube_kg": round(tube, 1), "net_kg": round(net, 1),
                     "difference": (round(slip - g, 1) if g is not None else None)})
        t_slip += slip
        t_net += net
        if g is not None:
            t_gate += g
    weighed = [r for r in rows if r["gate_kg"] is not None]
    return {"date": as_of.isoformat(), "rows": rows,
            "total_slip_kg": round(t_slip), "total_gate_kg": round(t_gate), "total_net_kg": round(t_net),
            "total_difference_kg": (round(sum(r["difference"] for r in weighed)) if weighed else None),
            "weighed_sets": len(weighed), "sets": len(rows)}


def yarn_weight_flags(con, cfg, as_of=None):
    """Data-quality check on the yarn ledger: receipts/movements whose weight is clearly wrong, because they throw
    the gully (and every yarn) balance off. Two kinds: (a) bags entered but the weight is 0 / missing; (b) the
    weight per bag is far from one bag (kg / (bags x bag_kg) outside the band), e.g. a kg typed in the bags box."""
    as_of = as_of or date.today()
    gcfg = cfg["gully"]
    bag_kg = float(gcfg.get("bag_kg", 45.359237) or 45.359237)
    lo = float(gcfg.get("weight_ratio_low", 0.5) or 0.5)
    hi = float(gcfg.get("weight_ratio_high", 2.0) or 2.0)
    rows = []
    for md, doc, mt, yname, cno, bags, kg in con.execute(
            "SELECT move_date, doc_no, move_type, yarn_name, contract_no, COALESCE(bags,0), kg "
            "FROM yarn_movements WHERE move_date <= ? AND COALESCE(bags,0) > 0", (as_of.isoformat(),)):
        bags = float(bags or 0)
        k = (float(kg) if kg is not None else None)
        if k is None or k == 0:
            rows.append({"date": md, "doc_no": doc, "move_type": mt, "yarn": yname, "contract": cno,
                         "bags": round(bags), "kg": (round(k) if k is not None else None),
                         "issue": "weight missing", "ratio": None})
            continue
        ratio = k / (bags * bag_kg) if bags and bag_kg else None
        if ratio is not None and (ratio < lo or ratio > hi):
            rows.append({"date": md, "doc_no": doc, "move_type": mt, "yarn": yname, "contract": cno,
                         "bags": round(bags), "kg": round(k),
                         "issue": ("weight too low" if ratio < lo else "weight too high"), "ratio": round(ratio, 2)})
    rows.sort(key=lambda r: str(r["date"]), reverse=True)
    return {"date": as_of.isoformat(), "rows": rows, "count": len(rows)}


# ---- movements and the rewound-vs-fresh decision ---------------------------------------------

def _movements(con, types, upto):
    if not types:
        return []
    q = "SELECT move_date, doc_no, yarn_name, contract_no, COALESCE(kg,0), COALESCE(bags,0) FROM yarn_movements " \
        "WHERE move_type IN (%s) AND move_date <= ? ORDER BY move_date, doc_no" % ",".join("?" * len(types))
    out = []
    for md, doc, yname, cno, kg, bags in con.execute(q, tuple(types) + (upto.isoformat(),)):
        out.append({"date": md, "doc_no": doc, "yarn": yname, "contract": cno, "kg": float(kg or 0), "bags": float(bags or 0)})
    return out


def _auto_rewound(line, receipts, match_days, tol_pct):
    """A to-sizing line is FRESH (not rewound) when it matches a fresh party receipt of the same yarn within the
    window and weight tolerance - i.e. the mill just forwarded fresh party yarn to the sizer. Otherwise it is
    rewound gully going back to sizing."""
    try:
        d = date.fromisoformat(str(line["date"])[:10])
    except ValueError:
        return True
    y = _norm(line["yarn"])
    kg = line["kg"]
    lo, hi = kg * (1 - tol_pct / 100.0), kg * (1 + tol_pct / 100.0)
    for r in receipts:
        if _norm(r["yarn"]) != y:
            continue
        try:
            rd = date.fromisoformat(str(r["date"])[:10])
        except ValueError:
            continue
        if abs((rd - d).days) <= match_days and lo <= r["kg"] <= hi:
            return False
    return True


def rewind_lines(con, cfg, as_of=None):
    """The movement lines the owner can tick: to-sizing lines (for sizing-route yarns, auto-flagged rewound vs
    fresh) and weft-issue lines (for weft-route yarns, owner-marked). Each carries the auto flag and the owner's
    override (manual scope 'gully_line', field 'rewound', 1/0), with the override winning."""
    as_of = as_of or date.today()
    gcfg = cfg["gully"]
    md, tol = int(gcfg.get("match_days", 3) or 0), float(gcfg.get("match_tolerance_pct", 1.0) or 0)
    receipts = _movements(con, gcfg.get("party_receipt_types") or [], as_of)
    override = manual.latest(con, "gully_line")      # {doc_no: {"rewound": 1/0}}
    out = []
    for line in _movements(con, gcfg.get("to_sizing_types") or [], as_of):
        if route_for(gcfg, line["yarn"]) != "sizing":
            continue
        auto = _auto_rewound(line, receipts, md, tol)
        ov = (override.get(str(line["doc_no"]), {}) or {}).get("rewound")
        rewound = (bool(ov) if ov is not None else auto)
        out.append({**line, "kind": "to_sizing", "auto_rewound": auto, "owner_set": (None if ov is None else bool(ov)),
                    "rewound": rewound})
    for line in _movements(con, gcfg.get("weft_issue_types") or [], as_of):
        if route_for(gcfg, line["yarn"]) != "weft":
            continue
        ov = (override.get(str(line["doc_no"]), {}) or {}).get("rewound")
        rewound = (bool(ov) if ov is not None else False)   # weft lines default fresh until the owner marks them
        out.append({**line, "kind": "weft", "auto_rewound": False, "owner_set": (None if ov is None else bool(ov)),
                    "rewound": rewound})
    return out


# ---- the running balance per yarn ------------------------------------------------------------

def rewind_balance(con, cfg, as_of=None):
    """Per yarn: opening + gully returned - rewound re-used (by route) - waste = book balance, vs a physical count.
    Returns rows (biggest untraced first) plus mill totals."""
    as_of = as_of or date.today()
    gcfg = cfg["gully"]
    waste_pct = float(gcfg.get("waste_pct", 0) or 0)
    cmap = _contract_yarn(con, gcfg)

    # gully returned per yarn - the NET rewind yarn (gully weight less the empty cones/tubes), attributed via the
    # contract, taking the gate weight where the owner has weighed it. Reuses the per-set calculation.
    gully, sets = {}, gully_sets(con, cfg, as_of)
    for r in sets["rows"]:
        y = _norm(r["yarn"])
        if y:
            gully[y] = gully.get(y, 0.0) + r["net_kg"]

    # rewound re-used per yarn, split by route from the ticked lines
    reused = {}
    labels = {}
    for line in rewind_lines(con, cfg, as_of):
        y = _norm(line["yarn"])
        labels.setdefault(y, line["yarn"])
        if line["rewound"]:
            reused[y] = reused.get(y, 0.0) + line["kg"]

    openings = manual.latest(con, "gully_yarn")      # {yarn_label(normalised key): {opening_kg, waste_kg, physical_kg, physical_at}}
    # owner keys openings by the yarn label; normalise the key so it lines up with movement yarns
    open_by = {}
    for k, v in openings.items():
        open_by[_norm(k)] = v

    yarns = set(gully) | set(reused) | set(open_by)
    for y in yarns:
        labels.setdefault(y, y)

    rows, tot = [], {"gully": 0.0, "reused": 0.0, "waste": 0.0, "book": 0.0, "untraced": 0.0}
    for y in yarns:
        label = labels.get(y, y)
        route = route_for(gcfg, label)
        o = open_by.get(y, {})
        opening = float(o.get("opening_kg") or 0)
        g = gully.get(y, 0.0)
        used = reused.get(y, 0.0)
        waste = float(o["waste_kg"]) if o.get("waste_kg") is not None else (g * waste_pct / 100.0)
        book = opening + g - used - waste
        phys = o.get("physical_kg")
        phys = (float(phys) if phys is not None else None)
        pat = o.get("physical_at_iso")
        try:
            pat = date.fromordinal(int(pat)).isoformat() if pat is not None else None
        except (TypeError, ValueError, OverflowError):
            pat = None
        rows.append({"yarn": label, "route": route, "opening_kg": round(opening),
                     "gully_returned_kg": round(g), "rewound_reused_kg": round(used), "waste_kg": round(waste),
                     "book_balance_kg": round(book), "physical_kg": (round(phys) if phys is not None else None),
                     "difference_kg": (round(book - phys) if phys is not None else None),
                     "physical_at": pat})
        tot["gully"] += g
        tot["reused"] += used
        tot["waste"] += waste
        tot["book"] += book
    rows.sort(key=lambda r: -r["book_balance_kg"])
    tot = {k: round(v) for k, v in tot.items()}
    tot["untraced_bags"] = None
    return {"date": as_of.isoformat(), "rows": rows, "totals": tot, "waste_pct": waste_pct}


# ---- registry report (PDF / Excel / CLI) -----------------------------------------------------

def gully_report(con, cfg, day=None):
    bal = rewind_balance(con, cfg, day)
    cols = ["Yarn", "Route", "Opening kg", "Gully net kg", "Rewound re-used kg", "Waste kg",
            "Book balance kg", "Physical kg", "Difference kg"]
    rows = [[r["yarn"], r["route"], r["opening_kg"], r["gully_returned_kg"], r["rewound_reused_kg"],
             r["waste_kg"], r["book_balance_kg"], r["physical_kg"], r["difference_kg"]] for r in bal["rows"]]
    t = bal["totals"]
    summary = [f"As of {bal['date']}. Book balance = opening + gully returned (net of tubes) − rewound re-used − waste.",
               f"Gully net {t['gully']:,} kg · rewound re-used {t['reused']:,} kg · "
               f"book balance {t['book']:,} kg across {len(rows)} yarn(s)."]
    return Report("Gully / rewind balance", cols, rows, summary)
