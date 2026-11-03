"""Reports written once against the standard entities - they work for any mapped ERP."""

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Report:
    title: str
    columns: list
    rows: list
    summary: list = field(default_factory=list)     # short lines shown above the table / in a push
    xlsx_sheets: list = None                         # optional live-formula workbook spec (reports.xlsx.write)

    def text(self, limit=None):
        out = [self.title, "=" * len(self.title), *self.summary, ""]
        rows = self.rows[:limit] if limit else self.rows
        if rows:
            cells = [[fmt(v) for v in r] for r in rows]
            widths = [max(len(str(h)), *(len(c[i]) for c in cells)) for i, h in enumerate(self.columns)]
            out.append("  ".join(str(h).ljust(w) for h, w in zip(self.columns, widths)))
            out.append("  ".join("-" * w for w in widths))
            out += ["  ".join(c.ljust(w) for c, w in zip(r, widths)) for r in cells]
            if limit and len(self.rows) > limit:
                out.append(f"... {len(self.rows) - limit} more rows")
        return "\n".join(out)

    def write_csv(self, folder, name):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        with open(path, "w", newline="", encoding="utf-8-sig") as f:     # BOM so Excel opens Urdu text right
            w = csv.writer(f)
            w.writerow(self.columns)
            w.writerows(self.rows)
        return path


    def write_pdf(self, folder, name, company="", product=""):
        from .pdf import write_pdf
        return write_pdf(self, Path(folder) / name, company, product)

    def write_xlsx(self, folder, name, company=""):
        from . import xlsx
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        if self.xlsx_sheets:                                 # a live-formula workbook (shaded inputs + formulas)
            return xlsx.write(folder / name, self.xlsx_sheets, company)
        return xlsx.report_to_xlsx(self, folder / name, company)


def fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)
