"""
77_sensor_twin_check.py - catch a loom's sensor moved onto another loom (owner, 3-Oct-2026).
A stopped loom's sensor fitted on a running loom makes the scoreboard count ONE loom twice: both loom numbers then stop and
restart at the same seconds and run at the same speed, while the stopped loom produces nothing.
Test, per pair of looms, on the scoreboard stop log (LOOMSTAT - every stop with stop time and restart time to the second):
  a "twin stop" = both looms stop within TOL seconds of each other AND restart within TOL seconds of each other
  (stops that hit 4+ looms at once - power cuts, the scoreboard's polling bursts - are left out).
Independent looms do this by chance only rarely; a moved sensor does it on almost every stop.
Reads only the copy E:\\scoreboard_copy\\OLMSDB_copy.mdb (74_ refreshes it; --copy refreshes it first).
python 77_sensor_twin_check.py --from 2025-12-01 [--to 2026-10-02]      history -> xlsx
python 77_sensor_twin_check.py --live [--copy] [--push]                 the running shift (live alert)
"""
import configparser
import importlib.util
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pyodbc

HERE = Path(__file__).resolve().parent
COPY = Path(r"E:\scoreboard_copy\OLMSDB_copy.mdb")
TOL = 2                 # seconds
GAP = 20                # minutes between twin stops that still belong to one episode
EP_MIN = 5              # twin stops in an episode to flag it
EP_SHARE = 0.60         # ... and at least this share of the smaller loom's stops inside the episode
MIN_TWINS, MIN_SHARE = EP_MIN, EP_SHARE
STATE = HERE / "sensor_twin_state.json"
OUT = HERE.parent / "scoreboard" / "sensor_twin_check.xlsx"


def layout():
    """Loom positions as on the scoreboard 'View Shed' screen (owner's photo, 3-Oct-2026) - replace with the floor plan when sent.
    Shed 1: column 0 = even looms 2..18 top to bottom, column 1 = odd looms 1..17. Shed 2: column 0 = 20..36, column 1 = 19..35."""
    pos = {}
    for i, n in enumerate(range(2, 19, 2)):
        pos[n] = (1, 0, i); pos[n - 1] = (1, 1, i)
    for i, n in enumerate(range(20, 37, 2)):
        pos[n] = (2, 0, i); pos[n - 1] = (2, 1, i)
    return pos


POS = layout()


def nearness(a, b):
    """neighbours: same shed, next to each other (side by side or one row apart) - a sensor can be swapped in a minute;
    near: same shed, two rows apart; far: anything else - treated as coincidence and never flagged"""
    pa, pb = POS.get(a), POS.get(b)
    if not pa or not pb or pa[0] != pb[0]:
        return "far"
    d = max(abs(pa[1] - pb[1]), abs(pa[2] - pb[2]))
    return "neighbours" if d <= 1 else "near" if d <= 2 else "far"


def con():
    return pyodbc.connect(rf"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={COPY};ReadOnly=1;", readonly=True).cursor()


def shift_of(t):
    """scoreboard shifts: day 06:00-18:00 (1), night 18:00-06:00 (2); night belongs to the date it started"""
    if 6 <= t.hour < 18:
        return t.date(), 1
    return (t - timedelta(hours=6)).date(), 2


