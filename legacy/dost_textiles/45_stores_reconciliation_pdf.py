"""
45_stores_reconciliation_pdf.py
Stores & spares check (2026-09-26): accounts' monthly consumption journal vs
what the store actually issued (issue slips ISS minus return slips RET, at
the store's average rate), every month since the store module started:
  old ERP (DOSTT) Oct-2016 .. Sep-2023, new ERP (DOSTEX) Oct-2023 onward.
Flags months that differ by Rs 50,000 or more, with a likely reason.
Read-only on the ERP. load_store_gap() also feeds the year-end true-up in
closing_entries (new ERP months only unless told otherwise).
"""

import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_s = importlib.util.spec_from_file_location("dash", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)
_s = importlib.util.spec_from_file_location("fp", HERE / "42_flag_detail_pdfs.py")
fp = importlib.util.module_from_spec(_s); _s.loader.exec_module(fp)
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer  # noqa: E402

SWITCH = "2023-10"
FLAG = 50000.0

STORE_SQL = {
    "old": """SELECT TO_CHAR(m.dd,'YYYY-MM'), m.dt, SUM(d.qty * NVL(d.avg_rate, d.rate))
              FROM dostt.dmn m JOIN dostt.ddt d ON d.dno = m.dno AND d.dd = m.dd AND d.dt = m.dt AND d.uc = m.uc
              WHERE m.dt IN ('ISS','RET') AND m.dd < DATE '2023-10-01' GROUP BY TO_CHAR(m.dd,'YYYY-MM'), m.dt""",
    "new": """SELECT TO_CHAR(m.dd,'YYYY-MM'), m.dt, SUM(d.qty * NVL(d.avg_rate, d.rate))
              FROM dostex.dmn m JOIN dostex.ddt d ON d.trans_no = m.trans_no AND d.uc = m.uc
              WHERE m.dt IN ('ISS','RET') AND m.dd >= DATE '2023-10-01' GROUP BY TO_CHAR(m.dd,'YYYY-MM'), m.dt""",
}
JV_SQL = {
    "old": """SELECT TO_CHAR(vd,'YYYY-MM'), LISTAGG(vno, ',') WITHIN GROUP (ORDER BY vno), MAX(naration), -SUM(amt)
              FROM dostt.acl WHERE ac LIKE '60110%' AND vt = 'JV' AND vd < DATE '2023-10-01' GROUP BY TO_CHAR(vd,'YYYY-MM')""",
    "new": """SELECT TO_CHAR(vd,'YYYY-MM'), LISTAGG(vno, ',') WITHIN GROUP (ORDER BY vno), MAX(naration), -SUM(amt)
              FROM dostex.acl WHERE ac LIKE '300101%' AND vt = 'JV' GROUP BY TO_CHAR(vd,'YYYY-MM')""",
}
DOCS_SQL = {
    "old": """SELECT m.dt, m.dno, m.dd, COUNT(*), SUM(d.qty * NVL(d.avg_rate, d.rate))
              FROM dostt.dmn m JOIN dostt.ddt d ON d.dno = m.dno AND d.dd = m.dd AND d.dt = m.dt AND d.uc = m.uc
              WHERE m.dt IN ('ISS','RET') AND TO_CHAR(m.dd,'YYYY-MM') = :mo GROUP BY m.dt, m.dno, m.dd ORDER BY m.dd, m.dt, m.dno""",
    "new": """SELECT m.dt, m.dno, m.dd, COUNT(*), SUM(d.qty * NVL(d.avg_rate, d.rate))
              FROM dostex.dmn m JOIN dostex.ddt d ON d.trans_no = m.trans_no AND d.uc = m.uc
              WHERE m.dt IN ('ISS','RET') AND TO_CHAR(m.dd,'YYYY-MM') = :mo GROUP BY m.dt, m.dno, m.dd ORDER BY m.dd, m.dt, m.dno""",
}


