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

    s.append(Paragraph("In one line", H2))
    s.append(Paragraph("The app sits on top of your ERP, <b>only reads</b> it (never changes anything), and shows you "
                       "clean reports and alerts on a web page. It is installed on <b>one computer that stays on</b>; "
                       "everyone else just opens a web page in their browser.", BODY))

    s.append(Paragraph("Collect these before we start (tick each box)", H2))
    s.append(bullets([
        "&#9744; <b>A computer that stays on</b> - a Windows PC (Windows 10 or 11, 64-bit) that is never switched off, "
        "sitting near your ERP server, on the <b>same network/LAN</b> as it. 8 GB RAM and 20 GB free space is plenty. "
        "(Do not use a personal laptop that sleeps.)",
        "&#9744; <b>The ERP database login (view-only)</b> - three things written on a paper: "
        "(1) the database <b>address</b> as <font face='Courier'>host:port/service</font> "
        "(e.g. <font face='Courier'>192.168.18.199:1521/orcl</font>), (2) a <b>username</b>, (3) its <b>password</b>. "
        "Ask your ERP/IT person for a <b>read-only</b> login - one that can look but not change.",
        "&#9744; <b>(Oracle 11g only) the Oracle Instant Client</b> - a free small download from Oracle "
        "(\"Instant Client Basic Light, Windows 64-bit\"). Unzip it to a simple folder like "
        "<font face='Courier'>C:\\oracle\\instantclient_23</font> and remember that folder. If your ERP is Oracle 12c or "
        "newer, you may not need this.",
        "&#9744; <b>(If you have a loom scoreboard) its database file</b> - the loom monitor (e.g. OLMS) keeps an "
        "Access file like <font face='Courier'>OLMSDB.mdb</font>. Make a copy of it to a fixed folder, e.g. "
        "<font face='Courier'>E:\\scoreboard_copy\\OLMSDB_copy.mdb</font>, and remember that path. (The app reads the "
        "copy so it never disturbs the live scoreboard.) Skip this if you have no scoreboard.",
        "&#9744; <b>The mill's name</b> - exactly how you want it shown on the dashboard.",
        "&#9744; <b>Internet on this PC for one minute</b> - only to send us the machine ID and get your licence.",
    ]))

    s.append(Paragraph("How to install - click by click", H2))
    s.append(bullets([
        "1. Copy <font face='Courier'>SmartLayer-Setup.exe</font> onto the always-on PC and <b>double-click</b> it.",
        "2. If a blue box says <i>\"Windows protected your PC\"</i>, click <b>More info</b>, then <b>Run anyway</b> "
        "(this is normal for new apps).",
        "3. Click <b>Next</b> / <b>Install</b>, wait for the bar to fill, then <b>Finish</b>.",
        "4. A <b>setup window</b> opens and asks questions one at a time. Type the answers you collected above "
        "(mill name, then the database address, username, password; the Oracle folder if asked; the scoreboard copy "
        "path if you have one; and a <b>dashboard password</b> you choose). Press Enter after each.",
        "5. It then <b>reads your ERP once</b> to recognise it, and does the <b>first sync</b> (pulling the data). "
        "This can take a few minutes - let it finish and leave the window open until it says it is done.",
    ]))

    s.append(Paragraph("Get your licence - one time", H2))
    s.append(bullets([
        "Open the black command window (Start menu &rarr; type <font face='Courier'>cmd</font>), then type "
        "<font face='Courier'>SmartLayer.exe machine-id</font> and press Enter. It prints a long ID.",
        "Send us that ID. We send back a licence code tied to this one PC.",
        "Open the dashboard, go to the <b>Licence</b> screen, and paste the code. Done.",
    ]))

    s.append(Paragraph("Check the numbers are right", H2))
    s.append(bullets([
        "Type <font face='Courier'>SmartLayer.exe check --deep</font> and press Enter. "
        "It checks the pulled data and <b>flags anything that looks wrong</b> (so you know before trusting a report).",
    ]))

    s.append(Paragraph("Open the dashboard", H2))
    s.append(bullets([
        "On this PC: open a browser and go to <font face='Courier'>http://localhost:8080</font>.",
        "On a phone or another PC on the mill network: <font face='Courier'>http://&lt;this-PC-address&gt;:8080</font> "
        "(ask IT for this PC's address, e.g. <font face='Courier'>192.168.18.50</font>).",
        "The data refreshes by itself on a schedule. To pull fresh data by hand any time: "
        "<font face='Courier'>SmartLayer.exe sync</font>.",
    ]))

    s.append(Paragraph("If your ERP is a new one we have not seen", H2))
    s.append(Paragraph("The app reads your ERP once at setup and recognises it automatically. If it is a brand-new ERP "
                       "we have not profiled, it drafts the connection and flags the few things it is unsure about for a "
                       "one-time confirm - then it works the same as any other mill.", BODY))

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
