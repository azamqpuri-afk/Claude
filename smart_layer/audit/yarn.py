"""Yarn checks in the daily audit: suspicious weft issues and sizing losses (see textile/pilferage.py)."""

from datetime import date

from ..textile.pilferage import analyse


def run(a):
    d0, d1 = (date.fromisoformat(x) for x in a.window)
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
