"""
71_photo_agent.py - AI photo check: an AI agent (Claude Code, run headless) reads every photo attached in the gate app for a day -
gate passes, supplier bills, vehicles, drivers / CNICs, store items, attendance sheets - and checks what it SEES against the gate log
row and the ERP document for the same entry. Discrepancies are pushed (ntfy) and listed in a PDF in Drive "ERP AUDIT\\PHOTO CHECKS".
ERP read only. Built 30-Sep-2026; the scheduled task stays DISABLED until the owner makes photos compulsory.

python 71_photo_agent.py [--date 2026-09-28] [--no-push] [--keep]
Steps: 1) collect the day's gate rows with a photo, download the photos, attach the gate row + the matching ERP document -> manifest.json
       2) run the agent: claude -p <instructions> (tools: Read, Write only, in the day's work folder) -> findings.json
       3) push discrepancies, keep a driver / vehicle register (drivers.json) to spot new drivers or CNICs used under two names
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
_a = importlib.util.spec_from_file_location("a70", HERE / "70_erp_audit.py"); a70 = importlib.util.module_from_spec(_a); _a.loader.exec_module(a70)
CLAUDE = r"C:\Users\DBSERVER\.local\bin\claude.exe"
WORK = HERE / "photo_agent"
DRIVERS = HERE / "photo_agent_drivers.json"
TABS = ("Inward Log", "Outward Log", "Attendance Log", "Yarn Stock Log", "Beam Stock Log", "Guest Log", "Meter Solar Log")

AGENT_BRIEF = """You are the photo auditor for Dost Textiles (a weaving mill). Work only inside this folder.
Read manifest.json. Each entry is one photo taken at the mill gate / store, with:
  - "photo": the image file in this folder (open it with the Read tool and LOOK at it carefully);
  - "tab": which gate-app log it belongs to; "gate": what the gate clerk typed; "erp": what the ERP holds for the same document (may be empty).
For every entry:
  1. Say what the photo actually is: gate pass / supplier bill / delivery challan / vehicle / driver or CNIC / store items / attendance sheet /
     meter reading / yarn stock / beams / other / unreadable.
  2. Read every useful value you can see: document number and date, party, vehicle registration number, driver name, CNIC number,
     items with quantities, meters, pieces/thaan, bags and cones, beam numbers, amounts, signatures present or not; for an attendance
     sheet, each worker's name and in/out times.
  3. Compare with "gate" and "erp". Report ONLY real differences you can see, e.g. photo says 105 bags but ERP 134; vehicle plate differs;
     item on the bill not in the GRN; rate or amount differs; a worker present on the sheet but absent in the log (or the reverse);
     times differ by more than 15 minutes; photo date differs from the entry date; document looks altered, cut, or re-used from an earlier day.
     Do not guess. If a value cannot be read, say so - never invent it.
Write findings.json: a JSON list, one object per entry, with keys:
  "photo", "what" (what the photo is), "status" ("MATCH" | "MISMATCH" | "UNREADABLE" | "NO_ERP_DOC" | "NOT_RELEVANT"),
  "read" (object of the values you read, include "vehicle", "driver", "cnic" when visible),
  "differences" (list of {"field", "photo", "gate", "erp", "note"}), "confidence" ("high" | "medium" | "low").
