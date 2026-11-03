"""
Excel export (openpyxl). Two uses:

  report_to_xlsx(report, path)   a plain dump of any Report (headers + rows + summary), for "Excel + PDF" reports.
  write(path, sheets)            a workbook with LIVE FORMULAS: input cells are shaded, results are real Excel
                                 formulas that recalculate when an input is edited. Used by the closing / sizer
                                 reports so the owner can change a count or a weight and see the numbers update.

The files open unchanged in Excel and Google Sheets (formulas intact). No cloud API - a local .xlsx.
"""

from pathlib import Path


class Inp:
    """An INPUT cell: a value the user may edit. Shaded so it stands out from the computed cells."""
    __slots__ = ("value", "fmt")

    def __init__(self, value, fmt=None):
        self.value, self.fmt = value, fmt


class Fx:
    """A FORMULA cell: an Excel formula string (with or without the leading '='), recalculated live."""
    __slots__ = ("formula", "fmt")

    def __init__(self, formula, fmt=None):
        self.formula = formula if str(formula).startswith("=") else "=" + str(formula)
        self.fmt = fmt


def _lazy():
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    return openpyxl, Font, PatternFill, Alignment, get_column_letter


MONEY = '#,##0'
NAVY = "1F3B57"
INPUT_FILL = "FFF4D6"          # light amber - an editable input
HEAD_FILL = "1F3B57"


def report_to_xlsx(report, path, company=""):
    """Dump a Report (plain values) to an .xlsx with a title, summary and a styled header row."""
    openpyxl, Font, PatternFill, Alignment, col = _lazy()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = _safe_sheet(report.title)
    r = 1
    if company:
        ws.cell(r, 1, company).font = Font(bold=True, color=NAVY); r += 1
    ws.cell(r, 1, report.title).font = Font(bold=True, size=14, color=NAVY); r += 1
    for line in (report.summary or []):
        ws.cell(r, 1, line); r += 1
    r += 1
    head_row = r
    for c, h in enumerate(report.columns, 1):
        cell = ws.cell(r, c, str(h))
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=HEAD_FILL)
    r += 1
    for row in report.rows:
        for c, v in enumerate(row, 1):
            ws.cell(r, c, v)
        r += 1
    _autosize(ws, col, len(report.columns))
    ws.freeze_panes = ws.cell(head_row + 1, 1)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return Path(path)


def write(path, sheets, company=""):
    """Workbook with live formulas. sheets = [{name, title, note?, headers, rows, money_cols?}]; each row is a list
    of cells where a cell is a plain value, an Inp(value) (shaded, editable) or an Fx('formula') (recalculated)."""
    openpyxl, Font, PatternFill, Alignment, col = _lazy()
    wb = openpyxl.Workbook()
    first = True
    inp_fill = PatternFill("solid", fgColor=INPUT_FILL)
    for sh in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = _safe_sheet(sh.get("name") or sh.get("title") or "Sheet")
        r = 1
        if company:
            ws.cell(r, 1, company).font = Font(bold=True, color=NAVY); r += 1
        if sh.get("title"):
            ws.cell(r, 1, sh["title"]).font = Font(bold=True, size=13, color=NAVY); r += 1
        for line in (sh.get("note") or []):
            ws.cell(r, 1, line); r += 1
        r += 1
        headers = sh.get("headers") or []
        money = set(sh.get("money_cols") or [])
        for c, h in enumerate(headers, 1):
            cell = ws.cell(r, c, str(h))
            cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor=HEAD_FILL)
        head_row = r
        r += 1
        for row in sh.get("rows") or []:
            for c, v in enumerate(row, 1):
                if isinstance(v, Fx):
                    cell = ws.cell(r, c); cell.value = v.formula
                    if v.fmt or (c - 1) in money:
                        cell.number_format = v.fmt or MONEY
                elif isinstance(v, Inp):
                    cell = ws.cell(r, c, v.value); cell.fill = inp_fill
                    if v.fmt or (c - 1) in money:
                        cell.number_format = v.fmt or MONEY
                else:
                    cell = ws.cell(r, c, v)
                    if (c - 1) in money and isinstance(v, (int, float)):
                        cell.number_format = MONEY
            r += 1
        if sh.get("legend"):
            r += 1
            lg = ws.cell(r, 1, sh["legend"]); lg.font = Font(italic=True, color="888888")
        _autosize(ws, col, len(headers))
        ws.freeze_panes = ws.cell(head_row + 1, 1)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(path))
    return Path(path)


def colref(col_index, row):
    """A1-style reference for a 0-based column index and 1-based row (for building formulas)."""
    _, _, _, _, col = _lazy()
    return f"{col(col_index + 1)}{row}"


def _safe_sheet(name):
    out = "".join(c for c in str(name) if c not in '[]:*?/\\')[:31]
    return out or "Sheet"


def _autosize(ws, col, ncols):
    for c in range(1, max(ncols, 1) + 1):
        width = 10
        for cell in ws[col(c)]:
            v = cell.value
            if v is not None:
                width = max(width, min(48, len(str(v)) + 2))
        ws.column_dimensions[col(c)].width = width
