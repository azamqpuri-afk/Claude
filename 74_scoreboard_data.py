"""Scoreboard (OLMS, MS Access on the scoreboard PC Y-PC): copy the database to this PC and analyse it loom by loom.

Only a COPY is read (E:\\scoreboard_copy\\OLMSDB_copy.mdb); the live file on Y-PC is never opened. Oracle is read only.
Formulas (checked against the scoreboard's own daily reports, loom 1 on 29-Sep: 206.6 m, 91.6% vs 91.7%):
    meters     = picks x panels / (picks-per-inch x 39.37)       RTLSUM: PPROD x PDELV / (PPINCH x 39.37)
    efficiency = picks / (RPM x shift minutes)                    PPROD / (PRPM x PSHFTDUR / 60)
The scoreboard records only two stop kinds: warp (PWRP) and undefined/other (PUD); weft, beam, power... are always 0.
No weaver names are stored (POPRTR / Poperator are empty), so there is no weaver-wise report.

usage:  python 74_scoreboard_data.py [--nocopy] [--upload [<sheet id>]]   (nightly task: --upload)
"""
import configparser
import importlib.util
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pyodbc
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HERE = Path(__file__).resolve().parent
COPY = Path(r"E:\scoreboard_copy\OLMSDB_copy.mdb")
OUT = HERE.parent / "scoreboard" / "Scoreboard analysis.xlsx"
SHEET = "1TdiDojejpKx3ktCpF3ldUZT5U_PCmBqL-FX90eVvbgg"           # Google Sheet 'Scoreboard analysis - Dost Textiles' in DostTextile_Reports
FROM = date(2025, 12, 1)                     # the scoreboard restarted in Dec-2025 (older data: Nov-2022..Apr-2023)
LONG_STOP_H = 2
B = Font(bold=True); T = Font(bold=True, size=13); RED = PatternFill("solid", fgColor="F8CBAD"); YEL = PatternFill("solid", fgColor="FFF2CC")


def copy_db():
    """Copy the live database from the scoreboard PC; keep the old copy if anything goes wrong."""
    c = configparser.ConfigParser(); c.read(HERE / "scoreboard_share.ini")
    s = c["scoreboard"]
    unc = rf"\\{s['host']}\{s['share']}"
    src = Path(unc) / s["file"]
    if not src.exists():
        subprocess.run(["net", "use", unc, f"/user:{s['host']}\\{s['user']}", s["password"]], capture_output=True)
    if not src.exists():
        print("scoreboard PC not reachable - using the last copy", datetime.fromtimestamp(COPY.stat().st_mtime) if COPY.exists() else "")
        return False
    tmp = COPY.with_suffix(".tmp")
    shutil.copyfile(src, tmp)
    try:
        cn = pyodbc.connect(rf"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={tmp};ReadOnly=1;", readonly=True)
        last = cn.cursor().execute("SELECT MAX(PDATE) FROM RTLSUM").fetchone()[0]; cn.close()
    except Exception as e:
        print("copied file does not open, keeping the old copy:", e); tmp.unlink(missing_ok=True); return False
    tmp.replace(COPY)
    print("copied", f"{COPY.stat().st_size / 1e6:,.0f} MB", "last day", f"{last:%d-%b-%Y}")
    return True


MAX_RPM = 260          # looms here are set to 245 rpm at most (RTLMACH.MSRPM); the scoreboard sometimes dumps a backlog of picks into a short record (15-Sep after the wire cut: 111,039 "rpm")


def bad_reason(r):
    """None = record is fine. Glitch records are left out of everything; zero/odd RPM records count for meters but not for efficiency/speed."""
    if r["picks"] <= 0:
        return None
    if r["rpm"] > MAX_RPM or (r["dur"] and r["picks"] > MAX_RPM * r["dur"] / 60):
        return "glitch"
    if r["rpm"] <= 0 or r["picks"] > 1.1 * r["cap"]:
        return "no speed"
    return None