def scan(cur, d0, d1):
    rows = cur.execute("""SELECT RMACH_NO, RSTOP_TIME, RCUR_TIME FROM LOOMSTAT WHERE RDATE BETWEEN ? AND ? AND RSTOP_TIME IS NOT NULL
                          AND RCUR_TIME IS NOT NULL ORDER BY RSTOP_TIME""",
                       datetime(d0.year, d0.month, d0.day), datetime(d1.year, d1.month, d1.day)).fetchall()
    ev = [(int(m), s, r) for m, s, r in rows]
    stops = defaultdict(int)                  # (shiftdate, shift, loom) -> stops
    for m, s, r in ev:
        stops[shift_of(s) + (m,)] += 1
    twins = defaultdict(list)                 # (shiftdate, shift, a, b) -> [stop times]
    n = len(ev); i = 0
    while i < n:
        j = i
        while j + 1 < n and (ev[j + 1][1] - ev[i][1]).total_seconds() <= TOL:
            j += 1
        grp = ev[i:j + 1]
        if 2 <= len({g[0] for g in grp}) <= 3:            # 4+ looms at once = shed-wide event, not a sensor
            for x in range(len(grp)):
                for y in range(x + 1, len(grp)):
                    a, b = grp[x], grp[y]
                    if a[0] != b[0] and abs((a[2] - b[2]).total_seconds()) <= TOL:
                        k = shift_of(a[1]) + tuple(sorted((a[0], b[0])))
                        twins[k].append(a[1])
        i = j + 1 if j > i else i + 1
    # owner: they move the sensor for about an hour and put it back -> look for EPISODES, not whole shifts:
    # twin stops of one pair no more than GAP minutes apart form an episode; flag it when it has EP_MIN twin stops and
    # the twins are EP_SHARE+ of the smaller loom's stops inside the episode (both looms stopping only together)
    times = defaultdict(list)
    for m, s, r in ev:
        times[m].append(s)
    out = []
    for (sd, sh, a, b), ts in twins.items():
        ts = sorted(set(ts))
        sa, sb = stops.get((sd, sh, a), 0), stops.get((sd, sh, b), 0)
        eps, cur_ = [], [ts[0]]
        for t in ts[1:]:
            if (t - cur_[-1]).total_seconds() <= GAP * 60:
                cur_.append(t)
            else:
                eps.append(cur_); cur_ = [t]
        eps.append(cur_)
        for e in eps:
            lo, hi = e[0] - timedelta(seconds=TOL), e[-1] + timedelta(seconds=TOL)
            na = sum(1 for t in times[a] if lo <= t <= hi); nb = sum(1 for t in times[b] if lo <= t <= hi)
            share = len(e) / max(1, min(na, nb))
            near = nearness(a, b)
            out.append(dict(date=sd, shift=sh, a=a, b=b, twins=len(e), share=share, stops_a=na, stops_b=nb, shift_stops=(sa, sb),
                            first=e[0], last=e[-1], mins=round((e[-1] - e[0]).total_seconds() / 60), near=near,
                            score=len(e) * share * {"neighbours": 1.0, "near": 0.5, "far": 0.0}[near],
                            flag=len(e) >= EP_MIN and share >= EP_SHARE and near != "far"))
    return out, stops


def shift_detail(cur, sd, sh, a, b):
    r = {int(m): (float(rpm or 0), float(p or 0), (st or "").strip(), (crew or "").strip())
         for m, rpm, p, st, crew in cur.execute("SELECT PMACH, PRPM, PPROD, PSTYLEDESC, Pstrshift FROM RTLSUM WHERE PDATE = ? AND PSHIFT = ? AND PMACH IN (?, ?)",
                                                datetime(sd.year, sd.month, sd.day), sh, a, b)}
    return r.get(a), r.get(b)


