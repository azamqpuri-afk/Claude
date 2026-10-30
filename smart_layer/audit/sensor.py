"""
Sensor duplicate-timings check (generalised from Dost Textiles' 77_sensor_twin_check.py).

A stopped loom's sensor fitted onto a running loom makes the scoreboard count ONE loom twice: both loom numbers
then stop and restart on the same second, while the stopped loom produces nothing. This rule finds that, from the
raw stop log (the loom_stops entity - only mills whose monitor exports per-stop timings have it).

  - a "twin stop" = two looms stop within tol_seconds AND restart within tol_seconds of each other
  - stops that hit more than max_simultaneous looms at once (power cuts, the monitor's polling bursts) are left out
  - twin stops of one pair no more than gap_minutes apart are one EPISODE (they move a sensor for a while, then
    put it back); an episode is CONFIRMED when it has min_twins+ twin stops that are min_share+ of the smaller
    loom's stops in that episode

Positions are OPTIONAL. With no layout configured every pair is checked on the timing rules alone. With a floor
layout ([sensor.sheds] or [sensor.layout]) the pair is graded by nearness - neighbours / near / far - and only
neighbouring or near pairs can be confirmed (a sensor can only be swapped between looms standing together).

Confirmed episodes are ACTION (pushed once each, live, through the normal urgent-alert path). Weaker patterns are
WATCH (shown on the dashboard / in the audit, never pushed). A separate ACTION fires when the monitor's newest
stop is more than stale_hours old, so the check never goes blind silently.

This rule does NOT use the audit's date window: it always looks at the most recent stops in the store, so it works
the same from the daily audit and from the 5-minute live run.
"""

import re
from datetime import datetime, timedelta

DEFAULTS = {"tol_seconds": 2, "gap_minutes": 20, "min_twins": 5, "min_share": 0.60,
            "max_simultaneous": 3, "open_stop_seconds": 5, "stale_hours": 2, "sheds": {}, "layout": {}}


def settings(cfg):
    return {**DEFAULTS, **(cfg["sensor"] or {})}


def _loom_num(code):
    """The loom's number, for matching to a floor layout ('7' or 'LOOM # 7' or 'L07' -> 7). None if there is none."""
    digits = re.findall(r"\d+", str(code or ""))
    return int(digits[-1]) if digits else None


def build_positions(t):
    """loom number -> (shed, column, row) from config. Two forms, both optional:
      [sensor.sheds.<shed>]  columns = [[looms in column 0 top->bottom], [column 1], ...]
      [sensor.layout]        "<loom>" = [shed, column, row]
    Returns {} when neither is set (then every pair is checked on timing alone)."""
    pos = {}
    for shed, spec in (t.get("sheds") or {}).items():
        cols = spec.get("columns", spec) if isinstance(spec, dict) else spec
        for ci, col in enumerate(cols or []):
            for ri, loom in enumerate(col or []):
                n = _loom_num(loom)
                if n is not None:
                    pos[n] = (str(shed), ci, ri)
    for loom, p in (t.get("layout") or {}).items():
        n = _loom_num(loom)
        if n is not None and isinstance(p, (list, tuple)) and len(p) >= 3:
            pos[n] = (str(p[0]), int(p[1]), int(p[2]))
    return pos


def nearness(pos, a, b):
    """'neighbours' (same shed, side by side or one row apart - a sensor can be swapped in a minute), 'near' (same
    shed, two columns/rows apart) or 'far' (anything else, or unknown). With no layout everything is 'unknown'."""
    if not pos:
        return "unknown"
    pa, pb = pos.get(_loom_num(a)), pos.get(_loom_num(b))
    if not pa or not pb or pa[0] != pb[0]:
        return "far"
    d = max(abs(pa[1] - pb[1]), abs(pa[2] - pb[2]))
    return "neighbours" if d <= 1 else "near" if d <= 2 else "far"


def _parse(ts):
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(str(ts)[:19])
    except ValueError:
        return None


