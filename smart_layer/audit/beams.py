"""Beam checks: looms about to run out of beam with no replacement ready, beams woven beyond their length,
empty beams not back from a sizer (see textile/beams.py)."""

from datetime import date

from ..textile.beams import beams_on_looms, settings


def run(a):
    if not a.q("SELECT 1 FROM beams LIMIT 1"):
        return
    t = settings(a.cfg)
    d1 = date.fromisoformat(a.window[1])
    for r in beams_on_looms(a.con, a.cfg, d1):
        if r["over"]:
            a.add("CONFIRMED", "Beams", f"beamover-{r['beam']}-{r['set_no']}",
                  f"Beam {r['beam']} on {r['loom_name']}: {r['woven']:,.0f} m woven from a {r['set_length']:,.0f} m set",
                  "more fabric than the beam holds - production booked to the wrong beam, or the beam changed without a take-off")
            continue
        if r["days"] is None or r["backup"] or r["days"] > float(t["warn_days"]):
            continue
        stage = "urgent" if r["days"] <= float(t["urgent_days"]) else "soon"      # pushed once per stage
        a.add("ACTION", "Beams", f"beamend-{r['beam']}-{r['set_no']}-{stage}",
              f"{r['loom_name']}: beam {r['beam']} finishes in about {r['days']:.0f} days - no sized beam waiting for it",
              f"contract {r['contract'] or '?'}, {r['to_come']:,.0f} m of fabric to come at {r['rate']:,.0f} m a day; "
              f"get the next set sized")
    limit = (d1.toordinal() - int(t["sizer_return_days"]))
    skip = {s.lower() for s in t["own_beam_sizers"]}
    out = {}
    for sizer, beam, mt, d in a.q("""SELECT party_name, beam_no, move_type, move_date FROM beam_movements
            WHERE move_type IN ('empty_to_sizer', 'empty_from_sizer', 'sized_received') AND move_date <= ? ORDER BY move_date""", a.window[1]):
        if (sizer or "").lower() in skip:
            continue
        if mt == "empty_to_sizer":
            out[(sizer, beam)] = d
        else:
            out.pop((sizer, beam), None)
    for (sizer, beam), d in out.items():
        if date.fromisoformat(d).toordinal() < limit:
            a.add("WATCH", "Beams", f"beamout-{sizer}-{beam}-{d}", f"Empty beam {beam} sent to {sizer} on {d} has not come back",
                  "ask the sizer, or check the return was entered under another party / beam number")