def push(title, body):
    cfg = configparser.ConfigParser(); cfg.read(HERE / "report_config.ini")
    req = urllib.request.Request(url=f"https://ntfy.sh/{cfg.get('ntfy', 'topic')}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title, "Priority": "high", "Tags": "warning"})
    urllib.request.urlopen(req, timeout=30).read()


def history(d0, d1):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    cur = con()
    res, stops = scan(cur, d0, d1)
    flagged = sorted([x for x in res if x["flag"]], key=lambda x: (x["date"], x["shift"], -x["twins"]))
    # chance level: twin stops per pair-shift over all pairs that were NOT flagged
    rest = [x["twins"] for x in res if not x["flag"]]
    wb = Workbook(); ws = wb.active; ws.title = "Flagged shifts"
    ws.append([f"SENSOR TWINS - pairs of looms stopping AND restarting within {TOL} s of each other, {d0:%d-%b-%Y} to {d1:%d-%b-%Y}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Episode = twin stops of one pair no more than {GAP} min apart. Flag: {EP_MIN}+ twin stops and {EP_SHARE:.0%}+ of the smaller loom's stops in the episode. Unflagged episodes average "
               f"{(sum(rest) / max(1, len(rest))):.2f} twin stops (chance level)."])
    ws.append([])
    ws.append(["Date", "Shift", "Loom A", "Loom B", "Twin stops", "Share of stops", "Stops A", "Stops B", "From", "To", "Minutes",
               "RPM A", "RPM B", "Picks A", "Picks B", "Quality A", "Quality B", "Crew"])
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3B57")
    for x in flagged:
        A, B = shift_detail(cur, x["date"], x["shift"], x["a"], x["b"])
        ws.append([x["date"], "day" if x["shift"] == 1 else "night", x["a"], x["b"], x["twins"], round(x["share"], 2), x["stops_a"], x["stops_b"],
                   x["first"].strftime("%H:%M"), x["last"].strftime("%H:%M"), x["mins"], A and A[0], B and B[0], A and A[1], B and B[1],
                   A and A[2], B and B[2], (A or B or ("", "", "", ""))[3]])
    by = defaultdict(lambda: [0, 0])
    for x in flagged:
        by[(x["a"], x["b"])][0] += 1; by[(x["a"], x["b"])][1] += x["twins"]
    s = wb.create_sheet("By pair")
    s.append(["Loom A", "Loom B", "Flagged shifts", "Twin stops"])
    for (a, b), (n, t) in sorted(by.items(), key=lambda z: -z[1][0]):
        s.append([a, b, n, t])
    OUT.parent.mkdir(exist_ok=True); wb.save(OUT)
    top = sorted(res, key=lambda x: -x["twins"])[:15]
    print(f"pair-shifts scanned: {len(res)} · flagged: {len(flagged)} · chance level {(sum(rest) / max(1, len(rest))):.2f}")
    print("highest twin counts:", [(f"{x['date']:%d-%b}", x['shift'], x['a'], x['b'], x['twins'], round(x['share'], 2)) for x in top])
    print("flagged by pair:", dict(sorted(by.items(), key=lambda z: -z[1][0])[:15]))
    print("saved", OUT)


def live(do_copy, do_push):
    if do_copy:
        _s = importlib.util.spec_from_file_location("s74", HERE / "74_scoreboard_data.py"); s74 = importlib.util.module_from_spec(_s); _s.loader.exec_module(s74)
        s74.copy_db()
    now = datetime.fromtimestamp(COPY.stat().st_mtime)
    sd, sh = shift_of(now)
    start = datetime(sd.year, sd.month, sd.day, 6 if sh == 1 else 18)
    cur = con()
    res, _ = scan(cur, start.date() - timedelta(days=1), now.date())
    hits = [x for x in res if x["date"] == sd and x["shift"] == sh and x["twins"] >= MIN_TWINS and x["share"] >= MIN_SHARE]
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    for x in hits:
        key = f"{sd}-{sh}-{x['a']}-{x['b']}-{x['first']:%H%M}"
        print(f"TWINS {sd} shift {sh}: loom {x['a']} & loom {x['b']} - {x['twins']} twin stops {x['first']:%H:%M}-{x['last']:%H:%M}")
        if do_push and key not in state:
            push(f"Sensor check: loom {x['a']} & loom {x['b']}",
                 f"Looms {x['a']} and {x['b']} have stopped and restarted together {x['twins']} times since {x['first']:%H:%M} "
                 f"(last {x['last']:%H:%M}). One sensor may be on the wrong loom - check both looms now.")
            state[key] = datetime.now().isoformat(timespec="minutes")
    STATE.write_text(json.dumps(state))
    if not hits:
        print(f"no twins in the running shift ({sd} shift {sh}, copy {now:%H:%M})")


def night(d, do_copy, do_push):
    """Morning report for the night shift of day d (18:00 d -> 06:00 d+1): every pair of looms that stopped and restarted at the
    same second, most likely sensor moves first. Pushed like the daily production notification."""
    if do_copy:
        _s = importlib.util.spec_from_file_location("s74", HERE / "74_scoreboard_data.py"); s74 = importlib.util.module_from_spec(_s); _s.loader.exec_module(s74)
        s74.copy_db()
    cur = con()
    res, stops = scan(cur, d, d + timedelta(days=1))
    eps = [x for x in res if x["date"] == d and x["shift"] == 2 and x["twins"] >= 2]
    likely = sorted([x for x in eps if x["flag"]], key=lambda x: -x["score"])
    possible = sorted([x for x in eps if not x["flag"] and x["near"] != "far" and x["twins"] >= 3], key=lambda x: -x["score"])
    far = [x for x in eps if x["near"] == "far"]
    recorded = sum(1 for k in stops if k[0] == d and k[1] == 2)
    line = lambda x: (f"Loom {x['a']} & {x['b']} ({x['near']}): {x['twins']} same-second stops {x['first']:%H:%M}-{x['last']:%H:%M}"  # noqa: E731
                      f" ({x['mins']} min), {x['share']:.0%} of their stops")
    body = []
    if recorded == 0:
        body.append("No scoreboard stop data for this night - scoreboard off or wire cut?")
    if likely:
        body.append("LIKELY sensor moved:"); body += ["• " + line(x) for x in likely]
    if possible:
        body.append("Possible (fewer matches):"); body += ["• " + line(x) for x in possible[:6]]
    body.append(f"Looms far apart that matched by chance: {len(far)} pair(s), ignored.")
    if not likely and not possible and recorded:
        body.insert(0, "No sensor moves found - no two nearby looms stopped together.")
    title = f"Night shift {d:%d-%b}: " + (f"{len(likely)} likely sensor move(s)" if likely else "sensor check clear")
    text = "\n".join(body)
    print(title); print(text)
    hist = HERE.parent / "scoreboard" / "sensor_twin_nightly.csv"
    new = not hist.exists()
    with hist.open("a", encoding="utf-8") as f:
        if new:
            f.write("night,loom_a,loom_b,nearness,twins,share,from,to,minutes,verdict\n")
        for x in likely + possible:
            f.write(f"{d},{x['a']},{x['b']},{x['near']},{x['twins']},{x['share']:.2f},{x['first']:%H:%M},{x['last']:%H:%M},{x['mins']},{'likely' if x['flag'] else 'possible'}\n")
    if do_push:
        cfg = configparser.ConfigParser(); cfg.read(HERE / "report_config.ini")
        req = urllib.request.Request(url=f"https://ntfy.sh/{cfg.get('ntfy', 'topic')}", data=text.encode("utf-8"), method="POST",
                                     headers={"Title": title, "Priority": "high" if likely else "default", "Tags": "warning" if likely else "white_check_mark"})
        urllib.request.urlopen(req, timeout=30).read()


if __name__ == "__main__":
    a = sys.argv
    if "--night" in a:
        i = a.index("--night")
        d = date.fromisoformat(a[i + 1]) if len(a) > i + 1 and not a[i + 1].startswith("--") else date.today() - timedelta(days=1)
        night(d, "--copy" in a, "--push" in a)
    elif "--live" in a:
        live("--copy" in a, "--push" in a)
    else:
        d0 = date.fromisoformat(a[a.index("--from") + 1]) if "--from" in a else date.today() - timedelta(days=30)
        d1 = date.fromisoformat(a[a.index("--to") + 1]) if "--to" in a else date.today()
        history(d0, d1)
