"""Yarn checks in the daily audit: suspicious weft issues and sizing losses (see textile/pilferage.py)."""

from datetime import date

from ..textile.pilferage import analyse


def run(a):
    d0, d1 = (date.fromisoformat(x) for x in a.window)
    # Negative fabric stock is read from the ERP's OWN stock view (the fabric_stock entity, which includes the
    # opening stock FAB_OPN) - never from woven - dispatched, which ignores opening and makes go-live contracts
    # look negative. So only a contract the ERP itself shows negative is flagged.
    for cno, m in a.q("SELECT contract_no, SUM(meters) FROM fabric_stock GROUP BY contract_no"):
        if (m or 0) < -50:
            a.add("CONFIRMED", "Fabric", f"fabneg-{cno}-{round(m)}",
                  f"Contract {cno}: fabric stock is negative ({m:,.0f} m) in the ERP's own stock report",
                  "more dispatched than produced - a grade or quantity keyed wrong on a gate pass or on production")
    if not a.q("SELECT 1 FROM yarn_movements LIMIT 1"):
        return
    flags, weekly, sizing, t = analyse(a.con, a.cfg, d0, d1)
    for f in flags:
        a.add("ACTION" if len(f["reasons"]) >= 2 else "WATCH", "Yarn", f"yarn-{f['contract']}-{f['date']}",
              f"Contract {f['contract']} ({f['party'] or '?'}) {f['date']}: {f['kg']:,.0f} kg weft issued - check",
              "; ".join(f["reasons"]))
    for s in sizing:
        if s["loss_pct"] > float(t["sizing_loss_pct"]):
            a.add("WATCH", "Yarn", f"sizing-{s['sizer']}-{s['contract']}-{round(s['loss_pct'])}",
                  f"{s['sizer']}, contract {s['contract']}: sizing loss {s['loss_pct']:.1f}%",
                  f"yarn sent {s['sent']:,.0f} kg, sized beams back {s['back']:,.0f} kg")