def episodes(rows, t):
    """Group the stop log into twin-stop episodes. rows = [(loom_code, stop_time, restart_time)] as stored (text).
    Returns a list of dicts, one per episode, with twins / share / nearness / confirmed already worked out."""
    tol = float(t["tol_seconds"])
    gap = float(t["gap_minutes"]) * 60
    pos = build_positions(t)
    ev = []
    for loom, stop, restart in rows:
        s, r = _parse(stop), _parse(restart)
        if loom is not None and s and r:
            ev.append((str(loom), s, r))
    ev.sort(key=lambda e: e[1])
    if not ev:
        return []
    # OPEN (still-stopped) stops can all carry the latest poll second as their "restart" on some monitors, which
    # would look like a twin. Drop that block ONLY when 3+ looms share the newest restart second (the tell-tale of
    # a rewritten poll time); when just 1-2 looms sit at the newest second they are genuine just-restarted stops -
    # keep them (and the next sync re-reads them anyway). Monitors that instead leave open stops NULL never hit this.
    poll = max(r for _, _, r in ev)
    open_s = float(t["open_stop_seconds"])
    if len({e[0] for e in ev if (poll - e[2]).total_seconds() <= open_s}) >= 3:
        ev = [e for e in ev if (poll - e[2]).total_seconds() > open_s]
    if not ev:
        return []
    times = {}                                           # loom -> sorted stop times (all stops, for the share)
    for loom, s, _ in ev:
        times.setdefault(loom, []).append(s)
    twins = {}                                           # (a, b) -> [stop times of a in the twin]
    n, i = len(ev), 0
    lo, hi = 2, int(t["max_simultaneous"])
    while i < n:
        j = i
        while j + 1 < n and (ev[j + 1][1] - ev[i][1]).total_seconds() <= tol:
            j += 1
        grp = ev[i:j + 1]
        looms = {g[0] for g in grp}
        if lo <= len(looms) <= hi:                       # 2..max looms together; more = a shed-wide event, skip
            for x in range(len(grp)):
                for y in range(x + 1, len(grp)):
                    a, b = grp[x], grp[y]
                    if a[0] != b[0] and abs((a[2] - b[2]).total_seconds()) <= tol:
                        twins.setdefault(tuple(sorted((a[0], b[0]))), []).append(a[1])
        i = j + 1 if j > i else i + 1
    out = []
    for (a, b), ts in twins.items():
        ts = sorted(set(ts))
        groups, cur = [], [ts[0]]
        for x in ts[1:]:
            if (x - cur[-1]).total_seconds() <= gap:
                cur.append(x)
            else:
                groups.append(cur); cur = [x]
        groups.append(cur)
        near = nearness(pos, a, b)
        for e in groups:
            span0, span1 = e[0] - timedelta(seconds=tol), e[-1] + timedelta(seconds=tol)
            na = sum(1 for x in times[a] if span0 <= x <= span1)
            nb = sum(1 for x in times[b] if span0 <= x <= span1)
            share = len(e) / max(1, min(na, nb))
            confirmed = (len(e) >= int(t["min_twins"]) and share >= float(t["min_share"])
                         and near in ("neighbours", "near", "unknown"))
            out.append({"a": a, "b": b, "twins": len(e), "share": share, "near": near,
                        "first": e[0], "last": e[-1],
                        "minutes": round((e[-1] - e[0]).total_seconds() / 60), "confirmed": confirmed})
    out.sort(key=lambda x: (not x["confirmed"], -x["twins"], -x["share"]))
    return out


def run(a):
    if not a.q("SELECT 1 FROM loom_stops LIMIT 1"):
        return
    t = settings(a.cfg)
    # most recent stops only (independent of the audit's date window): the last few days up to the newest stop
    last_date = a.q("SELECT MAX(stop_date) FROM loom_stops")[0][0]
    if not last_date:
        return
    since = (datetime.fromisoformat(str(last_date)[:10]) - timedelta(days=2)).date().isoformat()
    rows = a.q("""SELECT loom_code, stop_time, restart_time FROM loom_stops
                  WHERE stop_date >= ? AND stop_time IS NOT NULL AND restart_time IS NOT NULL
                  ORDER BY stop_time""", since)
    names = dict(a.q("SELECT loom_code, loom_name FROM looms"))
    nm = lambda c: names.get(c) or names.get(str(_loom_num(c))) or f"loom {c}"   # noqa: E731
    for ep in episodes(rows, t):
        na, nb = nm(ep["a"]), nm(ep["b"])
        where = {"neighbours": " (next to each other)", "near": " (near each other)",
                 "far": " (far apart)", "unknown": ""}[ep["near"]]
        if ep["confirmed"]:
            a.add("ACTION", "Looms", f"sensor-{ep['a']}-{ep['b']}-{ep['first']:%Y%m%d%H%M}",
                  f"{na} and {nb}{where}: {ep['twins']} same-second stops since {ep['first']:%d-%b %H:%M} "
                  f"({ep['share']:.0%} of their stops)",
                  "one loom's sensor may be fitted on the other (the scoreboard then counts one loom twice) - "
                  "check both looms now")
        elif ep["twins"] >= 3 and ep["near"] != "far":
            a.add("WATCH", "Looms", f"sensor-maybe-{ep['a']}-{ep['b']}-{ep['first']:%Y%m%d%H%M}",
                  f"{na} and {nb}{where}: {ep['twins']} same-second stops since {ep['first']:%d-%b %H:%M} "
                  f"({ep['share']:.0%} of their stops)",
                  "a possible sensor move (below the alert threshold) - watch it")
    _stale(a, t)


def _stale(a, t):
    """Push once when the monitor has gone quiet: its newest stop is more than stale_hours old. Without this the
    whole check goes blind silently if the scoreboard PC is off or the copy stops refreshing."""
    newest = a.q("SELECT MAX(restart_time) FROM loom_stops")[0][0] or a.q("SELECT MAX(stop_time) FROM loom_stops")[0][0]
    ts = _parse(newest)
    if not ts:
        return
    hrs = (datetime.now() - ts).total_seconds() / 3600
    if hrs >= float(t["stale_hours"]):
        a.add("ACTION", "Looms", f"sensor-stale-{ts:%Y%m%d%H}",
              f"Loom sensor data has not updated for {hrs:.0f} h (newest stop {ts:%d-%b %H:%M})",
              "the sensor duplicate-timings check is blind until it refreshes - check the scoreboard PC: power, "
              "network, or its export")
