"""
36_daily_zero_stock_check.py
Project : Dost Textiles - Daily New-Zero-Stock Push Alert
Purpose : Runs once a day via Task Scheduler ("DostTextiles Daily
          Zero-Stock Alert"). Checks the SAME frozen tracked-item list as
          34_weekly_store_stock_check.py (store_issue_master_list.csv,
          346 items issued via Store Issue in the trailing 2 months as of
          2026-09-18, deduped by item code) and pushes a phone
          notification via ntfy.sh for any item that just crossed from
          in-stock to zero since yesterday's run.

          DELIBERATELY SEPARATE from 34_weekly_store_stock_check.py, per
          explicit request (2026-09-18) - different cadence (daily vs
          weekly), different output (a push ping vs a PDF), different
          purpose (be told the moment something runs out vs a periodic
          full review). Both read the same master list but keep their
          own state/logs, don't merge them.

          NEW-ZERO-ONLY, not "every item currently at zero": as of
          rollout, 121 of 346 tracked items are ALREADY at zero stock
          (see 34_weekly_store_stock_check.py's first real run,
          2026-09-18) - alerting on "any item at zero" every day would
          re-ping on all 121 of those forever, which is noise, not a
          signal. This script only pings when an item's stock was > 0 on
          the PREVIOUS run and is <= 0 on THIS run (a genuine new
          stockout). On the very first run ever (no prior snapshot to
          compare against), nothing is pushed - there's no "before" to
          detect a transition from; the snapshot is just established as
          the baseline for tomorrow.

Stock source: DOSTEX.GET_ITEM_STOCK(p_ic, p_dd, p_uc) - same function
          used and verified in 34_weekly_store_stock_check.py, not
          re-verified here.

State   : scripts/last_stock_snapshot.csv (IC -> stock as of the most
          recent run) - local only, NOT written to the Drive folder
          (operational state, not a deliverable). Overwritten every run
          after the comparison is made.

Push    : ntfy.sh (report_config.ini [ntfy] topic) - free, no account
          needed. One notification per run listing every newly-zero item
          (not one push per item, to avoid a notification storm if many
          items run out the same day).

Config  : report_config.ini (same folder as this script) - DB connection
          reused via 13_daily_production_email.py's own load_config()/
          connect(), same importlib pattern as 34_weekly_store_stock_
          check.py and 27_month_end_reexport.py.
"""

import csv
import importlib.util
import json
import traceback
import urllib.request
from datetime import date, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
MASTER_LIST_PATH = SCRIPT_DIR / "store_issue_master_list.csv"
SNAPSHOT_PATH = SCRIPT_DIR / "last_stock_snapshot.csv"
STATUS_LOG = SCRIPT_DIR / "daily_zero_stock_status_log.txt"

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


def load_previous_snapshot():
    if not SNAPSHOT_PATH.exists():
        return None  # no baseline yet - first ever run
    with open(SNAPSHOT_PATH, encoding="utf-8") as f:
        return {row["IC"]: float(row["STOCK"]) for row in csv.DictReader(f)}


def save_snapshot(current_stock_by_ic):
    with open(SNAPSHOT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["IC", "STOCK"])
        for ic, stock in current_stock_by_ic.items():
            writer.writerow([ic, stock])


def fetch_current_stock(cursor, items, as_of):
    """One GET_ITEM_STOCK call per item - same approach and same
    reasoning (no bulk-SQL equivalent exists) as
    34_weekly_store_stock_check.py."""
    results = {}
    descriptions = {}
    for row in items:
        ic = row["IC"]
        uc = row["UC"]
        stock = cursor.callfunc("dostex.get_item_stock", float, [ic, as_of, uc])
        results[ic] = stock
        descriptions[ic] = row["IDESC"]
    return results, descriptions


def item_details(cursor, ics):
    """Owner (2026-09-29): with each zero-stock item show its last purchase (date, supplier, qty, rate - STORE_RECEIPT_V)
    and the loom / cost centre it was last issued to (STORE_ISSUE_V cost centre, e.g. LOOM NO 14), plus the other
    looms it went to in the last 60 days."""
    det = {}
    for ic in ics:
        cursor.execute("""SELECT * FROM (SELECT TO_CHAR(dd,'DD-Mon-YY'), an, qty, rate FROM dostex.store_receipt_v
                          WHERE ic = :ic ORDER BY dd DESC, dno DESC) WHERE ROWNUM = 1""", ic=ic)
        r = cursor.fetchone()
        cursor.execute("""SELECT TO_CHAR(dd,'DD-Mon-YY'), cn FROM dostex.store_issue_v WHERE ic = :ic
                          AND dd >= SYSDATE - 60 ORDER BY dd DESC, dno DESC""", ic=ic)
        iss = cursor.fetchall()
        if not iss:
            cursor.execute("""SELECT * FROM (SELECT TO_CHAR(dd,'DD-Mon-YY'), cn FROM dostex.store_issue_v WHERE ic = :ic
                              ORDER BY dd DESC, dno DESC) WHERE ROWNUM = 1""", ic=ic)
            iss = cursor.fetchall()
        looms = []
        for _d, cn in iss:
            nm = (cn or "?").strip().replace("LOOM NO ", "Loom ")
            if nm not in looms:
                looms.append(nm)
        if r:
            bought = f"last bought {r[0]} from {(r[1] or '?').strip()} ({float(r[2] or 0):g} @ Rs {float(r[3] or 0):,.0f})"
        else:
            bought = "no purchase in the ERP"
        if iss:
            issued = f"last issued {iss[0][0]} to {looms[0]}" + (f" · also {', '.join(looms[1:6])} (last 60 days)" if len(looms) > 1 else "")
        else:
            issued = "never issued"
        det[ic] = {"bought": bought, "issued": issued}
    return det


def send_ntfy_push(topic, newly_zero, descriptions, details=None):
    lines = []
    for ic in newly_zero:
        dt = (details or {}).get(ic)
        lines.append(f"{ic} - {descriptions[ic]}" + (f"\n   {dt['bought']}\n   {dt['issued']}" if dt else ""))
    body = "\n".join(lines)
    title = f"{len(newly_zero)} item(s) just hit ZERO stock"

    req = urllib.request.Request(
        url=f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": title,
            "Priority": "high",
            "Tags": "warning,package",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status


def main():
    cfg = daily_report.load_config()
    as_of = date.today()

    try:
        items = load_master_list()
        previous = load_previous_snapshot()

        conn = daily_report.connect(cfg)
        try:
            cursor = conn.cursor()
            current, descriptions = fetch_current_stock(cursor, items, as_of)
            new_zero = [ic for ic, stock in current.items()
                        if previous is not None and stock <= 0 and previous.get(ic, 0) > 0]
            details = item_details(cursor, new_zero) if new_zero else {}
        finally:
            conn.close()

        if previous is None:
            log(
                f"BASELINE - {len(current)} items snapshotted, no prior "
                "baseline to compare against - nothing pushed on first run."
            )
        else:
            newly_zero = [
                ic
                for ic, stock in current.items()
                if stock <= 0 and previous.get(ic, 0) > 0
            ]
            if newly_zero:
                topic = cfg.get("ntfy", "topic")
                status = send_ntfy_push(topic, newly_zero, descriptions, details)
                log(
                    f"OK - {len(current)} items checked, {len(newly_zero)} NEW "
                    f"zero-stock item(s), push sent (HTTP {status}): "
                    + ", ".join(newly_zero)
                )
            else:
                log(f"OK - {len(current)} items checked, 0 new zero-stock items, no push sent.")

        save_snapshot(current)
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