def monthly(cur, rows):
    iss, ret, jv = defaultdict(float), defaultdict(float), {}
    for era in ("old", "new"):
        for mo, dt, v in rows(cur, STORE_SQL[era]):
            (iss if dt == "ISS" else ret)[mo] += float(v or 0)
        for mo, vno, nar, v in rows(cur, JV_SQL[era]):
            jv[mo] = (vno, nar, float(v or 0))
    months = sorted(set(iss) | set(ret) | set(jv))
    return months, iss, ret, jv


def reasons(months, iss, ret, jv, last_month):
    """Likely reason for each flagged month."""
    diff = {m: jv.get(m, (0, 0, 0.0))[2] - (iss[m] - ret[m]) for m in months}
    out = {}
    for i, m in enumerate(months):
        d = diff[m]
        if abs(d) < FLAG:
            continue
        net, j = iss[m] - ret[m], jv.get(m, (0, 0, 0.0))[2]
        nb = [months[k] for k in (i - 1, i + 1) if 0 <= k < len(months)]
        offset = next((n for n in nb if abs(diff[n]) >= FLAG / 2 and abs(d + diff[n]) <= 0.25 * abs(d)), None)
        first_slip = min(k for k in months if iss[k] or ret[k])
        if m < first_slip:
            r = "Journal posted before the store module started - no store slips to compare"
        elif m not in jv:
            r = "Journal not posted" + (" yet (month still open)" if m >= last_month else "")
        elif net <= 0 < j:
            r = "Returns were bigger than issues, but the journal still charged consumption"
        elif offset:
            r = f"Timing: journal amount seems shifted to/from {offset} (the two months offset each other)"
        elif j > 0 and net > 0 and (j / net < 0.3 or j / net > 3):
            r = "Journal amount far from store slips - possible typing mistake"
        elif d > 0:
            r = "Journal charged more than the store issued"
        else:
            r = "Journal charged less than the store issued"
        if m in ("2023-09", "2023-10"):
            r += " (old-to-new ERP switch-over month)"
        out[m] = (d, r)
    return diff, out


