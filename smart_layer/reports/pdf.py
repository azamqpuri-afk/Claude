"""PDF version of any report (reportlab): title, summary lines and the table, landscape when it is wide."""

from pathlib import Path
from xml.sax.saxutils import escape

SEVERITY_COLORS = {"ACTION": "#d03b3b", "CONFIRMED": "#ec835a", "WATCH": "#fab219", "OUT OF STOCK": "#d03b3b",
                   "LOW STOCK": "#fab219", "NONE": "#d03b3b"}


def write_pdf(report, path, company="", product=""):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

    from . import fmt
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wide = len(report.columns) > 6
    page = landscape(A4) if wide else A4
    styles = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=styles["BodyText"], fontSize=7.5 if wide else 8.5, leading=9.5 if wide else 10.5)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")
    navy = colors.HexColor("#1F3B57")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawCentredString(page[0] / 2, 8 * mm, f"{product} · {company} · page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=14 * mm, title=report.title, author=product)
    story = [Paragraph(escape(company), ParagraphStyle("co", parent=styles["Normal"], textColor=navy, fontSize=9)),
             Paragraph(escape(report.title), ParagraphStyle("t", parent=styles["Title"], alignment=0, textColor=navy, fontSize=15)),
             *[Paragraph(escape(s), styles["BodyText"]) for s in report.summary if s], Spacer(1, 4 * mm)]
    if report.rows:
        data = [[Paragraph(escape(str(c)), head) for c in report.columns]]
        numeric = [all(isinstance(r[i], (int, float)) or r[i] in (None, "") for r in report.rows) for i in range(len(report.columns))]
        style = [("BACKGROUND", (0, 0), (-1, 0), navy), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9dde3")),
                 ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6f8")])]
        for r_i, row in enumerate(report.rows, 1):
            out = []
            for c_i, v in enumerate(row):
                text = escape(fmt(v))
                if str(v) in SEVERITY_COLORS:
                    text = f'<font color="{SEVERITY_COLORS[str(v)]}"><b>{text}</b></font>'
                p = Paragraph(text, ParagraphStyle(f"c{c_i}", parent=cell, alignment=2 if numeric[c_i] else 0))
                out.append(p)
            data.append(out)
        avail = page[0] - 24 * mm
        char = (7.5 if wide else 8.5) * 0.66                  # generous character width (capitals, bold), points
        cols = range(len(report.columns))
        # every column at least as wide as its longest word (no words broken), the rest shared by content length
        words = [max(len(w) for txt in [str(report.columns[i])] + [fmt(r[i]) for r in report.rows] for w in (txt.split() or [""]))
                 for i in cols]
        minw = [min(max(n, 3), 18) * char + 14 for n in words]
        want = [min(max(len(str(report.columns[i])), *(len(fmt(r[i])) for r in report.rows)), 70) * char + 14 for i in cols]
        spare = max(avail - sum(minw), 0)
        extra = [max(w - m, 0) for w, m in zip(want, minw)]
        widths = [m + (spare * e / sum(extra) if sum(extra) else spare / len(minw)) for m, e in zip(minw, extra)]
        if sum(widths) > avail:
            widths = [w * avail / sum(widths) for w in widths]
        t = LongTable(data, colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle(style))
        story.append(t)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return path
