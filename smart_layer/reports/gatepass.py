"""
Gate-pass reports from the canonical gate_passes register (works for any mill). The register is one row per item
line; gate-pass numbers repeat across types and over time, so slips are counted by gatepass_id, never the bare number.
"""

from datetime import timedelta

from . import Report


def _qty_text(q, u, bags, cones, thaan):
    """Show the quantity in the line's own unit, as the ERP stores it: yarn in bags (+loose cones),
    fabric in meters (+thaan count), store in its own uom. We read these; we do not convert."""
    if bags:
        s = f"{round(bags):g} bags"
        return s + (f" + {round(cones):g} cones" if cones else "")
    if thaan:
        return f"{round(q or 0):g} m ({round(thaan):g} thaan)"
    if q is not None:
        return (f"{round(q, 2):g} " + (u or "")).strip()
    return ""


def register(con, cfg, as_of=None):
    """Recent gate passes, newest first (one row per item line). Quantity is shown in each line's own unit."""
    cols = ["Date", "Type", "GP no", "In/Out", "Party", "Item / beam", "Quantity", "Vehicle"]
    rows = con.execute("""SELECT gp_date, type, gp_no, direction, party_name,
                                 COALESCE(item, beam_no), qty, uom, bags, cones, thaan, vehicle
                          FROM gate_passes ORDER BY gp_date DESC, gp_no DESC LIMIT 500""").fetchall()
    out = [[d, t, no, dirn, p, it, _qty_text(q, u, bg, cn, th), v]
           for d, t, no, dirn, p, it, q, u, bg, cn, th, v in rows]
    slips = con.execute("SELECT COUNT(DISTINCT gatepass_id) FROM gate_passes").fetchone()[0]
    return Report("Gate register (latest 500 lines)", cols, out,
                  [f"{slips:,} gate passes in all; showing the most recent lines."] if slips
                  else ["No gate passes have been synced yet."])


def summary(con, cfg, as_of=None):
    """Gate passes (slips) and total quantity by type over the last 30 days (or up to as_of)."""
    cols = ["Type", "In/Out", "Gate passes", "Lines", "Total qty"]
    end = (as_of.isoformat() if as_of else None)
    start = ((as_of - timedelta(days=30)).isoformat() if as_of else None)
    where, params = "", []
    if start and end:
        where = "WHERE gp_date BETWEEN ? AND ?"
        params = [start, end]
    rows = con.execute(f"""SELECT type, direction, COUNT(DISTINCT gatepass_id), COUNT(*), SUM(qty)
                           FROM gate_passes {where} GROUP BY type, direction ORDER BY 1, 2""", params).fetchall()
    out = [[t, d, slips, lines, round(q or 0)] for t, d, slips, lines, q in rows]
    title = "Gate passes by type" + (f" - 30 days to {as_of:%d-%b-%Y}" if as_of else "")
    return Report(title, cols, out, [] if out else ["No gate passes in this period."])
