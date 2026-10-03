"""
64_weekly_beam_report.py - weekly (Friday) beam report PDF, saved to the Drive reports folder, with an ntfy push carrying
the link (owner's choice over email, 2026-09-29). Reads yarn_fabric_data.json - run 52_yarn_fabric_data.py first
(run_weekly_beam_report.ps1 does both). ERP read only.
Contents: where all beams are (by location, with numbers) · looms ending soon with no filled backup of the same ends
(VA within 7 days, others 15) · every beam on a loom with set, sizer, mounted, last production checked, fabric still to come
and days to finish by ERP and by scoreboard · filled beams waiting · empty beams on the floor · beams and yarn at sizers.
python 64_weekly_beam_report.py [--no-push]
"""
import configparser
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

HERE = Path(__file__).resolve().parent
REPORTS_FOLDER_ID = "18CYkYptYXvrrq_szDHt4lbtsfyK4j3W5"      # Drive: DostTextile_Reports (the synced output folder)
DAYS, DAYS_VA = 15, 7
NAVY = colors.HexColor("#1F3B57"); GRID = colors.HexColor("#C9CED6"); WARN = colors.HexColor("#FFE3B3"); CRIT = colors.HexColor("#F8C9C9")
SS = getSampleStyleSheet()
SMALL = ParagraphStyle("s", parent=SS["Normal"], fontSize=7.5, leading=9)
NORM = ParagraphStyle("n", parent=SS["Normal"], fontSize=9, leading=11)
H2 = ParagraphStyle("h2", parent=SS["Heading2"], fontSize=12, textColor=NAVY, spaceBefore=8, spaceAfter=4)


def short(s):
    return (s or "").replace("SIZING INDUSTRY", "").replace("SIZING INDUSTRIES", "").replace("WARPING CENTRE", "").replace("LOOM # ", "L").strip()