Keep notes short and factual. Urdu / handwritten text: transcribe what you can. Write findings.json before you finish."""


def erp_for(cur, tab, row):
    """the ERP document(s) behind a gate row (gate register # = ERP gate-entry number)"""
    q = lambda s, **b: [dict(zip([c[0].lower() for c in cur.description], r)) for r in cur.execute(s, **b).fetchall()]  # noqa: E731
    reg = a70.regno(row.get("Register #"))
    out = []
    if tab == "Inward Log" and reg:
        out += q("""SELECT 'GRN ' || r.dno doc, TRUNC(r.dd) doc_date, r.an party, r.bno bill_no, r.idesc item, r.qty, r.rate, r.total_amt amount
                    FROM dostex.store_receipt_v r JOIN dostex.dmn m ON m.trans_no = r.trans_no WHERE m.ge_no = :g""", g=reg)
        out += q("""SELECT m.tt || ' ' || m.tno doc, TRUNC(m.td) doc_date, a.an party, m.vehicle, m.driver, y.yarn_desc yarn, d.bags, d.cones, d.yarn_wt kg, c.cont_no contract
                    FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no LEFT JOIN dostex.yarn y ON y.ycode = d.ycode LEFT JOIN dostex.contm c ON c.cont_id = d.cont_id
                    LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2) WHERE m.ge_no = :g""", g=reg)
        out += q("""SELECT trans_type || ' ' || NVL(TO_CHAR(inv_no), TO_CHAR(gp_no)) doc, TRUNC(trans_date) doc_date, party, deliver_to, vehicle, driver, than, total_mtr meters
                    FROM dostex.fab_gp_v WHERE ge_no = :g AND trans_type <> 'GPOUT'""", g=reg)
    if tab == "Outward Log" and reg:
        out += q("""SELECT 'OGP ' || inv_no doc, TRUNC(trans_date) doc_date, party, deliver_to, vehicle, driver, qcode, than, total_mtr meters
                    FROM dostex.fab_gp_v WHERE ge_no = :g AND trans_type = 'GPOUT'""", g=reg)
        out += q("""SELECT m.tt || ' GP ' || m.gp_no doc, TRUNC(m.td) doc_date, a.an party, m.vehicle, m.driver, d.beam_code beam
                    FROM dostex.wv_gp_beamsm m JOIN dostex.wv_gp_beamsd d ON d.trans_no = m.trans_no LEFT JOIN dostex.ach a ON a.ac = m.ac1 WHERE m.ge_no = :g""", g=reg)
    for r in out:
        for k, v in list(r.items()):
            if isinstance(v, datetime):
                r[k] = f"{v:%Y-%m-%d}"
            elif isinstance(v, float) and v.is_integer():
                r[k] = int(v)
    return out


def attendance_erp(cur, d, names, emp):
    codes = {emp.get(n.upper()): n for n in names if emp.get(n.upper())}
    if not codes:
        return []
    rows = cur.execute(f"""SELECT emp_code, attendance_type FROM payroll.in_out WHERE TRUNC(dated) = DATE '{d:%Y-%m-%d}'
                           AND emp_code IN ({",".join(str(int(c)) for c in codes if str(c).isdigit()) or "0"})""").fetchall()
    return [{"name": codes.get(str(c)), "code": c, "erp_status": t} for c, t in rows]


def cell(v):
    """a gate-sheet value as text: times stay times (the sheet export gives 08:05 as 1899-12-30 08:05 or a time object)"""
    from datetime import time as dtime
    if isinstance(v, dtime):
        return f"{v:%H:%M}"
    if isinstance(v, datetime):
        return f"{v:%H:%M}" if v.year < 1901 else (f"{v:%Y-%m-%d}" if v.hour == v.minute == 0 else f"{v:%Y-%m-%d %H:%M}")
    if isinstance(v, date):
        return f"{v:%Y-%m-%d}"
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def clean(r):
    return {k: cell(v) for k, v in r.items() if k not in ("Media", "Timestamp") and v not in (None, "")}


def download(url, path):
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})|id=([A-Za-z0-9_-]{20,})", url)
    if not m:
        return None
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    drv = build("drive", "v3", credentials=service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"),
                                                                                              scopes=["https://www.googleapis.com/auth/drive.readonly"]))
    fid = m.group(1) or m.group(2)
    meta = drv.files().get(fileId=fid, fields="name,mimeType").execute()
    ext = {"image/jpeg": ".jpg", "image/png": ".png", "application/pdf": ".pdf"}.get(meta["mimeType"], Path(meta["name"]).suffix or ".jpg")
    p = path.with_suffix(ext)
    p.write_bytes(drv.files().get_media(fileId=fid).execute())
    return p