class Agg:
    __slots__ = ("picks", "epicks", "cap", "m", "rpmw", "wst", "wdur", "ost", "odur", "sdur", "shift", "n", "run")

    def __init__(self):
        for k in self.__slots__:
            setattr(self, k, 0.0)

    def add(self, r):
        if r["bad"] == "glitch":
            return
        self.n += 1; self.shift += r["dur"]; self.sdur += r["stopdur"]
        if r["picks"] > 0:
            self.run += 1; self.picks += r["picks"]; self.m += r["m"]
            self.wst += r["wst"]; self.wdur += r["wdur"]; self.ost += r["ost"]; self.odur += r["odur"]
            if not r["bad"]:
                self.epicks += r["picks"]; self.cap += r["cap"]; self.rpmw += r["rpm"] * r["picks"]

    eff = property(lambda s: s.epicks / s.cap if s.cap else 0)
    rpm = property(lambda s: s.rpmw / s.epicks if s.epicks else 0)
    wp100k = property(lambda s: s.wst / s.picks * 1e5 if s.picks else 0)     # warp breaks per 100,000 picks
    stop_pct = property(lambda s: s.sdur / s.shift if s.shift else 0)


def load(cur):
    rows = []
    for (d, mach, sh, crew, dur, stopdur, picks, delv, pinch, rpm, style, wst, wdur, ost, odur) in cur.execute(
            """SELECT PDATE, PMACH, PSHIFT, Pstrshift, PSHFTDUR, PTSTPDUR, PPROD, PDELV, PPINCH, PRPM, PSTYLEDESC, PWRP, PWRPDUR, PUD, PUDDUR
               FROM RTLSUM WHERE PDATE >= ?""", datetime(FROM.year, FROM.month, FROM.day)):
        picks = float(picks or 0); rpm = float(rpm or 0); dur = float(dur or 0); pinch = float(pinch or 0)
        rows.append(dict(d=d.date(), loom=int(mach), shift=int(sh or 0), crew=(crew or "").strip(), dur=dur, stopdur=float(stopdur or 0),
                         picks=picks, m=picks * float(delv or 0) / (pinch * 39.37) if pinch else 0.0, cap=rpm * dur / 60, rpm=rpm,
                         style=(style or "").strip(), pinch=pinch, delv=float(delv or 0),
                         wst=float(wst or 0), wdur=float(wdur or 0), ost=float(ost or 0), odur=float(odur or 0)))
        rows[-1]["bad"] = bad_reason(rows[-1])
    return rows


def erp_data():
    _d = importlib.util.spec_from_file_location("dash", HERE / "40_business_dashboard.py"); dash = importlib.util.module_from_spec(_d); _d.loader.exec_module(dash)
    cur = dash.connect(dash.load_config()).cursor()
    num = lambda ln: int(re.search(r"\d+", ln).group()) if ln and re.search(r"\d+", ln) else None  # noqa: E731
    month = defaultdict(float)
    for ln, mo, m in cur.execute("""SELECT l.lname, TO_CHAR(p.td, 'YYYY-MM'), SUM(p.total_mtr) FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode
                                    WHERE p.tt = 'PRODUCTION' AND p.td >= :d GROUP BY l.lname, TO_CHAR(p.td, 'YYYY-MM')""", d=datetime(FROM.year, FROM.month, 1)):
        if num(ln):
            month[(mo, num(ln))] += float(m or 0)
    last = {}
    for ln, td, q, pick, w in cur.execute("""SELECT l.lname, p.td, NVL(p.sub_quality, p.quality), p.pick, p.bwidth FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode
                                             WHERE p.tt = 'PRODUCTION' AND p.td >= SYSDATE - 45 ORDER BY p.td"""):
        if num(ln):
            last[num(ln)] = (td.date(), (q or "").strip(), float(pick or 0), float(w or 0))
    return month, last


def long_stops(cur, since):
    out = []
    for loom, st, dur, code in cur.execute("""SELECT RMACH_NO, RSTOP_TIME, RSTOP_DUR, RSTP_CODE FROM LOOMSTAT
                                              WHERE RDATE >= ? AND RSTOP_DUR >= ? ORDER BY RSTOP_DUR DESC""",
                                           datetime(since.year, since.month, since.day), LONG_STOP_H * 3600):
        out.append((int(loom), st, float(dur) / 3600, {2: "Warp", 9: "Other / undefined"}.get(int(code or 0), str(code))))
    return out


