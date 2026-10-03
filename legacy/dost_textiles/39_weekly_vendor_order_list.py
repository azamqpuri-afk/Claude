"""
39_weekly_vendor_order_list.py
Project : Dost Textiles - Weekly Store Order Draft List
Purpose : Runs every Friday (10:05 AM, right after the 10:00 AM weekly
          stock-check task) via Task Scheduler ("DostTextiles Weekly
          Vendor Order List"). Produces a DRAFT reorder list for items in
          the same frozen tracked list (store_issue_master_list.csv, 346
          items issued via Store Issue in the trailing 2 months) that are
          currently OUT OF STOCK or LOW STOCK (same definitions as
          34_weekly_store_stock_check.py: stock<=0, or 0<stock<avg
          monthly consumption) - i.e. only items that plausibly need
          reordering, not the full 346-item list.

          THIS SCRIPT ONLY GENERATES THE DRAFT. It does NOT send anything
          to vendors - per explicit instruction (2026-09-18), sending only
          happens after Azam reviews the draft and confirms, at which
          point a live Claude Code session drives WhatsApp Web to send
          the actual messages. No WhatsApp Business API / Twilio is
          configured or needed for this script.

          Per explicit instruction (2026-09-18): NO suggested order
          quantity is computed - Azam decides quantities himself. The PDF
          leaves an "Order Qty" column blank for him to fill in by hand
          (or dictate before I send). Current stock + avg monthly
          consumption are shown as reference only, not used to compute a
          number here.

Vendor attribution: derived from real purchase-order history -
          DOSTEX.PO_DET (item detail) -> PO_MAIN (header, has AC = vendor
          account code) -> ACH (vendor name/phone), most recent PO_DATE
          per item. NOT a stored/dedicated item-vendor mapping - this
          ERP doesn't have one, confirmed via schema search (2026-09-18).
          An item is treated as having NO usable vendor for WhatsApp
          purposes if:
            - it has no PO history at all, OR
            - its most recent vendor has no phone number on file, OR
            - its most recent vendor's name matches '%CASH%' (e.g.
              "PURCHASE ON CASH" - a generic petty-purchase account, not
              a real contactable supplier)
          These items are still shown on the draft (per explicit
          instruction - flag, don't drop) under their own "NO VENDOR ON
          FILE" section, grouped separately from real vendor orders.

Output  : weekly_vendor_order_list_<date>.pdf, written to the same
          Drive-synced DostTextile_Reports folder as everything else in
          this project.

Config  : report_config.ini - DB connection reused via
          13_daily_production_email.py's load_config()/connect(), same
          importlib pattern as 34_weekly_store_stock_check.py.
"""

import csv
import importlib.util
import traceback
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MASTER_LIST_PATH = SCRIPT_DIR / "store_issue_master_list.csv"
STATUS_LOG = SCRIPT_DIR / "weekly_vendor_order_status_log.txt"

spec = importlib.util.spec_from_file_location(
    "daily_report", SCRIPT_DIR / "13_daily_production_email.py"
)
daily_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily_report)

VENDOR_LOOKUP_SQL = """
    SELECT ic, vendor_name, vendor_phone
    FROM (
        SELECT d.ic, a.an AS vendor_name, a.ph AS vendor_phone,
               ROW_NUMBER() OVER (PARTITION BY d.ic ORDER BY m.po_date DESC) rn
        FROM dostex.po_det d
        JOIN dostex.po_main m ON m.trans_no = d.trans_no AND m.uc = d.uc
        JOIN dostex.ach a ON a.ac = m.ac
        WHERE d.ic IN ({placeholders})
    )
    WHERE rn = 1
"""


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


def fetch_current_stock_and_status(cursor, items, as_of):
    """Same OUT OF STOCK / LOW STOCK / IN STOCK definitions as
    34_weekly_store_stock_check.py - kept independent/duplicated rather
    than imported, since these are two separate scheduled scripts by
    design (2026-09-18 instruction: keep them separate)."""
    results = []
    for row in items:
        ic = row["IC"]
        uc = row["UC"]
        stock = cursor.callfunc("dostex.get_item_stock", float, [ic, as_of, uc])
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
                "uc": uc,
                "idesc": row["IDESC"],
                "uom": row["UOM"],
                "stock": stock,
                "avg_monthly_use": avg_monthly_use,
                "status": status,
            }
        )
    return results


