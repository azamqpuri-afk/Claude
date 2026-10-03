"""
54_pilferage_check.py - day-by-day check of Qadri 52/54 cotton (codes 6/18) for signs of pilferage. Read only.
 1. Weft issued to looms vs weft needed for the fabric woven (7-day windows, since production is entered at roll check)
 2. Entry pattern: issues on days with no production, very large issues, entries keyed long after the date or out of hours
 3. Sizing: yarn sent vs sized-beam weight written on the gate pass (loss above 6%)
Writes an Excel workbook to the Drive audit folder.
"""
import importlib.util
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("cc", HERE / "48_contract_closing.py")
cc = importlib.util.module_from_spec(_s); _s.loader.exec_module(cc)
rows = cc.dash.rows
CONTRACTS = (38, 50, 60, 68, 70)
WEFT_KG_PER_M = 82 * 46 * 1.0936 / 800 / 52.5 * 0.45359237      # 90x82 54/54 46", owner count 52.5, per fabric meter
FROM = date(2024, 9, 1)
RED = PatternFill("solid", fgColor="F8C9C9"); AMB = PatternFill("solid", fgColor="FFE3B3")


def main():
    cur = cc.dash.connect(cc.dash.load_config()).cursor()
    cl = ",".join(str(c) for c in CONTRACTS)
    issues = defaultdict(float); ilines = []
    for d, tno, cno, bags, cones, kg, feed, user, loomcnt in rows(cur, f"""
        SELECT TRUNC(m.td), m.tno, c.cont_no, NVL(d.bags,0), NVL(d.cones,0), {cc.KG}, m.feedd, m.usern, COUNT(*) OVER (PARTITION BY m.trans_no)
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        JOIN dostex.contm c ON c.cont_id=d.cont_id
        WHERE m.tt='YARN_TO_LOOMS' AND d.ycode IN (6,18) AND c.cont_no IN ({cl}) AND m.td >= :f0""", f0=FROM):
        d = d.date(); kg = float(kg or 0)
        issues[d] += kg
        ilines.append([d, tno, cno, float(bags), float(cones), round(kg, 1), feed, (user or "").strip()])
    back = defaultdict(float)
    for d, kg in rows(cur, f"""SELECT TRUNC(m.td), SUM({cc.KG}) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no
        LEFT JOIN dostex.yarn y ON y.ycode=d.ycode JOIN dostex.contm c ON c.cont_id=d.cont_id
        WHERE m.tt='YARN_FROM_LOOMS' AND d.ycode IN (6,18) AND c.cont_no IN ({cl}) AND m.td >= :f0 GROUP BY TRUNC(m.td)""", f0=FROM):
        back[d.date()] += float(kg or 0)
    prod = defaultdict(float)
    for d, m in rows(cur, f"""SELECT TRUNC(td), SUM(total_mtr) FROM dostex.wv_prod_v WHERE cont_no IN ({cl}) AND td >= :f0 GROUP BY TRUNC(td)""", f0=FROM):
        prod[d.date()] += float(m or 0)

    # daily table + rolling 7-day comparison
    days = []
    d, end = FROM, date.today()
    while d <= end:
        days.append(d); d += timedelta(days=1)
    daily = []
    for i, d in enumerate(days):
        w = days[max(0, i - 6): i + 1]
        iss = sum(issues[x] - back[x] for x in w); need = sum(prod[x] for x in w) * WEFT_KG_PER_M
        daily.append([d, round(issues[d] - back[d], 1), round(prod[d]), round(prod[d] * WEFT_KG_PER_M, 1), round(iss), round(need), round(iss - need)])

    # entry-level signals
    flags = []
    by_day_lines = defaultdict(list)
    for l in ilines:
        by_day_lines[l[0]].append(l)
    for d, L in by_day_lines.items():
        tot = sum(x[5] for x in L)
        near = sum(prod[d + timedelta(k)] for k in range(-3, 4))
        wk = next(r for r in daily if r[0] == d)
        reasons = []
        if tot >= 300 and near == 0:
            reasons.append(f"{tot:,.0f} kg issued but no production entered within 3 days either side")
        if tot >= 700:
            reasons.append(f"very large day: {tot:,.0f} kg (≈{tot / 45.36:.0f} bags) issued")
        if wk[6] > 400 and wk[4] > 1.3 * max(wk[5], 1):
            reasons.append(f"week to this day: {wk[4]:,} kg issued vs {wk[5]:,} kg needed for fabric woven (+{wk[6]:,} kg)")
        for x in L:
            feed, user = x[6], x[7]
            if feed and (feed.date() - d).days >= 7:
                reasons.append(f"issue {x[1]} keyed in {(feed.date() - d).days} days after its date (on {feed:%d-%b-%y} by {user or '?'})")
            if feed and (feed.hour >= 21 or feed.hour < 6):
                reasons.append(f"issue {x[1]} keyed at {feed:%H:%M} (outside working hours) by {user or '?'}")
        if reasons:
            flags.append([d, round(tot, 1), round(prod[d]), "; ".join(dict.fromkeys(reasons)), len(reasons)])

    # sizing losses per set
    sizing = []
    sent = defaultdict(float)
    for d, an, cno, kg in rows(cur, f"""SELECT TRUNC(m.td), a.an, c.cont_no, SUM({cc.KG}) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no
        LEFT JOIN dostex.yarn y ON y.ycode=d.ycode JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE m.tt IN ('TO_SIZING','FROM_SIZING') AND d.ycode IN (6,18) AND m.td >= :f0 GROUP BY TRUNC(m.td), a.an, c.cont_no ORDER BY 1""".replace(
            "SUM(", "SUM(CASE WHEN m.tt='FROM_SIZING' THEN -1 ELSE 1 END * "), f0=FROM):
        sent[((an or "").strip(), cno)] += float(kg or 0)     # yarn sent less yarn taken back from the sizer
    rec = defaultdict(float); lastd = {}
    for d, an, cno, desc, kg in rows(cur, f"""SELECT TRUNC(m.td), a.an, c.cont_no, m.tdesc, SUM({cc.KG}) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no
        LEFT JOIN dostex.yarn y ON y.ycode=d.ycode JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE m.tt='IGP_SIZED_BEAMS' AND d.ycode IN (6,18) AND m.td >= :f0 GROUP BY TRUNC(m.td), a.an, c.cont_no, m.tdesc ORDER BY 1""", f0=FROM):
        k = ((an or "").strip(), cno)
        actual = cc.desc_kg(desc)
        rec[k] += actual if actual else float(kg or 0)
        lastd[k] = d.date()
    for k, s in sent.items():
        r = rec.get(k, 0)
        if s > 0 and r > 0:
            loss = (s - r) / s * 100
            sizing.append([k[0], k[1], round(s), round(r), round(s - r), round(loss, 1), lastd.get(k)])

    wb = Workbook(); ws = wb.active; ws.title = "Days to check"
    ws.append(["Date", "Weft issued that day (kg)", "Fabric woven that day (m)", "Why flagged", "Signals"])
    for r in sorted(flags, key=lambda r: (-r[4], -r[1])):
        ws.append(r)
        ws.cell(row=ws.max_row, column=1).fill = RED if r[4] >= 2 else AMB
    ws2 = wb.create_sheet("Daily weft vs production")
    ws2.append(["Date", "Weft issued net (kg)", "Fabric woven (m)", "Weft needed for it (kg)", "Issued, last 7 days (kg)", "Needed, last 7 days (kg)", "Excess, last 7 days (kg)"])
    for r in daily:
        ws2.append(r)
        if r[6] > 400 and r[4] > 1.3 * max(r[5], 1):
            ws2.cell(row=ws2.max_row, column=7).fill = RED
    ws3 = wb.create_sheet("Sizing loss by sizer")
    ws3.append(["Sizer", "Contract", "Yarn sent (kg)", "Sized beams back, actual kg", "Difference (kg)", "Loss %", "Last beams back"])
    for r in sorted(sizing, key=lambda r: -r[5]):
        ws3.append(r)
        if r[5] > 6:
            ws3.cell(row=ws3.max_row, column=6).fill = RED
    ws4 = wb.create_sheet("All weft issues")
    ws4.append(["Date", "Issue no.", "Contract", "Bags", "Cones", "Kg", "Keyed at", "User"])
    for l in sorted(ilines):
        ws4.append(l)
    for s in wb.worksheets:
        for c in s[1]:
            c.font = Font(bold=True)
    out = Path(cc.dash.load_config()["output"]["folder"]) / "Claude ERP Audit" / f"Qadri 52-54 yarn day-by-day check {date.today():%Y-%m-%d}.xlsx"
    wb.save(out)
    tot_iss = sum(r[1] for r in daily); tot_need = sum(r[3] for r in daily)
    print(f"period {FROM} to {date.today()}: weft issued {tot_iss:,.0f} kg vs needed {tot_need:,.0f} kg ({(tot_iss - tot_need) / tot_need * 100:+.1f}%)")
    print("flagged days:", len(flags))
    for r in sorted(flags, key=lambda r: (-r[4], -r[1]))[:25]:
        print(f"{r[0]} | {r[1]:,.0f} kg | woven {r[2]:,} m | {r[3]}")
    print("SIZING:")
    for r in sorted(sizing, key=lambda r: -r[5]):
        print(r)
    print("saved", out)


if __name__ == "__main__":
    main()
