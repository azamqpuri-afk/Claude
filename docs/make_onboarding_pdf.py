"""Generate the mill onboarding checklist PDF (what a mill must provide to install Project Smart Layer)."""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

ACCENT = colors.HexColor("#2a78d6")
DARK = colors.HexColor("#0b0b0b")
MUTED = colors.HexColor("#52514e")

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Title"], fontSize=20, textColor=DARK, spaceAfter=2, alignment=TA_LEFT)
SUB = ParagraphStyle("SUB", parent=ss["Normal"], fontSize=10.5, textColor=MUTED, spaceAfter=12)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], fontSize=13, textColor=ACCENT, spaceBefore=12, spaceAfter=4)
BODY = ParagraphStyle("BODY", parent=ss["Normal"], fontSize=10.5, textColor=DARK, leading=15, spaceAfter=4)
NOTE = ParagraphStyle("NOTE", parent=BODY, textColor=MUTED, fontSize=9.5, leading=13)


def bullets(items):
    return ListFlowable([ListItem(Paragraph(t, BODY), leftIndent=6) for t in items],
                        bulletType="bullet", bulletColor=ACCENT, leftIndent=12, spaceAfter=6)


def build(path):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Project Smart Layer - Mill Onboarding Checklist")
    s = []
    s.append(Paragraph("Project Smart Layer by Azam Puri", H1))
    s.append(Paragraph("What the mill needs to provide to install the app (read-only on your ERP)", SUB))

    s.append(Paragraph("How it runs (read this first)", H2))
    s.append(Paragraph("Smart Layer has two parts:", BODY))
    s.append(bullets([
        "<b>The engine</b> - reads the ERP, runs the dashboard, sends the alerts and does the automatic syncs. "
        "It is installed on <b>one always-on Windows PC</b> near the ERP.",
        "<b>The viewer</b> - the dashboard is just a web page. The owner and managers open it in <b>any browser</b> "
        "(Windows, <b>Mac</b>, iPhone, Android). Nothing to install, and their device does <b>not</b> need to stay on.",
    ]))
    s.append(Paragraph("So the app is installed on the always-on PC only. The owner's MacBook, laptop or phone just opens "
                       "the dashboard in a browser - on the mill network, or from anywhere through a secure Tailscale link. "
                       "Do not run the engine on a personal laptop that sleeps or switches off, or it will miss syncs and alerts.", NOTE))

    s.append(Paragraph("1. A computer to run it (the always-on PC)", H2))
    s.append(bullets([
        "An always-on Windows PC (not the ERP database server itself). Windows 10/11 or Server, 64-bit.",
        "Modest spec is fine: 8 GB RAM, ~20 GB free disk. It only reads the ERP and serves a dashboard.",
        "It must be on the same network as the ERP database server.",
    ]))

    s.append(Paragraph("2. ERP database connection (read-only)", H2))
    s.append(bullets([
        "Which database the ERP uses: <b>Oracle</b>, SQL Server, MySQL or PostgreSQL, and its version.",
        "The database address: <b>host : port / service</b> (for Oracle, e.g. <font face='Courier'>192.168.18.199:1521/orcl</font>), "
        "or the TNS name.",
        "A <b>read-only</b> database user and password. Best practice: create a dedicated read-only user "
        "(the app prints the exact SQL with <font face='Courier'>SmartLayer.exe grants</font>). "
        "Never give a write/admin login.",
        "If payroll or other data lives in a <b>separate schema</b>, that read-only user must be allowed to read it too.",
    ]))
    s.append(Paragraph("The password is typed once during setup and stored only on this PC (sealed to the machine on "
                       "Windows). It is never shown again and never leaves the mill.", NOTE))

    s.append(Paragraph("3. Oracle 11g only - the Oracle Client", H2))
    s.append(bullets([
        "Older Oracle (11g and earlier) needs the free <b>Oracle Instant Client 19 or newer</b> (Windows x64) on the PC.",
        "Download Basic Light, unzip to e.g. <font face='Courier'>C:\\oracle\\instantclient_23</font>, and note that folder "
        "(it has <font face='Courier'>oci.dll</font>). Setup asks for it.",
        "This does not touch or change your existing Oracle install - it sits alongside, used only by this app.",
    ]))

    s.append(Paragraph("4. Network access", H2))
    s.append(bullets([
        "The PC must reach the ERP database server over the LAN (e.g. Oracle port 1521 open to this PC).",
        "To open the dashboard to phones/PCs on the mill network, the app opens its dashboard port (default 8080) "
        "in the Windows firewall during install.",
    ]))

    s.append(Paragraph("5. Choices to make at setup", H2))
    s.append(bullets([
        "A <b>dashboard password</b> of your choice (type it; don't leave it blank).",
        "Whether to open the dashboard to the whole mill network or keep it on this PC only.",
        "Optional - the owner's phone: install the free <b>ntfy</b> app for alerts (setup gives a private topic to subscribe to).",
        "Optional - a <b>Claude API key</b> if you want the 'Ask the ERP' chat.",
    ]))

    s.append(Paragraph("6. Licence (one step)", H2))
    s.append(bullets([
        "After install, run <font face='Courier'>SmartLayer.exe machine-id</font> and send us that ID.",
        "We send back a licence file/code, tied to that one PC. Paste it on the dashboard's Licence screen.",
    ]))

    s.append(Paragraph("7. If the ERP is one we have not mapped before", H2))
    s.append(bullets([
        "The app reads each ERP through a mapping file. We already have mappings for known ERPs.",
        "For a new ERP, we run a few read-only queries to list the relevant tables and columns, then write its mapping - "
        "a one-time step, after which everything works the same.",
    ]))

    s.append(Spacer(1, 8))
    tbl = Table([
        ["The app NEVER", ""],
        ["Writes to the ERP", "every connection is read-only (SET TRANSACTION READ ONLY)"],
        ["Sends mill data out", "all data stays on the mill's own PC"],
        ["Needs admin DB rights", "a plain read-only login is enough"],
    ], colWidths=[48 * mm, 110 * mm])
    tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 9.5),
        ("FONT", (0, 0), (0, -1), "Helvetica-Bold", 9.5),
        ("SPAN", (0, 0), (1, 0)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 1), (-1, -1), DARK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f4f4f1"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e1dc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    s.append(tbl)
    s.append(Spacer(1, 10))
    s.append(Paragraph("Project Smart Layer by Azam Puri - reads your ERP, changes nothing.", NOTE))
    doc.build(s)
    return path


if __name__ == "__main__":
    out = Path(__file__).with_name("Mill_Onboarding_Checklist.pdf")
    build(out)
    print("wrote", out)
