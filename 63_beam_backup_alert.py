"""
63_beam_backup_alert.py - push (ntfy) when a beam on a loom will finish within 15 days and there is no filled beam waiting
with the same ends to replace it. Runs after 52_yarn_fabric_data.py in the daily 3 PM task and reads its yarn_fabric_data.json.

Days to finish (same as the dashboard): fabric still to come = total set length x (1 - shrinkage) - fabric woven (as of the
last production check); the loom kept weaving since, so days = (to come - rate x days since check) / rate, with the ERP rate
(last 30 days) and the scoreboard rate (last 14 days) - the EARLIER of the two is used.
Backups: filled beams waiting on the floor, matched on ends (same set ends, or within 5 ends for 8,855/8,856-type sets),
each backup covering one loom - looms finishing first get them first. Yarn at a sizer for the same contract is mentioned.
Each loom beam is pushed once when it first qualifies, and again when it is 5 days or less from finishing.
python 63_beam_backup_alert.py [--dry]
"""
import configparser
import json
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "beam_alert_state.json"
DAYS = 15
DAYS_VA = 7          # owner: VA sets come one beam at a time from the warping centre - alert only within 7 days
URGENT = 5


def ends_match(a, b):
    return bool(a and b) and abs(a - b) <= 5


def main(dry=False):
    cfg = configparser.ConfigParser(); cfg.read(HERE / "report_config.ini")
    data = json.loads((Path(cfg["output"]["folder"]) / "yarn_fabric_data.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(data["today"][:10]) if data.get("today") else date.today()
    ends = data.get("set_ends", {})
    cno_of = {str(c["id"]): str(c["no"]) for c in data["contracts"]}
    qual = lambda s_: (s_ or "").split("(")[0].replace(" ", "")                    # "90X82 54/54 (CTN-68) 46" -> "90X8254/54"  # noqa: E731
    qual_of = {str(c["id"]): qual(c["quality"]) for c in data["contracts"]}
    loom, spare = [], []
    for b in data["beams"]:
        if b[2] == "On loom" and len(b) >= 14:
            since = (today - date.fromisoformat(str(b[10] or b[4])[:10])).days
            days = []
            for rate in (b[11], b[12]):
                if rate:
                    days.append(max(0.0, (b[13] or 0) - rate * since) / rate)
            if not days:
                continue
            d = min(days)
            loom.append(dict(loom=b[3], beam=b[1], set=str(b[8]), ends=ends.get(str(b[8])), detail=b[5], days=d,
                             erp=days[0] if b[11] else None, scb=days[-1] if b[12] else None))
        elif b[2] == "Sized, waiting for loom":
            spare.append(dict(beam=b[1], set=str(b[8]), ends=ends.get(str(b[8])), detail=b[3], used=False))
    loom.sort(key=lambda x: x["days"])
    alerts = []
    for L in loom:
        is_va = "VA" in L["detail"].split(" · ")[0].upper()
        if L["days"] > (DAYS_VA if is_va else DAYS):
            continue
        bk = next((s for s in spare if not s["used"] and ends_match(s["ends"], L["ends"])), None)
        if bk:
            bk["used"] = True
            continue
        cno = "".join(ch for ch in (L["detail"].split("contract")[-1] if "contract" in L["detail"] else "") if ch.isdigit())
        sz = [s for s in data.get("sizers", []) if str(s[1]) and s[4] >= 5 * 45.36 and s[5] and
              (today - date.fromisoformat(str(s[5])[:10])).days <= 60 and qual_of.get(str(s[1])) == qual(L["detail"].split(" · ")[0])]
        alerts.append((L, sz))
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    push = []
    for L, sz in alerts:
        key = f"{L['loom']}|{L['beam']}|{L['set']}"
        level = "urgent" if L["days"] <= URGENT else "first"
        if state.get(key) == "urgent" or state.get(key) == level:
            continue
        state[key] = level
        push.append((L, sz, level))
    # forget looms that are no longer in the list (beam changed)
    live = {f"{L['loom']}|{L['beam']}|{L['set']}" for L, _ in alerts}
    state = {k: v for k, v in state.items() if k in live}
    # one notification per run listing every loom beam that is new on the list or has just become urgent
    if push:
        fin = lambda d_: "now" if d_ is None or d_ < 0.5 else f"{d_:.0f}d"  # noqa: E731
        lines = []
        for L, sz, level in sorted(push, key=lambda p: p[0]["days"]):
            qual = L["detail"].split(" · ")[0].replace("  ", " ").strip()
            line = (f"{'!! ' if level == 'urgent' else ''}{L['loom'].replace('LOOM # ', 'Loom ')}: beam {L['beam']} ends in ~{L['days']:.0f} days "
                    f"(ERP {fin(L['erp'])}, scoreboard {fin(L['scb'])}) - {qual}, {L['ends'] or '?'} ends, no filled backup beam")
            if sz:
                line += " | yarn at sizer for this quality: " + "; ".join(f"{s[0].split()[0].title()} {s[4] / 45.36:.0f} bags (contract {cno_of.get(str(s[1]))}, sent {str(s[5])[8:10]}-{str(s[5])[5:7]})" for s in sz)
            lines.append(line)
        n_urg = sum(1 for p in push if p[2] == "urgent")
        title = f"{len(push)} loom beam(s) ending soon with no backup beam (VA within {DAYS_VA} days, others {DAYS})" + (f" - {n_urg} within {URGENT} days" if n_urg else "")
        body = "\n".join(lines)
        print(("DRY " if dry else "") + "PUSH:", title); print(body)
        if not dry:
            req = urllib.request.Request(url=f"https://ntfy.sh/{cfg.get('ntfy', 'topic')}", data=body.encode("utf-8"), method="POST",
                                         headers={"Title": title, "Priority": "high" if n_urg else "default", "Tags": "warning" if n_urg else "hourglass"})
            with urllib.request.urlopen(req, timeout=15) as r:
                print("  sent", r.status)
    if not dry:
        STATE.write_text(json.dumps(state, indent=1))
    print(f"{datetime.now():%Y-%m-%d %H:%M} looms {len(loom)}, finishing within {DAYS} days without backup {len(alerts)}, pushed {len(push)}")
    for L, sz in alerts:
        print(f"   {L['loom']} {L['beam']} set {L['set']} ends {L['ends']} days {L['days']:.1f}")


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
