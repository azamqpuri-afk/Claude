"""Yarn checks in the daily audit: suspicious weft issues and sizing losses (see textile/pilferage.py)."""

from datetime import date, timedelta

from ..textile.pilferage import analyse


def run(a):
    d0, d1 = (date.fromisoformat(x) for x in a.window)
    # Negative fabric stock is read from the ERP's OWN stock view (the fabric_stock entity, which includes the
    # opening stock FAB_OPN) - never from woven - dispatched, which ignores opening and makes go-live contracts
    # look negative. So only a contract the ERP itself shows negative is flagged.
    # Item AF: a legacy migration contract (opening carried from the old ERP, no activity since) shows negative
    # forever and must not nag daily. Flag only contracts with real movement (production OR dispatch) in the last
    # `fabric_stale_days` (default 90); the rest are a one-time data-cleanup matter, not a daily alert.
    stale_days = int(a.cfg["audit"].get("fabric_stale_days", 90))
    cutoff = (d1 - timedelta(days=stale_days)).isoformat()
    active = {c for (c,) in a.q("SELECT DISTINCT contract_no FROM loom_production WHERE prod_date >= ?", cutoff)}
    active |= {c for (c,) in a.q("SELECT DISTINCT contract_no FROM fabric_dispatches WHERE dispatch_date >= ?", cutoff)}
    for cno, m in a.q("SELECT contract_no, SUM(meters) FROM fabric_stock GROUP BY contract_no"):
        if (m or 0) < -50 and cno in active:
            a.add("CONFIRMED", "Fabric", f"fabneg-{cno}-{round(m)}",
                  f"Contract {cno}: fabric stock is negative ({m:,.0f} m) in the ERP's own stock report",
                  "more dispatched than produced - a grade or quantity keyed wrong on a gate pass or on production")
    if not a.q("SELECT 1 FROM yarn_movements LIMIT 1"):
        return
    flags, weekly, sizing, t = analyse(a.con, a.cfg, d0, d1)
    for f in flags:
        fid = f.get("key") or f"yarn-{f['contract']}-{f['date']}"      # stable id so a mismatch is not re-pushed daily
        when = (f["first"] if f["first"] == f["last"] else f"{f['first']} to {f['last']}") if f.get("first") else f["date"]
        if f.get("kind") == "weft_differs":
            headline = f"Contract {f['contract']} ({f['party'] or '?'}) {when}: weft count does not match the contract"
        else:
            headline = f"Contract {f['contract']} ({f['party'] or '?'}) {when}: {f['kg']:,.0f} kg weft issued - check"
        a.add("ACTION" if len(f["reasons"]) >= 2 else "WATCH", "Yarn", fid, headline, "; ".join(f["reasons"]))
    for s in sizing:
        # per CONTRACT, and only for contracts that actually moved in the audit window (item AF: a closed contract
        # with no recent movement is not a daily alarm, and loss netted across sizers is not a false per-party gap)
        if s["moved_in_window"] and s["loss_pct"] > float(t["sizing_loss_pct"]):
            detail = (f"yarn sent {s['sent']:,.0f} kg, sized beams back {s['back']:,.0f} kg (netted across sizers). "
                      + "Per sizer: " + "; ".join(f"{z['sizer']} {z['sent'] - z['back']:+,.0f} kg" for z in s["by_sizer"]))
            a.add("WATCH", "Yarn", f"sizing-{s['contract']}-{round(s['loss_pct'])}",
                  f"Contract {s['contract']}: sizing loss {s['loss_pct']:.1f}%", detail)