def fetch_vendor_info(cursor, item_codes):
    if not item_codes:
        return {}
    placeholders = ", ".join(f":ic{i}" for i in range(len(item_codes)))
    binds = {f"ic{i}": ic for i, ic in enumerate(item_codes)}
    sql = VENDOR_LOOKUP_SQL.format(placeholders=placeholders)
    cursor.execute(sql, binds)
    cols = [d[0].lower() for d in cursor.description]
    return {row[0]: dict(zip(cols, row)) for row in cursor.fetchall()}


def has_usable_vendor(vendor_info):
    if vendor_info is None:
        return False
    name = (vendor_info.get("vendor_name") or "").strip().upper()
    phone = (vendor_info.get("vendor_phone") or "").strip()
    if not phone:
        return False
    if "CASH" in name:
        return False
    return True


def build_pdf(needs_reorder, vendor_by_ic, as_of, out_path):
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

    by_vendor = defaultdict(list)
    no_vendor = []
    for item in needs_reorder:
        vendor_info = vendor_by_ic.get(item["ic"])
        if has_usable_vendor(vendor_info):
            key = (vendor_info["vendor_name"], vendor_info["vendor_phone"])
            by_vendor[key].append(item)
        else:
            no_vendor.append(item)

    story = [
        Paragraph(
            f"Dost Textiles &ndash; Weekly Store Order Draft &ndash; {as_of:%d-%b-%Y}",
            styles["Title"],
        ),
        Paragraph(
            f"{len(needs_reorder)} item(s) need reordering (OUT OF STOCK or LOW STOCK) "
            f"&ndash; DRAFT ONLY, nothing sent to vendors yet. Order Qty left blank "
            f"for review before sending.",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]

    header = ["Item Code", "Description", "UOM", "Current Stock", "Avg Monthly Use", "Status", "Order Qty"]
    col_widths = [1.0*inch, 4.2*inch, 0.7*inch, 1.1*inch, 1.2*inch, 1.1*inch, 1.0*inch]

    def make_table(rows):
        data = [header]
        for r in sorted(rows, key=lambda r: r["ic"]):
            data.append(
                [
                    r["ic"],
                    r["idesc"],
                    r["uom"],
                    f"{r['stock']:g}",
                    f"{r['avg_monthly_use']:g}",
                    r["status"],
                    "",
                ]
            )
        table = Table(data, repeatRows=1, colWidths=col_widths)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ]
        for i, r in enumerate(sorted(rows, key=lambda r: r["ic"]), start=1):
            if r["status"] == "OUT OF STOCK":
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8d7da")))
        table.setStyle(TableStyle(style))
        return table

    for (vendor_name, vendor_phone), rows in sorted(by_vendor.items()):
        story.append(Paragraph(f"<b>{vendor_name}</b> &ndash; {vendor_phone}", styles["Heading2"]))
        story.append(make_table(rows))
        story.append(Spacer(1, 14))

    if no_vendor:
        story.append(Paragraph("<b>NO VENDOR ON FILE</b> &ndash; no contactable supplier found in purchase history, source manually", styles["Heading2"]))
        story.append(make_table(no_vendor))

    doc.build(story)
    return len(by_vendor), len(no_vendor)


def main():
    cfg = daily_report.load_config()
    as_of = date.today()

    try:
        items = load_master_list()
        conn = daily_report.connect(cfg)
        try:
            cursor = conn.cursor()
            checked = fetch_current_stock_and_status(cursor, items, as_of)
            needs_reorder = [r for r in checked if r["status"] in ("OUT OF STOCK", "LOW STOCK")]
            vendor_by_ic = fetch_vendor_info(cursor, [r["ic"] for r in needs_reorder])
        finally:
            conn.close()

        out_folder = Path(cfg.get("output", "folder"))
        out_folder.mkdir(parents=True, exist_ok=True)
        out_path = out_folder / f"weekly_vendor_order_list_{as_of.isoformat()}.pdf"
        vendor_count, no_vendor_count = build_pdf(needs_reorder, vendor_by_ic, as_of, out_path)

        log(
            f"OK - {len(needs_reorder)} items need reorder, {vendor_count} vendor group(s), "
            f"{no_vendor_count} item(s) with no usable vendor, wrote {out_path.name}"
        )
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