def main():
    cur = dash.connect(dash.load_config()).cursor()
    months, iss, ret, jv = monthly(cur, dash.rows)
    last = max(months)
    diff, flags = reasons(months, iss, ret, jv, last)
    over = sum(d for d, _ in flags.values() if d > 0)
    under = -sum(d for d, _ in flags.values() if d < 0)

    story = [Paragraph("Stores &amp; spares check - every month since the store module started", fp.H1),
             Paragraph("Each month the accounts team posts a journal (\"BILL M/O ... (STORE)\") that takes the value of "
                       "goods issued out of Stores &amp; spares and charges it to Stores &amp; spares consumed. This compares "
                       "that journal with what the store itself recorded (issue slips minus return slips, at the store's "
                       f"average rate), for every month from {months[0]} (when the old ERP's store module started) to {last}. "
                       "Old ERP up to Sep-2023, new ERP after. A positive difference means the journal charged more than "
                       f"the store issued; negative means less. A month is flagged when the gap is Rs {FLAG:,.0f} or more.",
                       fp.BODY),
             Paragraph("Summary", fp.H2),
             Paragraph(f"<b>{len(flags)}</b> of {len(months)} months are flagged. In flagged months the journal charged "
                       f"<b>Rs {over:,.0f}</b> too much in total and <b>Rs {under:,.0f}</b> too little. Solar panels and "
                       "inverters issued in Jul-2024 and Jan-2026 were correctly moved to fixed assets and do not cause a "
                       "gap. Timing flags (a journal posted in the neighbouring month) cancel out and matter little; the "
                       "others change stock and profit.", fp.BODY)]

    story.append(Paragraph("Flagged months", fp.H2))
    rows = [["Month", "ERP", "Store net issued", "Journal", "Difference", "Likely reason"]]
    for m in sorted(flags):
        d, r = flags[m]
        rows.append([m, "old" if m < SWITCH else "new", fp.n(iss[m] - ret[m]), fp.n(jv.get(m, (0, 0, 0.0))[2]), fp.n(d), r])
    story.append(fp.table(rows, [18 * mm, 12 * mm, 30 * mm, 28 * mm, 26 * mm, 150 * mm], num_cols=(2, 3, 4)))

    story.append(PageBreak())
    story.append(Paragraph("By fiscal year (Jul-Jun)", fp.H2))
    fy = defaultdict(lambda: [0.0, 0.0, 0])
    for m in months:
        y = int(m[:4]) + (1 if int(m[5:]) >= 7 else 0)
        fy[y][0] += iss[m] - ret[m]
        fy[y][1] += jv.get(m, (0, 0, 0.0))[2]
        fy[y][2] += 1 if m in flags else 0
    rows = [["FY", "Store issued (net of returns)", "Accounts journal", "Difference", "Running difference", "Flagged months"]]
    run = 0.0
    for y in sorted(fy):
        a, b, k = fy[y]
        run += b - a
        rows.append([f"FY{y % 100:02d}", fp.n(a), fp.n(b), fp.n(b - a), fp.n(run, False), str(k)])
    story.append(fp.table(rows, [18 * mm, 50 * mm, 42 * mm, 34 * mm, 38 * mm, 28 * mm], num_cols=(1, 2, 3, 4, 5)))
    story.append(Spacer(1, 4))
    story.append(Paragraph("Running difference: how far the ledger's Stores &amp; spares balance has drifted from the store "
                           "records (positive = ledger stock lower than store records).", fp.SMALL))

    story.append(PageBreak())
    story.append(Paragraph("Every month", fp.H2))
    rows = [["Month", "Issued", "Returned", "Net issued", "Journal", "JV #", "Difference", "Running", "Flag"]]
    red, run = [], 0.0
    for m in months:
        net = iss[m] - ret[m]
        vno, nar, j = jv.get(m, ("", "", 0.0))
        run += diff[m]
        rows.append([m, fp.n(iss[m]), fp.n(ret[m]), fp.n(net), fp.n(j), str(vno) if vno else "not posted",
                     fp.n(diff[m]), fp.n(run, False), "FLAG" if m in flags else ""])
        if m in flags:
            red.append(len(rows) - 1)
    story.append(fp.table(rows, [18 * mm, 28 * mm, 24 * mm, 28 * mm, 28 * mm, 22 * mm, 26 * mm, 28 * mm, 14 * mm],
                          num_cols=(1, 2, 3, 4, 6, 7), red_rows=red))

    # Slip detail for the biggest non-timing problems
    worst = sorted((m for m in flags if not flags[m][1].startswith(("Timing", "Journal not posted yet"))),
                   key=lambda m: -abs(flags[m][0]))[:6]
    for m in sorted(worst):
        era = "old" if m < SWITCH else "new"
        rows = [["Type", "Slip #", "Date", "Lines", "Value"]]
        for dt, dno, dd, cnt, v in dash.rows(cur, DOCS_SQL[era], mo=m):
            rows.append(["Issue" if dt == "ISS" else "Return", str(dno), fp.d(dd), str(cnt), fp.n(float(v or 0))])
        story.append(PageBreak())
        story.append(KeepTogether([Paragraph(f"{m} - store slips ({flags[m][1].lower()}; difference Rs {flags[m][0]:,.0f})", fp.H2),
                                   fp.table(rows, [22 * mm, 22 * mm, 28 * mm, 18 * mm, 32 * mm], num_cols=(3, 4))]))
    fp.OUT.mkdir(exist_ok=True)
    fp.build("stores_and_spares_reconciliation.pdf", story)
    print(f"built: {len(months)} months, {len(flags)} flagged, over {over:,.0f}, under {under:,.0f}")
    for m in sorted(flags):
        print(m, round(flags[m][0]), flags[m][1])


def load_store_gap(cur, rows, from_month=SWITCH):
    """{month: journal consumption - store net issues}, from from_month onward."""
    months, iss, ret, jv = monthly(cur, rows)
    return {m: jv.get(m, (0, 0, 0.0))[2] - (iss[m] - ret[m]) for m in months if m >= from_month}


if __name__ == "__main__":
    main()
