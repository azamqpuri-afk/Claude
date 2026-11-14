"""Beam checks: looms about to run out of beam with no replacement ready, beams woven beyond their length,
empty beams not back from a sizer (see textile/beams.py)."""

from datetime import date

from ..textile.beams import beams_on_looms, is_own_sizer, settings


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
    own = t["own_beam_sizers"]
    # don't resurface ancient unmatched beams forever: a beam sent long ago that never came back by its own number
    # is almost always one the sizer returned under a different number (they swap physical beams). Only alert on
    # beams sent recently enough to still act on (within 4x the return window).
    floor = (d1.toordinal() - 4 * int(t["sizer_return_days"]))
    # Sizers swap physical beams, so matching by beam NUMBER alone raises false alerts (a beam comes back under a
    # different number). Judge by QUANTITY per sizer first: empties sent vs empties + sized beams returned. If the
    # count is square (or they owe us nothing), it is just number-swapping - INFO, not a WATCH. Only when more went
    # out than came back do we alert, and only that many, the oldest still-open-by-number beams.
    per = {}
    for sizer, beam, mt, d in a.q("""SELECT party_name, beam_no, move_type, move_date FROM beam_movements
            WHERE move_type IN ('empty_to_sizer', 'empty_from_sizer', 'sized_received') AND move_date <= ? ORDER BY move_date""", a.window[1]):
        if is_own_sizer(sizer, own):
            continue
        p = per.setdefault(sizer, {"sent": 0, "returned": 0, "open": {}})
        if mt == "empty_to_sizer":
            p["sent"] += 1
            p["open"].setdefault(beam, []).append(d)
        else:
            p["returned"] += 1
            if p["open"].get(beam):                        # a return that matches an outstanding number
                p["open"][beam].pop()
    for sizer, p in per.items():
        net = p["sent"] - p["returned"]                    # physical beams still out, by count
        unmatched = sorted((d, beam) for beam, ds in p["open"].items() for d in ds)   # oldest open-by-number first
        if net <= 0:
            if unmatched:
                a.add("INFO", "Beams", f"beamqty-{sizer}", f"{p['sent']} empty beams sent to {sizer} are all back by count",
                      "some were returned under a different beam number - nothing outstanding")
            continue
        alerted = 0
        for d, beam in unmatched:                          # alert only `net` beams, the oldest still in the window
            if alerted >= net:
                break
            if floor <= date.fromisoformat(d).toordinal() < limit:
                a.add("WATCH", "Beams", f"beamout-{sizer}-{beam}-{d}", f"Empty beam {beam} sent to {sizer} on {d} has not come back",
                      f"{net} of {p['sent']} beams sent to {sizer} are still out by count - "
                      f"ask the sizer, or check the return was entered under another beam number")
                alerted += 1
