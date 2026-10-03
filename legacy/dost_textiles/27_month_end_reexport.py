"""
27_month_end_reexport.py
Project : Dost Textiles - Month-end full re-export for retroactive-
          correction verification (2026-08-28 request; widened
          2026-08-29 to also cover fabric/yarn exports)
Purpose : Re-export a set of per-date CSVs for EVERY date in a given
          month, overwriting whatever's currently in the Drive folder
          for each of those dates with a fresh pull from ERP's CURRENT
          state - not the state at whatever time it was first exported.

          Why this exists: a production/yarn/fabric value can be
          corrected in ERP by staff after the fact (backdated
          correction), but a CSV export for that historical date, once
          written, never refreshes itself again just because the
          underlying data changed days or weeks later - the daily
          script's own ROLLING_REFRESH_DAYS window (see
          13_daily_production_email.py) only covers the trailing 5
          days, not a full month. This script closes that gap in one
          deliberate step, meant to run shortly after each month ends
          so Azam can run `update_loomwise_report.py
          --check-corrections` (a separate, review-first tool - never
          auto-corrects) against genuinely fresh data.

          SCOPE (widened 2026-08-29, was loom_quality_history_with_
          contract_ only): now covers that file PLUS every fabric_* and
          yarn_* prefixed export - 9 files total:
            - loom_quality_history_with_contract_ (production detail)
            - yarn_issue_actual_ / yarn_received_ / yarn_received_detail_
            - yarn_to_sizing_
            - fabric_dispatched_ / fabric_dispatched_detail_
            - fabric_stock_by_contract_ / fabric_stock_by_quality_
          Deliberately EXCLUDES sets_sized_detail_/sets_sized_beams_/
          warp_sizing_outbound_/beams_issued_to_looms_ - those are
          beam/sizing-named, not literally fabric_*/yarn_* by filename,
          and weren't asked for. Also excludes excel_update_/
          erp_efficiency_ (production-adjacent but not fabric/yarn).
          Ask before widening further rather than assuming more is
          wanted.

          fabric_stock_by_contract_/fabric_stock_by_quality_ are
          CUMULATIVE-balance queries (WHERE TD <= :report_date, see
          13_daily_production_email.py's own comments) - re-exporting
          them for a whole month re-computes each day's running stock
          balance as of that day fresh from current data, same as the
          daily script does one day at a time.

Usage   : python 27_month_end_reexport.py [YEAR MONTH]
          No arguments: re-exports the month that JUST ENDED relative to
          today (e.g. run on 2026-09-03, re-exports all of August 2026)
          - this is what the scheduled Task Scheduler job calls with no
          arguments. Pass YEAR MONTH explicitly to redo a specific past
          month on demand (e.g. `python 27_month_end_reexport.py 2026 7`
          to redo July 2026 specifically), independent of today's date.

          Every date in the target month gets re-queried and
          overwritten for all 9 files, including dates with 0 rows (so
          a date that WAS deleted/corrected-to-nothing in ERP correctly
          clears down to an empty file too, rather than leaving stale
          old data behind) - this is a full, unconditional re-export,
          not a presence-based backfill like the daily script's own
          gap-filling logic.

Config  : Same report_config.ini as 13_daily_production_email.py (DB
          connection + output folder) - loaded by importing that
          script's own functions/SQL/columns directly rather than
          duplicating them, so there's exactly one place each query and
          writer function lives.
"""

import sys
import calendar
import importlib.util
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location(
    "daily_report", SCRIPT_DIR / "13_daily_production_email.py"
)
daily_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily_report)

LOG_PATH = SCRIPT_DIR / "month_end_reexport_log.txt"

# (filename prefix, SQL, write function) - one entry per file this script
# covers. Reuses 13_daily_production_email.py's own SQL/write functions
# directly - see that script for how each one is sourced/verified.
REEXPORT_TARGETS = [
    ("loom_quality_history_with_contract_", daily_report.DETAIL_SQL, daily_report.write_detail_csv),
    ("yarn_issue_actual_", daily_report.YARN_SQL, daily_report.write_yarn_csv),
    ("yarn_received_", daily_report.YARN_RECEIVED_SQL, daily_report.write_yarn_received_csv),
    ("yarn_received_detail_", daily_report.YARN_RECEIVED_DETAIL_SQL, daily_report.write_yarn_received_detail_csv),
    ("yarn_to_sizing_", daily_report.YARN_TO_SIZING_SQL, daily_report.write_yarn_to_sizing_csv),
    ("fabric_dispatched_", daily_report.FABRIC_DISPATCHED_SQL, daily_report.write_fabric_dispatched_csv),
    ("fabric_dispatched_detail_", daily_report.FABRIC_DISPATCHED_DETAIL_SQL, daily_report.write_fabric_dispatched_detail_csv),
    ("fabric_stock_by_contract_", daily_report.FABRIC_STOCK_BY_CONTRACT_SQL, daily_report.write_fabric_stock_by_contract_csv),
    ("fabric_stock_by_quality_", daily_report.FABRIC_STOCK_BY_QUALITY_SQL, daily_report.write_fabric_stock_by_quality_csv),
]


def month_just_ended(today):
    """(year, month) of the month immediately before today's month -
    e.g. today=2026-09-03 -> (2026, 8)."""
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    return last_of_prev_month.year, last_of_prev_month.month


def main():
    if len(sys.argv) >= 3:
        year, month = int(sys.argv[1]), int(sys.argv[2])
    else:
        year, month = month_just_ended(date.today())

    n_days = calendar.monthrange(year, month)[1]
    run_stamp = date.today().isoformat()
    log_lines = [
        f"=== Month-end re-export run {run_stamp}: target {year}-{month:02d} "
        f"({n_days} days x {len(REEXPORT_TARGETS)} files) ==="
    ]

    try:
        cfg = daily_report.load_config()
        conn = daily_report.connect(cfg)
        try:
            cursor = conn.cursor()
            out_folder = Path(cfg.get("output", "folder"))
            out_folder.mkdir(parents=True, exist_ok=True)

            file_totals = {prefix: 0 for prefix, _, _ in REEXPORT_TARGETS}

            for day_num in range(1, n_days + 1):
                d = date(year, month, day_num)
                day_summary = []
                for prefix, sql, writer in REEXPORT_TARGETS:
                    rows = daily_report.fetch_rows(cursor, sql, report_date=d)
                    out_path = out_folder / f"{prefix}{d.isoformat()}.csv"
                    writer(rows, out_path)
                    file_totals[prefix] += len(rows)
                    day_summary.append(f"{prefix.rstrip('_')}={len(rows)}")
                log_lines.append(f"  {d.isoformat()}: " + ", ".join(day_summary))

            log_lines.append(f"Done - {n_days} dates re-exported across {len(REEXPORT_TARGETS)} files each.")
            for prefix, total in file_totals.items():
                log_lines.append(f"  {prefix} total rows across month: {total}")
        finally:
            conn.close()
    except Exception:
        import traceback
        log_lines.append("ERROR - run failed:")
        log_lines.append(traceback.format_exc())
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(log_lines) + "\n")
        print("\n".join(log_lines))
        sys.exit(1)

    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")
    print("\n".join(log_lines))


if __name__ == "__main__":
    main()