def tbl(head, rows, widths, fills=None):
    data = [[Paragraph(f"<b>{h}</b>", SMALL) for h in head]] + [[Paragraph(str(c), SMALL) for c in r] for r in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    st = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF2")), ("GRID", (0, 0), (-1, -1), 0.3, GRID),
          ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
    for i, c in (fills or {}).items():
        st.append(("BACKGROUND", (0, i + 1), (-1, i + 1), c))
    t.setStyle(TableStyle(st))
    return t


def main(push=True):
    cfg = configparser.ConfigParser(); cfg.read(HERE / "report_config.ini")
    out = Path(cfg["output"]["folder"])
    d = json.loads((out / "yarn_fabric_data.json").read_text(encoding="utf-8"))
    today = date.fromisoformat(d["today"][:10])
    ends = d.get("set_ends", {})
    by = defaultdict(list)
    for b in d["beams"]:
        by[b[2]].append(b)
    fd = lambda s: datetime.fromisoformat(str(s)[:10]).strftime("%d-%b") if s else "-"  # noqa: E731

    # beams on looms: days to finish (same as the dashboard): (to come - rate x days since last check) / rate
    looms = []
    for b in by["On loom"]:
        since = (today - date.fromisoformat(str(b[10] or b[4])[:10])).days
        res = []
        for rate in (b[11], b[12]):
            if rate:
                dd = max(0.0, (b[13] or 0) - rate * since) / rate
                res.append((rate, dd, today + timedelta(days=dd)))
            else:
                res.append((None, None, None))
        looms.append(dict(b=b, since=since, erp=res[0], scb=res[1], ends=ends.get(str(b[8])),
                          days=min(x[1] for x in res if x[1] is not None) if any(x[1] is not None for x in res) else 999))
    looms.sort(key=lambda x: int("".join(c for c in x["b"][3] if c.isdigit()) or 0))
    # backups: filled beams waiting, same ends (within 5), soonest-finishing looms first
    spare = [dict(b=b, ends=ends.get(str(b[8])), used=None) for b in by["Sized, waiting for loom"]]
    nobk = []
    for L in sorted(looms, key=lambda x: x["days"]):
        va = "VA" in L["b"][5].split(" · ")[0].upper()
        if L["days"] > (DAYS_VA if va else DAYS):
            continue
        s = next((s for s in spare if not s["used"] and s["ends"] and L["ends"] and abs(s["ends"] - L["ends"]) <= 5), None)
        if s:
            s["used"] = L["b"][3]; L["backup"] = s["b"][1]
        else:
            nobk.append(L)

    folder = out / "BEAM REPORTS"                            # owner: separate Drive folder, files named by date
    folder.mkdir(exist_ok=True)
    f = folder / f"{today.isoformat()}.pdf"
    doc = SimpleDocTemplate(str(f), pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1 * cm, bottomMargin=1 * cm,
                            title=f"Beam report {today:%d-%b-%Y}")
    E = [Paragraph(f"Dost Textiles - Beam report - {today:%A %d-%b-%Y}", SS["Title"]),
         Paragraph(f"As tracked by the ERP (data {str(d.get('generated_at', ''))[:16]}). Beams with no movement in the last 90 days are not counted "
                   f"({len(d.get('beams_ignored', []))} old numbers). Scoreboard data to {d.get('scb_last') or '-'}.", NORM), Spacer(1, 6)]
    # 1. where all beams are
    E.append(Paragraph(f"Where all my beams are - {len(d['beams'])} beams", H2))
    labels = [("On loom", "On looms"), ("Sized, waiting for loom", "Filled, waiting on the floor"), ("Empty in mill", "Empty on the floor"),
              ("At sizer (empty)", "Empty at sizers"), ("Sent out filled", "Filled beams sent out")]
    rows = []
    for k, lab in labels:
        L = by.get(k, [])
        if k == "On loom":
            nums = ", ".join(f"{x['b'][1][:5]}{'*' if len(x['b'][1]) > 5 else ''}&rarr;{short(x['b'][3])}" for x in looms)
        elif k == "At sizer (empty)":
            g = defaultdict(list)
            for b in L:
                g[short(b[3])].append(b)
            nums = "<br/>".join(f"<b>{k2}</b>: " + " ".join(b[1] for b in v) + f" (sent {fd(min(b[4] for b in v))})" for k2, v in g.items())
        elif k == "Sized, waiting for loom":
            yarn = lambda s_: ((s_ or "").split(" · ")[1] if " · " in (s_ or "") else "").replace("CTN", "cotton").strip()  # noqa: E731
            nums = ", ".join(f"{b[1][:5]}{'*' if len(b[1]) > 5 else ''} (<b>{yarn(b[3])}</b> · contract {''.join(c for c in (b[3].split('contract')[-1] if 'contract' in b[3] else '') if c.isdigit())}, "
                             f"{ends.get(str(b[8]), '?')} ends, {b[6] or 0:,} m)" for b in L)
        else:
            nums = " ".join(b[1] for b in L)
        rows.append([lab, len(L), nums or "-"])
    w = defaultdict(int)
    for b in d["beams"]:
        if b[7]:
            w[int(b[7])] += 1
    rows.append(["<b>TOTAL</b>", f"<b>{len(d['beams'])}</b>", " · ".join(f"{n} x {k}\"" for k, n in sorted(w.items(), reverse=True)) + " · * = number painted on two beams"])
    E.append(tbl(["Where", "Beams", "Beam numbers"], rows, [4.5 * cm, 1.6 * cm, 20.5 * cm]))
    # 2. alert list
    E.append(Paragraph(f"Looms ending soon with NO filled backup beam of the same ends (VA within {DAYS_VA} days, others within {DAYS})", H2))
    if nobk:
        rows, fills = [], {}
        for i, L in enumerate(nobk):
            b = L["b"]
            rows.append([b[3], b[1], b[5].split(" · ")[0], L["ends"] or "?", f"{L['days']:.0f}", fd(L["erp"][2]) if L["erp"][1] is not None else "-",
                         fd(L["scb"][2]) if L["scb"][1] is not None else "-"])
            if L["days"] <= 5:
                fills[i] = CRIT
        E.append(tbl(["Loom", "Beam", "Quality", "Ends", "Days left", "Finish (ERP)", "Finish (scoreboard)"], rows,
                     [2.6 * cm, 2.4 * cm, 8.5 * cm, 1.8 * cm, 1.8 * cm, 2.8 * cm, 3 * cm], fills))
    else:
        E.append(Paragraph("None - every loom ending soon has a filled beam of the same ends waiting.", NORM))
    covered = [L for L in looms if L.get("backup")]
    if covered:
        E.append(Paragraph("Covered: " + "; ".join(f"{short(L['b'][3])} ends in {L['days']:.0f} d &rarr; backup {L['backup']}" for L in covered), SMALL))
    # 3. beams on looms
    E.append(PageBreak())
    E.append(Paragraph("Beams on looms", H2))
    E.append(Paragraph("Fabric still to come = total set length less shrinkage (from that quality's finished beams) less fabric woven. "
                       "Days to finish = (fabric to come - daily rate x days since the last production check) / daily rate; "
                       "ERP rate = last 30 days of production entries, scoreboard rate = last 14 days.", SMALL))
    rows, fills = [], {}
    for i, L in enumerate(looms):
        b = L["b"]
        rows.append([short(b[3]), b[1], b[5].split(" · ")[0] + (" (shared no.)" if "same number" in b[5] else ""), b[8], short(b[9]), fd(b[4]),
                     f"{fd(b[10])} ({L['since']} d)" if b[10] else "none yet", f"{b[13] or 0:,}", f"{(b[14] or 0) * 100:.1f}%",
                     f"{L['erp'][0]:.0f}" if L["erp"][0] else "-", f"{L['erp'][1]:.1f} &rarr; {fd(L['erp'][2])}" if L["erp"][1] is not None else "-",
                     f"{L['scb'][0]:.0f}" if L["scb"][0] else "-", f"{L['scb'][1]:.1f} &rarr; {fd(L['scb'][2])}" if L["scb"][1] is not None else "-"])
        if L in nobk:
            fills[i] = CRIT if L["days"] <= 5 else WARN
    E.append(tbl(["Loom", "Beam", "Contract / quality", "Set", "Sizer", "Mounted", "Last checked", "To come (m)", "Shrink", "ERP m/day",
                  "Days (ERP)", "Scb m/day", "Days (scoreboard)"], rows,
                 [1.2 * cm, 2.1 * cm, 5.0 * cm, 1.5 * cm, 2.4 * cm, 1.8 * cm, 2.2 * cm, 1.7 * cm, 1.3 * cm, 1.4 * cm, 2.4 * cm, 1.4 * cm, 2.4 * cm], fills))
    E.append(Paragraph("Orange = ending within the alert window with no filled backup beam; red = within 5 days.", SMALL))
    # 4. sizers
    E.append(Paragraph("At sizers", H2))
    rows = []
    g = defaultdict(list)
    for b in by.get("At sizer (empty)", []):
        g[b[3]].append(b)
    for k, v in g.items():
        rows.append([k, "empty beams", len(v), " ".join(b[1] for b in v), fd(min(b[4] for b in v)), f"{(today - date.fromisoformat(str(min(b[4] for b in v))[:10])).days} d"])
    cno = {str(c["id"]): c for c in d["contracts"]}
    for s in d.get("sizers", []):
        if s[4] >= 5 * 45.36 and s[5] and (today - date.fromisoformat(str(s[5])[:10])).days <= 60:
            c = cno.get(str(s[1]), {})
            rows.append([s[0], "yarn", f"{s[4] / 45.36:.0f} bags ({s[4]:,.0f} kg)", f"contract {c.get('no')} · {c.get('quality', '')}", fd(s[5]),
                         f"{(today - date.fromisoformat(str(s[5])[:10])).days} d"])
    E.append(tbl(["Sizer", "What", "Quantity", "Beams / contract", "Sent", "Since"], rows or [["-", "nothing at sizers", "", "", "", ""]],
                 [5 * cm, 2.2 * cm, 3.5 * cm, 10.5 * cm, 2 * cm, 1.6 * cm]))
    # owner: a sizer is fine when each yarn COUNT balances across its contracts; flag every sizer + count off by 1 bag or more
    E.append(Paragraph("Sizer yarn balances to check against your ledger (by yarn count, 1 bag or more either way)", H2))
    yn = d.get("yarn_names", {})
    grp = defaultdict(list)
    for s in d.get("sizers", []):
        grp[(s[0], s[8] if len(s) > 8 else None)].append(s)
    rows, fills = [], {}
    for (sz, yc), L in sorted(grp.items(), key=lambda kv: -abs(sum(x[4] for x in kv[1]))):
        net = sum(x[4] for x in L)
        if abs(net) < 45.36:
            continue
        open_ = sum(x[4] for x in L if x[4] >= 5 * 45.36 and x[5] and (today - date.fromisoformat(str(x[5])[:10])).days <= 60)
        rest = net - open_
        why = ("lot at the sizer now" if abs(rest) < 45.36 else f"CHECK: {rest / 45.36:.1f} bags sent, not back" if rest > 0
               else f"{-rest / 45.36:.1f} bags more back than sent - send not entered, or sent in the old ERP")
        conts = ", ".join(f"{cno.get(str(x[1]), {}).get('no')} ({x[4] / 45.36:+.1f})" for x in sorted(L, key=lambda x: -abs(x[4])) if abs(x[4]) >= 4.5)
        rows.append([sz, yn.get(str(yc), str(yc)), f"{net / 45.36:.1f}", why, conts])
        if why.startswith("CHECK"):
            fills[len(rows) - 1] = CRIT
    E.append(tbl(["Sizer", "Yarn count", "Net bags", "What it looks like", "Contracts (bags)"], rows or [["-", "", "", "every count balances", ""]],
                 [5 * cm, 3 * cm, 1.8 * cm, 8 * cm, 8.8 * cm], fills))
    doc.build(E)
    print("saved", f)
    if not push:
        return
    link = f"https://drive.google.com/drive/folders/{REPORTS_FOLDER_ID}"
    folder_id = None
    try:          # the synced folder uploads within a few minutes; find the file's own Drive link
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        creds = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"),
                                                                      scopes=["https://www.googleapis.com/auth/drive.readonly"])
        drv = build("drive", "v3", credentials=creds)
        for _ in range(20):
            if not folder_id:
                r = drv.files().list(q=f"name = 'BEAM REPORTS' and '{REPORTS_FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                                     fields="files(id)").execute()
                if r.get("files"):
                    folder_id = r["files"][0]["id"]; link = f"https://drive.google.com/drive/folders/{folder_id}"
            if folder_id:
                r = drv.files().list(q=f"name = '{f.name}' and '{folder_id}' in parents and trashed = false", fields="files(id)").execute()
                if r.get("files"):
                    link = f"https://drive.google.com/file/d/{r['files'][0]['id']}/view"; break
            time.sleep(30)
    except Exception as e:
        print("link lookup failed:", e)
    body = (f"{len(d['beams'])} beams: {len(by['On loom'])} on looms, {len(by['Sized, waiting for loom'])} filled waiting, "
            f"{len(by['Empty in mill'])} empty on floor, {len(by['At sizer (empty)'])} at sizers.\n"
            + (f"{len(nobk)} loom(s) ending soon with no backup beam: " + ", ".join(f"{short(L['b'][3])} ({L['days']:.0f} d)" for L in nobk) if nobk
               else "Every loom ending soon has a backup beam.") + "\nTap to open the PDF.")
    req = urllib.request.Request(url=f"https://ntfy.sh/{cfg.get('ntfy', 'topic')}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": f"Weekly beam report - {today:%d-%b-%Y}", "Click": link, "Tags": "page_facing_up",
                                          "Actions": f"view, Open PDF, {link}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        print("pushed", r.status, link)


if __name__ == "__main__":
    main(push="--no-push" not in sys.argv)