def sheet(wb, title, head, rows, widths=None, note=None, fills=None):
    ws = wb.create_sheet(title)
    r0 = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True); r0 = 3
    for j, h in enumerate(head, 1):
        c = ws.cell(row=r0, column=j, value=h); c.font = B; c.alignment = Alignment(wrap_text=True, vertical="top")
    for i, row in enumerate(rows, r0 + 1):
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            if isinstance(v, float):
                c.number_format = "0.0%" if head[j - 1].endswith("%") else "#,##0.0" if abs(v) < 100 else "#,##0"
        if fills and fills[i - r0 - 1]:
            for j in range(1, len(head) + 1):
                ws.cell(row=i, column=j).fill = fills[i - r0 - 1]
    for j in range(1, len(head) + 1):
        ws.column_dimensions[get_column_letter(j)].width = (widths or {}).get(j, 13)
    ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    return ws


def write_rpm_files(rows):
    """Per-loom daily RPM for the Mill Floor dashboard: scb_rpm_loomwise_<date>.csv (LOOM_NO, RPM) in the reports folder,
    which 40_business_dashboard.build_rpm already reads. RPM = picks-weighted, glitch / no-speed records left out."""
    cfg = configparser.ConfigParser(); cfg.read(HERE / "report_config.ini")
    out = Path(cfg["output"]["folder"])
    acc = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        if r["picks"] > 0 and not r["bad"]:
            a = acc[(r["d"], r["loom"])]; a[0] += r["rpm"] * r["picks"]; a[1] += r["picks"]
    days = defaultdict(list)
    for (d, loom), (w, p) in acc.items():
        days[d].append((loom, round(w / p)))
    for d, lst in days.items():
        (out / f"scb_rpm_loomwise_{d:%Y-%m-%d}.csv").write_text(
            "LOOM_NO,RPM\n" + "".join(f"{l},{v}\n" for l, v in sorted(lst)), encoding="utf-8")
    print(f"rpm files: {len(days)} days written to {out}")
    # shed per day (for the Mill Floor 30-day averages and shed RPM): efficiency %, meters, RPM
    day = defaultdict(Agg)
    # a scoreboard day runs 06:00 to 06:00 next morning: leave out a day whose night shift had not ended when the copy was taken
    copied = datetime.fromtimestamp(COPY.stat().st_mtime)
    for r in rows:
        if datetime(r["d"].year, r["d"].month, r["d"].day) + timedelta(hours=30) <= copied:
            day[r["d"]].add(r)
    (out / "scb_daily_from_db.csv").write_text("DATE,SCB_EFF,SCB_PRODUCTION,SCB_RPM\n" + "".join(
        f"{d:%Y-%m-%d},{a.eff * 100:.1f},{a.m:.0f},{a.rpm:.0f}\n" for d, a in sorted(day.items()) if a.picks and a.cap), encoding="utf-8")