def build_manifest(d, folder):
    tabs = a70.read_gate()
    if tabs is None:
        raise SystemExit("gate sheet not readable")
    cur = a70.dash.connect(a70.dash.load_config()).cursor()
    emp = {str(r.get("Name") or "").strip().upper(): str(r.get("Code") or "").split(".")[0] for r in tabs.get("Employees", [])}
    groups = defaultdict(list)                       # one photo can cover many rows (an attendance sheet, a multi-line gate pass)
    for t in TABS:
        for r in tabs.get(t, []):
            u = str(r.get("Media") or "").strip()
            if r.get("Date") == d and u.startswith("http"):
                for link in re.split(r"[\s,]+", u):
                    if link.startswith("http"):
                        groups[(t, link)].append(r)
    man = []
    for i, ((t, link), rows) in enumerate(sorted(groups.items(), key=lambda x: x[0]), 1):
        try:
            p = download(link, folder / f"photo_{i:03d}")
        except Exception as e:
            man.append({"photo": None, "link": link, "tab": t, "error": f"could not download: {str(e)[:80]}"}); continue
        e = {"photo": p.name if p else None, "link": link, "tab": t, "gate": [clean(r) for r in rows]}
        if t in ("Inward Log", "Outward Log"):
            e["erp"] = erp_for(cur, t, rows[0])
        elif t == "Attendance Log":
            e["erp"] = attendance_erp(cur, d, [str(r.get("Name") or "").strip() for r in rows], emp)
            e["erp_note"] = "ERP attendance (payroll IN_OUT) is keyed in half-month batches; an empty list means not keyed yet, not absent"
        man.append(e)
    (folder / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return man


def run_agent(folder):
    r = subprocess.run([CLAUDE, "-p", AGENT_BRIEF, "--allowedTools", "Read", "Write", "--output-format", "text"], cwd=str(folder),
                       capture_output=True, text=True, encoding="utf-8", timeout=3600)
    (folder / "agent_log.txt").write_text((r.stdout or "") + "\n--- stderr ---\n" + (r.stderr or ""), encoding="utf-8")
    f = folder / "findings.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def driver_register(findings, man, d):
    """vehicle -> drivers / CNICs seen; flag a new driver on a known vehicle, or one CNIC under two names"""
    reg = json.loads(DRIVERS.read_text(encoding="utf-8")) if DRIVERS.exists() else {"vehicles": {}, "cnic": {}}
    notes = []
    for f in findings or []:
        rd = f.get("read") or {}
        veh = re.sub(r"[^A-Z0-9]", "", str(rd.get("vehicle") or "").upper()); cnic = re.sub(r"\D", "", str(rd.get("cnic") or "")); nm = str(rd.get("driver") or "").strip().upper()
        if cnic and nm:
            old = reg["cnic"].get(cnic)
            if old and old != nm:
                notes.append(f"CNIC {cnic} was '{old}' before, now '{nm}' ({f.get('photo')})")
            reg["cnic"].setdefault(cnic, nm)
        if veh and (cnic or nm):
            v = reg["vehicles"].setdefault(veh, {"drivers": [], "first": f"{d}"})
            who = cnic or nm
            if v["drivers"] and who not in v["drivers"]:
                notes.append(f"vehicle {veh}: new driver {nm or ''} {cnic or ''} (seen before: {', '.join(v['drivers'][:3])})")
            if who not in v["drivers"]:
                v["drivers"].append(who)
    DRIVERS.write_text(json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")
    return notes


def report(d, man, findings, notes, push):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    ss = getSampleStyleSheet(); sm = ParagraphStyle("sm", parent=ss["BodyText"], fontSize=8, leading=10)
    out = a70.REPORTS / "ERP AUDIT" / "PHOTO CHECKS"; out.mkdir(parents=True, exist_ok=True)
    name = f"{d:%Y-%m-%d} photo check.pdf"
    tabmap = {m.get("photo"): m.get("tab") for m in man}
    # attendance is keyed in the ERP in half-month batches, so 'no ERP rows yet' is not a discrepancy for attendance sheets
    bad = [f for f in findings if f.get("status") == "MISMATCH" or (f.get("status") == "NO_ERP_DOC" and tabmap.get(f.get("photo")) != "Attendance Log")]
    el = [Paragraph(f"Dost Textiles - AI photo check - {d:%a %d-%b-%Y}", ss["Title"]),
          Paragraph(f"{len(man)} photos read by the AI agent and compared with the gate log and the ERP. "
                    f"{sum(1 for f in findings if f.get('status') == 'MATCH')} match, {len(bad)} with differences, "
                    f"{sum(1 for f in findings if f.get('status') == 'UNREADABLE')} unreadable. ERP read only.", sm), Spacer(1, 6)]
    rows = [["Photo", "Log", "What it is", "Result", "Differences / notes"]]
    tabmap = {m.get("photo"): m.get("tab") for m in man}
    for f in findings:
        diffs = "; ".join(f"{x.get('field')}: photo {x.get('photo')} / gate {x.get('gate')} / ERP {x.get('erp')} {x.get('note') or ''}" for x in f.get("differences") or [])
        rows.append([f.get("photo"), tabmap.get(f.get("photo"), ""), Paragraph(str(f.get("what") or ""), sm), f"{f.get('status')} ({f.get('confidence', '')})", Paragraph(diffs or "-", sm)])
    t = Table(rows, colWidths=[2.6 * cm, 2.8 * cm, 5 * cm, 3.2 * cm, 13.6 * cm], repeatRows=1)
    sty = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3B57")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8),
           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9AA5B1"))]
    for i, f in enumerate(findings, 1):
        if f.get("status") in ("MISMATCH", "NO_ERP_DOC"):
            sty.append(("BACKGROUND", (3, i), (3, i), colors.HexColor("#F8C9C9")))
    t.setStyle(TableStyle(sty)); el.append(t)
    if notes:
        el += [Spacer(1, 8), Paragraph("<b>Drivers / vehicles</b>", ss["Heading2"])] + [Paragraph("• " + n, sm) for n in notes]
    SimpleDocTemplate(str(out / name), pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.1 * cm, bottomMargin=1.1 * cm).build(el)
    print(f"{d}: {len(man)} photos, {len(bad)} with differences -> {out / name}")
    for f in bad:
        print("  ", f.get("photo"), f.get("status"), json.dumps(f.get("differences"), ensure_ascii=False)[:300])
    if push and (bad or notes):
        lines = [f"{len(bad)} of {len(man)} photos differ from the gate log / ERP:"]
        for f in bad[:10]:
            x = (f.get("differences") or [{}])[0]
            lines.append(f"- {tabmap.get(f.get('photo'), '')} {f.get('what', '')}: {x.get('field', '')} photo {x.get('photo', '')} vs ERP {x.get('erp', '')} / gate {x.get('gate', '')}")
        lines += [f"- {n}" for n in notes[:5]]
        a70.push(f"Photo check - {d:%d-%b}", "\n".join(lines), a70.drive_link(name), "high")


def main():
    args = sys.argv[1:]
    d = datetime.strptime(args[args.index("--date") + 1], "%Y-%m-%d").date() if "--date" in args else date.today() - timedelta(days=1)
    folder = WORK / f"{d:%Y-%m-%d}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    man = build_manifest(d, folder)
    if not man:
        print(f"{d}: no photos"); return
    findings = run_agent(folder)
    if findings is None:
        a70.push(f"Photo check - {d:%d-%b}", "The AI photo agent did not finish - see photo_agent log.", "https://drive.google.com", "default")
        raise SystemExit("agent produced no findings.json")
    notes = driver_register(findings, man, d)
    report(d, man, findings, notes, "--no-push" not in args)
    if "--keep" not in args:
        for p in folder.glob("photo_*"):
            p.unlink()                                   # photos stay in Drive; only the findings are kept here


if __name__ == "__main__":
    main()
