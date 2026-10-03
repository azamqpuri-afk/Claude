"""
34_weekly_store_stock_check.py
Project : Dost Textiles - Weekly Store Issue Item Stock Check
Purpose : Runs every Friday at 10:00 AM via Task Scheduler ("DostTextiles
          Weekly Store Stock Check"). Reads the FROZEN master list of
          items (store_issue_master_list.csv, built once by
          33_store_issue_master_list.sql from DOSTEX.STORE_ISSUE_V,
          DT='ISS' - all distinct item codes issued via Store Issue in
          the trailing 2 months as of 2026-09-18, 346 items, deduped by
          item code) and looks up EACH item's real, current stock via
          DOSTEX.GET_ITEM_STOCK(p_ic, p_dd, p_uc) - the ERP's own live
          stock-balance function, verified byte-for-byte against a
          manual SUM(qty) from DOSTEX.STOCK on two real items before
          being trusted here (see 32_get_item_stock_verify.sql).

          This script does NOT re-derive "issued in the last 2 months"
          on every run - the tracked item set is fixed at whatever
          store_issue_master_list.csv contains. To redefine the tracked
          set later, re-run 33_store_issue_master_list.sql by hand and
          overwrite that CSV; this script will pick up the new list on
          its next scheduled run automatically.

Output  : One PDF per run, store_stock_check_<date>.pdf, written to the
          same Google-Drive-synced folder as every other report in this
          project (report_config.ini [output] folder) - Azam sees it in
          his own Drive automatically, no email/SMTP setup needed (his
          explicit choice over an emailed PDF, 2026-09-18).

          Table columns: Item Code, Description, UOM, Current Stock, Avg
          Monthly Use, Status. NOTE: MIN_QTY/MAX_QTY (reorder-level
          fields on the item master) were checked and found 0% populated
          ACROSS THE ENTIRE 8,097-row item master, not just these 346
          items - a dead/unused field in this ERP, confirmed 2026-09-18 -
          so a "below reorder level" flag isn't derivable from the item
          master. Instead (per explicit follow-up request, same day),
          "average monthly use" is derived from data already captured in
          store_issue_master_list.csv itself: TOTAL_QTY_ISSUED_2MO / 2 -
          real consumption history for exactly this item, not a stored
          ERP field. STATUS:
            - "OUT OF STOCK"  current stock <= 0
            - "LOW STOCK"     0 < current stock < avg monthly use
            - "IN STOCK"      current stock >= avg monthly use (or avg
                               monthly use is 0/unknown - can't assess
                               "low" without any consumption history,
                               so it's not flagged as such)
          Rows sorted OUT OF STOCK, then LOW STOCK, then IN STOCK, so the
          items needing attention are immediately visible without
          scrolling a 346-row table.

Config  : report_config.ini (same folder as this script, DB connection +
          Drive output folder) - reused via 13_daily_production_email.py's
          own load_config()/connect(), same importlib pattern already
          used by 27_month_end_reexport.py, not duplicated here.
"""

import csv
import importlib.util
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MASTER_LIST_PATH = SCRIPT_DIR / "store_issue_master_list.csv"
STATUS_LOG = SCRIPT_DIR / "weekly_stock_check_status_log.txt"

spec = importlib.util.spec_from_file_location(
    "daily_report", SCRIPT_DIR / "13_daily_production_email.py"
)
daily_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily_report)


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(STATUS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {msg}\n")


def load_master_list():
    if not MASTER_LIST_PATH.exists():
        raise FileNotFoundError(
            f"Master list not found: {MASTER_LIST_PATH}. "
            "Run 33_store_issue_master_list.sql once to build it first."
        )
    with open(MASTER_LIST_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fetch_current_stock(cursor, items, as_of):
    """One GET_ITEM_STOCK call per item, cheap (346 lightweight PL/SQL
    calls) - no bulk SQL equivalent exists for this ERP's own stock
    function, and correctness (matching the ERP's own screen) matters
    more than shaving a few hundred round-trips on a once-a-week job."""
    results = []
    for row in items:
        ic = row["IC"]
        uc = row["UC"]
        stock = cursor.callfunc(
            "dostex.get_item_stock", float, [ic, as_of, uc]
        )
        total_issued_2mo = float(row["TOTAL_QTY_ISSUED_2MO"] or 0)
        avg_monthly_use = total_issued_2mo / 2

        if stock <= 0:
            status = "OUT OF STOCK"
        elif avg_monthly_use > 0 and stock < avg_monthly_use:
            status = "LOW STOCK"
        else:
            status = "IN STOCK"

        results.append(
            {
                "ic": ic,
                "idesc": row["IDESC"],
                "uom": row["UOM"],
                "stock": stock,
                "avg_monthly_use": avg_monthly_use,
                "status": status,
            }
        )
    return results


def build_pdf(rows, as_of, out_path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate,
        Table,
        TableStyle,
        Paragraph,
        Spacer,
    )

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=landscape(letter),
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )

    out_count = sum(1 for r in rows if r["status"] == "OUT OF STOCK")
    low_count = sum(1 for r in rows if r["status"] == "LOW STOCK")
    title = Paragraph(
        f"Dost Textiles &ndash; Store Issue Item Stock Check &ndash; {as_of:%d-%b-%Y}",
        styles["Title"],
    )
    subtitle = Paragraph(
        f"{len(rows)} items tracked (issued via Store Issue in the trailing 2 months, "
        f"deduped by item code) &ndash; {out_count} OUT OF STOCK, {low_count} LOW STOCK "
        f"(below average monthly consumption)",
        styles["Normal"],
    )

    # OUT OF STOCK first, then LOW STOCK, then everything else - alphabetical
    # by item code within each tier.
    status_rank = {"OUT OF STOCK": 0, "LOW STOCK": 1, "IN STOCK": 2}
    ordered = sorted(rows, key=lambda r: (status_rank[r["status"]], r["ic"]))

    header = ["Item Code", "Description", "UOM", "Current Stock", "Avg Monthly Use", "Status"]
    data = [header]
    for r in ordered:
        data.append(
            [
                r["ic"],
                r["idesc"],
                r["uom"],
                f"{r['stock']:g}",
                f"{r['avg_monthly_use']:g}",
                r["status"],
            ]
        )

    table = Table(data, repeatRows=1, colWidths=[1.1*inch, 4.7*inch, 0.8*inch, 1.2*inch, 1.3*inch, 1.3*inch])
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]
    for i, r in enumerate(ordered, start=1):
        if r["status"] == "OUT OF STOCK":
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8d7da")))
        elif r["status"] == "LOW STOCK":
            style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff3cd")))
    table.setStyle(TableStyle(style))

    doc.build([title, subtitle, Spacer(1, 12), table])


def main():
    cfg = daily_report.load_config()
    as_of = date.today()

    try:
        items = load_master_list()
        conn = daily_report.connect(cfg)
        try:
            cursor = conn.cursor()
            rows = fetch_current_stock(cursor, items, as_of)
        finally:
            conn.close()

        out_folder = Path(cfg.get("output", "folder"))
        out_folder.mkdir(parents=True, exist_ok=True)
        out_path = out_folder / f"store_stock_check_{as_of.isoformat()}.pdf"
        build_pdf(rows, as_of, out_path)

        out_count = sum(1 for r in rows if r["status"] == "OUT OF STOCK")
        low_count = sum(1 for r in rows if r["status"] == "LOW STOCK")
        log(
            f"OK - {len(rows)} items checked, {out_count} OUT OF STOCK, "
            f"{low_count} LOW STOCK, wrote {out_path.name}"
        )
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