def main(do_copy=True):
    if do_copy:
        copy_db()
    cn = pyodbc.connect(rf"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={COPY};ReadOnly=1;", readonly=True)
    cur = cn.cursor()
    rows = load(cur)
    write_rpm_files(rows)
    last_day = max(r["d"] for r in rows)
    d30 = last_day - timedelta(days=29)
    stops = long_stops(cur, d30)
    erp_m, erp_last = erp_data()

    day, mon, ml, l30, crew, shift, style, latest = tuple(defaultdict(Agg) for _ in range(7)) + ({},)
    day_looms = defaultdict(set)
    for r in rows:
        mo = f"{r['d']:%Y-%m}"
        day[r["d"]].add(r); mon[mo].add(r); ml[(mo, r["loom"])].add(r)
        crew[(mo, r["crew"] or "?")].add(r); shift[(mo, "Day (shift 1)" if r["shift"] == 1 else "Night (shift 2)")].add(r)
        style[(mo, r["style"])].add(r)
        if r["picks"] > 0 and r["bad"] != "glitch":
            day_looms[r["d"]].add(r["loom"])
        if r["d"] >= d30:
            l30[r["loom"]].add(r)
        if r["picks"] > 0 and (r["loom"] not in latest or r["d"] >= latest[r["loom"]]["d"]):
            latest[r["loom"]] = r
    looms = sorted({r["loom"] for r in rows})
    findings = []

    # ---- daily mill
    drows, dfill = [], []
    d = FROM
    while d <= last_day:
        a = day.get(d)
        if not a or a.picks == 0:
            drows.append((d, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0.0, "NO DATA (scoreboard off / wire cut)")); dfill.append(RED)
        else:
            usual = sorted(len(day_looms[x]) for x in day_looms if x.month == d.month and x.year == d.year)
            part = len(day_looms[d]) < 0.8 * usual[len(usual) // 2]
            drows.append((d, len(day_looms[d]), round(a.m), a.eff, a.rpm, a.wp100k, int(a.wst), int(a.ost), a.sdur / 3600,
                          "partial - fewer looms counted" if part else "")); dfill.append(YEL if part else None)
        d += timedelta(days=1)
    nodata = [r[0] for r in drows if r[1] == 0]

    # ---- monthly mill + ERP
    mrows = []
    for mo in sorted(mon):
        a = mon[mo]; e = sum(v for (m_, _), v in erp_m.items() if m_ == mo)
        nd = sum(1 for r in drows if f"{r[0]:%Y-%m}" == mo and r[1] == 0)
        mrows.append((mo, round(a.m), round(e), e / a.m if a.m else 0.0, a.eff, a.rpm, a.wp100k, a.stop_pct, nd))

    # ---- per loom per month: scoreboard vs ERP
    lrows, lfill = [], []
    gaps = defaultdict(int)                  # days with no / partial scoreboard data per month
    for r, f in zip(drows, dfill):
        if f:
            gaps[f"{r[0]:%Y-%m}"] += 1
    for mo in sorted(mon):
        unfair = gaps[mo] >= 5               # scoreboard missing on many days -> its monthly count is short for some looms
        a_m = mon[mo]; e_m = sum(v for (m_, ln), v in erp_m.items() if m_ == mo and ml.get((mo, ln)) and ml[(mo, ln)].m > 0)
        mill = e_m / a_m.m if a_m.m else 0
        for ln in looms:
            a = ml.get((mo, ln)); e = erp_m.get((mo, ln), 0.0)
            if not a and not e:
                continue
            s = a.m if a else 0.0
            dev = (e / s) / mill - 1 if s > 50 and mill else None
            flag = ""
            if unfair:
                pass
            elif s < 50 and e > 300:
                flag = "ERP production but scoreboard counted nothing"
            elif dev is not None and abs(dev) > 0.15 and abs(e - s * mill) > 1200:     # one roll (~500-1000 m) either side of month-end is normal
                flag = "ERP higher than loom's count" if dev > 0 else "ERP lower than loom's count (rolls not entered?)"
            lrows.append((mo, ln, round(s), round(e), dev if dev is not None else "", a.eff if a else "", a.rpm if a else "", flag))
            lfill.append(RED if flag else None)
    cur_mo = f"{last_day:%Y-%m}"; prev_mo = f"{last_day.replace(day=1) - timedelta(days=1):%Y-%m}"
    fair = [mo for mo in sorted(mon) if gaps[mo] < 5 and mo != cur_mo or (mo == cur_mo and gaps[mo] < 5 and last_day.day >= 28)]
    cmp_mo = fair[-1] if fair else prev_mo
    bad = [r for r in lrows if r[0] == cmp_mo and r[7]]
    if bad:
        findings.append(f"{cmp_mo} (latest month with full scoreboard data): {len(bad)} looms - " + ", ".join(f"loom {r[1]} ERP {r[3]:,} m vs scoreboard {r[2]:,} m" for r in bad[:8]) + " - "
                        f"ERP and the loom's own count differ by more than 15% after the mill's normal ERP/scoreboard ratio (see 'Loom ERP vs scoreboard').")

    # ---- loom ranking, last 30 days
    mill30 = Agg()
    for r in rows:
        if r["d"] >= d30:
            mill30.add(r)
    best_rpm = {}
    for (mo, ln), a in ml.items():
        if a.picks and a.run >= 10:
            best_rpm[ln] = max(best_rpm.get(ln, 0), a.rpm)
    days30 = sum(1 for r in drows if r[0] >= d30 and r[1] > 0) or 1
    rrows, rfill = [], []
    for ln in sorted(l30, key=lambda k: l30[k].eff):
        a = l30[ln]
        if not a.picks:
            rrows.append((ln, "", 0, 0.0, 0.0, "", 0.0, 0.0, "loom not running / not counted in last 30 days")); rfill.append(YEL); continue
        why = []
        if a.eff < mill30.eff - 0.10:
            why.append(f"efficiency {a.eff:.0%} vs mill {mill30.eff:.0%}")
        if best_rpm.get(ln) and a.rpm < 0.9 * best_rpm[ln]:
            why.append(f"speed {a.rpm:.0f} rpm vs its best month {best_rpm[ln]:.0f}")
        if a.wp100k > 2 * mill30.wp100k:
            why.append(f"warp breaks {a.wp100k:.1f}/100k picks vs mill {mill30.wp100k:.1f}")
        lt = latest.get(ln)
        rrows.append((ln, lt["style"] if lt else "", round(a.m / days30), a.eff, a.rpm, round(best_rpm.get(ln, 0)), a.wp100k, a.stop_pct, "; ".join(why)))
        rfill.append(RED if why else None)
    weak = [r for r in rrows if r[8] and not r[8].startswith("loom not")]
    if weak:
        findings.append(f"Last 30 days: {len(weak)} looms below par (efficiency 10 points under the mill, speed down 10%+, or double the warp breaks): "
                        + ", ".join(f"loom {r[0]}" for r in weak[:12]) + ".")

    # ---- settings: scoreboard picks/inch and panels vs ERP
    srows, sfill = [], []
    for ln in looms:
        s = latest.get(ln); e = erp_last.get(ln)
        if not s and not e:
            continue
        flag = ""
        if s and e:
            if e[2] and s["pinch"] and abs(s["pinch"] - e[2]) >= 2:
                q = re.match(r"\s*\d+\s*X\s*(\d+)", e[1], re.I); q = float(q.group(1)) if q else None
                if q and abs(q - s["pinch"]) < 2:
                    flag = f"ERP entry has {e[2]:.0f} picks but the quality is {e[1].split()[0]} and the scoreboard {s['pinch']:.0f} - ERP pick field looks wrong"
                elif q and abs(q - e[2]) < 2:
                    flag = (f"ERP last entry {e[0]:%d-%b} says {e[1].split()[0]} ({e[2]:.0f} picks) but the scoreboard is still set to {s['pinch']:.0f} picks ({s['style']}) - "
                            f"check on the loom; if it weaves {e[2]:.0f} picks, change the style on the scoreboard PC (its meters are {1 - e[2] / s['pinch']:.0%} too {'low' if s['pinch'] > e[2] else 'high'}), else the ERP entry is wrong")
                else:
                    flag = f"picks/inch: scoreboard {s['pinch']:.0f}, ERP entry {e[2]:.0f}, quality {e[1].split()[0]} - check on the loom"
            if e[3] and s["delv"] and s["delv"] != e[3]:
                flag += ("; " if flag else "") + f"panels differ ({s['delv']:.0f} vs ERP {e[3]:.0f})"
        srows.append((ln, s["d"] if s else "", s["style"] if s else "", s["pinch"] if s else "", s["delv"] if s else "",
                      e[0] if e else "", e[1] if e else "", e[2] if e else "", e[3] if e else "", flag))
        sfill.append(RED if flag else None)
    wrong = [r for r in srows if r[9]]
    if wrong:
        findings += [f"Loom {r[0]} settings: {r[9]}." for r in wrong]
    idle = [r for r in srows if r[1] and r[1] < last_day - timedelta(days=7)]
    if idle:
        findings.append("Looms not running on the scoreboard: " + ", ".join(f"loom {r[0]} (last ran {r[1]:%d-%b})" for r in idle) + ".")

    # ---- crews, day/night, qualities (last 3 months)
    last3 = sorted(mon)[-3:]
    crows = [(mo, k, round(a.m), a.eff, a.rpm, a.wp100k, a.stop_pct) for (mo, k), a in sorted(crew.items()) if mo in last3 and a.picks]
    crows += [(mo, k, round(a.m), a.eff, a.rpm, a.wp100k, a.stop_pct) for (mo, k), a in sorted(shift.items()) if mo in last3 and a.picks]
    qrows = [(mo, k, round(a.m), a.eff, a.rpm, a.wp100k, a.stop_pct, int(a.run)) for (mo, k), a in sorted(style.items()) if mo in last3 and a.picks]
    pm = [(k, a) for (mo, k), a in shift.items() if mo == cmp_mo and a.picks]
    if len(pm) == 2:
        (k1, a1), (k2, a2) = sorted(pm, key=lambda x: x[1].eff)
        if a2.eff - a1.eff >= 0.03:
            findings.append(f"{cmp_mo}: {k1} efficiency {a1.eff:.0%} vs {k2} {a2.eff:.0%} - {a2.eff - a1.eff:.0%} points lower.")

    # ---- long stops
    lsrows = [(ln, st, h, code) for ln, st, h, code in stops[:400]]
    per_loom = defaultdict(float)
    for ln, st, h, code in stops:
        per_loom[ln] += h
    if per_loom:
        top = sorted(per_loom.items(), key=lambda x: -x[1])[:5]
        findings.append(f"Long stops (>{LONG_STOP_H} h) in the last 30 days: {len(stops)}; most hours lost on " + ", ".join(f"loom {k} ({v:,.0f} h)" for k, v in top) + ".")
    glitch = [r for r in rows if r["bad"] == "glitch"]
    nospeed = [r for r in rows if r["bad"] == "no speed"]
    brows = [(r["d"], r["loom"], r["shift"], r["bad"], r["rpm"], r["picks"], r["dur"] / 60, round(r["m"]))
             for r in sorted(glitch + nospeed, key=lambda x: (x["d"], x["loom"]), reverse=True)]
    g30 = defaultdict(int)
    for r in glitch:
        if r["d"] >= d30:
            g30[r["loom"]] += 1
    if g30:
        findings.append(f"Last 30 days: {sum(g30.values())} scoreboard records with impossible speed/picks (left out); looms with repeats: "
                        + (", ".join(f"loom {k} ({v})" for k, v in sorted(g30.items(), key=lambda x: -x[1]) if v >= 3) or "none")
                        + ". Repeats on one loom point to a faulty sensor/counter on that loom.")
    if nodata:
        findings.append(f"Days with no scoreboard data since {FROM:%b-%Y}: {len(nodata)} (latest {', '.join(f'{x:%d-%b}' for x in nodata[-8:])}).")
    a = mon.get(cmp_mo)
    head = [f"Scoreboard data to {last_day:%d-%b-%Y} (copy taken {datetime.fromtimestamp(COPY.stat().st_mtime):%d-%b %H:%M}).",
            f"Last 30 days: {mill30.m / days30:,.0f} m a day, efficiency {mill30.eff:.1%}, average speed {mill30.rpm:.0f} rpm, warp breaks {mill30.wp100k:.1f} per 100k picks, "
            f"stopped {mill30.stop_pct:.0%} of the time."]
    if a:
        head.append(f"{cmp_mo}: {a.m:,.0f} m on the scoreboard, efficiency {a.eff:.1%}.")

    wb = Workbook(); ws = wb.active; ws.title = "Summary"
    ws["A1"] = "Scoreboard analysis - Dost Textiles"; ws["A1"].font = T
    i = 3
    for t in head:
        ws.cell(row=i, column=1, value=t); i += 1
    i += 1; ws.cell(row=i, column=1, value="Worth checking").font = B; i += 1
    for t in findings or ["Nothing out of line."]:
        ws.cell(row=i, column=1, value="- " + t).alignment = Alignment(wrap_text=True); i += 1
    i += 1; ws.cell(row=i, column=1, value="How the numbers are made").font = B; i += 1
    for t in ["meters = picks x panels / (picks-per-inch x 39.37) - the scoreboard's own formula (loom 1, 29-Sep: 206.6 m, same as its report).",
              "efficiency = picks / (loom speed x shift minutes); speed = RPM measured by the scoreboard.",
              "Stops: the scoreboard only separates WARP stops; everything else is 'undefined/other'. Weft, beam, power, mechanical are not recorded.",
              "Weaver names are not stored on the scoreboard, so a weaver-wise report is not possible from it.",
              "ERP vs scoreboard is compared per month: ERP enters a roll every ~3 days, so single days do not match. Each loom is compared after the mill's own ERP/scoreboard ratio.",
              "Read-only: a copy of the database is analysed; nothing is written to the scoreboard PC or the ERP."]:
        ws.cell(row=i, column=1, value=t); i += 1
    ws.column_dimensions["A"].width = 160

    sheet(wb, "Daily mill", ["Date", "Looms running", "Meters", "Efficiency %", "Avg RPM", "Warp breaks /100k picks", "Warp stops", "Other stops", "Stop hours", "Note"],
          drows, {1: 12, 10: 36}, fills=dfill)
    sheet(wb, "Monthly mill", ["Month", "Scoreboard m", "ERP m", "ERP / scoreboard", "Efficiency %", "Avg RPM", "Warp breaks /100k picks", "Stopped %", "Days no data"], mrows)
    sheet(wb, "Loom ranking 30d", ["Loom", "Quality (scoreboard)", "Meters a day", "Efficiency %", "Avg RPM", "Best month RPM", "Warp breaks /100k picks", "Stopped %", "Why flagged"],
          rrows, {2: 26, 9: 70}, note=f"{d30:%d-%b} to {last_day:%d-%b}; mill: efficiency {mill30.eff:.1%}, {mill30.rpm:.0f} rpm, {mill30.wp100k:.1f} warp breaks/100k picks. Worst efficiency first.", fills=rfill)
    sheet(wb, "Loom ERP vs scoreboard", ["Month", "Loom", "Scoreboard m", "ERP m", "Deviation from mill ratio %", "Efficiency %", "Avg RPM", "Flag"],
          lrows, {8: 48}, note="Deviation = (loom's ERP/scoreboard) / (mill's ERP/scoreboard) - 1. Flagged when over 15% and over 1,200 m (one roll either side of month-end is normal).", fills=lfill)
    sheet(wb, "Settings check", ["Loom", "Scoreboard last day", "Scoreboard quality", "Scoreboard picks/inch", "Scoreboard panels",
                                 "ERP last entry", "ERP quality", "ERP picks/inch", "ERP panels", "Flag"], srows, {2: 12, 3: 26, 7: 36, 10: 50}, fills=sfill)
    sheet(wb, "Shifts & crews", ["Month", "Shift / crew", "Meters", "Efficiency %", "Avg RPM", "Warp breaks /100k picks", "Stopped %"], crows, {2: 18})
    sheet(wb, "Qualities", ["Month", "Quality (scoreboard)", "Meters", "Efficiency %", "Avg RPM", "Warp breaks /100k picks", "Stopped %", "Loom-shifts"], qrows, {2: 30})
    sheet(wb, "Long stops 30d", ["Loom", "Stopped at", "Hours", "Kind"], lsrows, {2: 18, 4: 18}, note=f"Single stops longer than {LONG_STOP_H} hours, {d30:%d-%b} to {last_day:%d-%b}, longest first.")
    sheet(wb, "Bad records", ["Date", "Loom", "Shift", "Problem", "RPM recorded", "Picks", "Record minutes", "Meters (from picks)"], brows, {4: 12},
          note=f"glitch = speed over {MAX_RPM} rpm or more picks than possible in the record time - left out of every total. "
               "no speed = picks but RPM 0 / too low - meters counted, efficiency and speed not.")
    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    print("\n".join(head)); print("FINDINGS:"); print("\n".join("- " + f for f in findings)); print("saved", OUT)


def upload(fid):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
    build("drive", "v3", credentials=creds).files().update(fileId=fid, media_body=MediaFileUpload(
        str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
    print("uploaded", fid)


if __name__ == "__main__":
    main(do_copy="--nocopy" not in sys.argv)
    if "--upload" in sys.argv:
        i = sys.argv.index("--upload")
        upload(sys.argv[i + 1] if len(sys.argv) > i + 1 else SHEET)
