"""
26_prod_entry_snapshot_diff.py
Project : Dost Textiles - Production Entry form behavior observation
          (Phase 1, 2026-08-17 request)
Purpose : READ-ONLY. Runs on a schedule (Task Scheduler, every 20 min,
          08:00-20:00 daily). Each run takes a fresh full snapshot of
          DOSTEX.WPRDTM and DOSTEX.WPRDTD (the two tables confirmed this
          session to be what the Production Entry form actually writes to
          - see WV_PROD_V's view SQL and the INSERT_WPRDTD_SR_NO trigger
          in the PL/SQL atlas), diffs it against the previous run's
          snapshot, and - only if something actually changed - appends a
          human-readable summary block to a running markdown report.

          Nothing is ever inserted/updated/deleted. Every query here is a
          plain SELECT. The existing ERP application is never touched,
          locked, or interrupted - snapshots are ordinary read queries
          against tables the app already reads from constantly.

Key design choices:
  - Primary key for WPRDTM = TRANS_NO (no DB trigger enforces this - the
    form itself must be assigning it; treated as the natural key here
    and duplicate-checked every run in case that assumption is wrong).
  - Primary key for WPRDTD = TRANS_NO|UC|YC|SR_NO (matches exactly the
    scope of INSERT_WPRDTD_SR_NO's own MAX(sr_no)+1 WHERE clause).
  - Baseline snapshots are overwritten every run (not timestamped per
    run - the point is the DIFF, not an ever-growing pile of near-
    identical CSVs). The first run establishes a baseline with no diff.
  - The output report (production_entry_behavior_analysis.md) is
    APPEND-ONLY - never rewritten from scratch - so a history of
    observed entries accumulates across days, then gets copied to the
    Drive folder every run.

Output:
  - C:\\Users\\DBSERVER\\DostTextiles\\scripts\\prod_entry_watch\\
      last_wprdtm.csv, last_wprdtd.csv   (overwritten each run - baseline)
  - C:\\Users\\DBSERVER\\DostTextiles\\scripts\\prod_entry_watch\\
      watch_log.txt                       (rolling run log, append-only)
  - O:\\My Drive\\DostTextile_Reports\\
      production_entry_behavior_analysis.md  (append-only, human-readable)
"""

import configparser
import csv
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path

import oracledb

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "report_config.ini"
WATCH_DIR = SCRIPT_DIR / "prod_entry_watch"
WATCH_DIR.mkdir(exist_ok=True)

LAST_WPRDTM = WATCH_DIR / "last_wprdtm.csv"
LAST_WPRDTD = WATCH_DIR / "last_wprdtd.csv"
WATCH_LOG = WATCH_DIR / "watch_log.txt"
REPORT_LOCAL = WATCH_DIR / "production_entry_behavior_analysis.md"

WPRDTM_PK = ["TRANS_NO"]
WPRDTD_PK = ["TRANS_NO", "UC", "YC", "SR_NO"]


def load_config():
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    return cfg


def connect(cfg):
    client_lib_dir = cfg.get("oracle", "client_lib_dir", fallback="").strip()
    if client_lib_dir:
        oracledb.init_oracle_client(lib_dir=client_lib_dir)
    else:
        oracledb.init_oracle_client()
    return oracledb.connect(
        user=cfg.get("oracle", "user"),
        password=cfg.get("oracle", "password"),
        dsn=cfg.get("oracle", "dsn"),
    )


def fetch_table(cursor, table_name):
    cursor.execute(f"SELECT * FROM dostex.{table_name}")
    cols = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    return cols, rows


def pk_value(cols, row, pk_cols):
    idx = {c: i for i, c in enumerate(cols)}
    return "|".join(str(row[idx[p]]) for p in pk_cols)


def rows_to_dict(cols, rows, pk_cols):
    """PK -> {col: value} dict, plus a duplicate-PK count for sanity."""
    out = {}
    dupes = 0
    for row in rows:
        key = pk_value(cols, row, pk_cols)
        record = dict(zip(cols, row))
        if key in out:
            dupes += 1
        out[key] = record
    return out, dupes


def write_snapshot_csv(cols, rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for row in rows:
            w.writerow(["" if v is None else v for v in row])


def read_snapshot_csv(path, pk_cols):
    """Returns (cols, {pk: {col: value_as_string}}) or (None, {}) if the
    file doesn't exist yet (first run)."""
    if not path.exists():
        return None, {}
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        cols = next(reader)
        out = {}
        idx = {c: i for i, c in enumerate(cols)}
        for row in reader:
            key = "|".join(str(row[idx[p]]) for p in pk_cols)
            out[key] = dict(zip(cols, row))
    return cols, out


def fmt_val(v):
    if v is None or v == "":
        return None
    return str(v)


def diff_tables(prev, curr, cols):
    """prev/curr: {pk: {col: value}} (curr values are native types from
    Oracle, prev values are strings read back from CSV - compare as
    strings on both sides to avoid false positives from type formatting).
    Returns (new_rows: [(pk, {col: val})], changed_rows: [(pk, {col: (old,new)})])."""
    new_rows = []
    changed_rows = []
    for pk, curr_rec in curr.items():
        curr_str = {c: fmt_val(curr_rec.get(c)) for c in cols}
        if pk not in prev:
            non_null = {c: v for c, v in curr_str.items() if v is not None}
            new_rows.append((pk, non_null))
        else:
            prev_rec = prev[pk]
            changes = {}
            for c in cols:
                old_v = fmt_val(prev_rec.get(c))
                new_v = curr_str.get(c)
                if old_v != new_v:
                    changes[c] = (old_v, new_v)
            if changes:
                changed_rows.append((pk, changes))
    return new_rows, changed_rows


def format_report_block(run_stamp, table_label, pk_cols, new_rows, changed_rows):
    lines = []
    if not new_rows and not changed_rows:
        return lines
    lines.append(f"### {table_label} — {run_stamp}")
    lines.append("")
    if new_rows:
        lines.append(f"**{len(new_rows)} new row(s)** (key = {'/'.join(pk_cols)}):")
        lines.append("")
        for pk, fields in new_rows:
            lines.append(f"- `{pk}`")
            field_str = ", ".join(f"{c}={v}" for c, v in fields.items())
            lines.append(f"  - {field_str}")
        lines.append("")
    if changed_rows:
        lines.append(f"**{len(changed_rows)} changed row(s)**:")
        lines.append("")
        for pk, changes in changed_rows:
            lines.append(f"- `{pk}`")
            for c, (old_v, new_v) in changes.items():
                lines.append(f"  - `{c}`: {old_v!r} \u2192 {new_v!r}")
        lines.append("")
    return lines


def append_text(path, lines):
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")


def main():
    run_stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_lines = [f"=== Run {run_stamp} ==="]

    try:
        cfg = load_config()
        conn = connect(cfg)
        try:
            cursor = conn.cursor()

            report_blocks = []
            first_run = not LAST_WPRDTM.exists()

            for table_name, pk_cols, csv_path in (
                ("WPRDTM", WPRDTM_PK, LAST_WPRDTM),
                ("WPRDTD", WPRDTD_PK, LAST_WPRDTD),
            ):
                cols, rows = fetch_table(cursor, table_name)
                curr_dict, dupes = rows_to_dict(cols, rows, pk_cols)
                if dupes:
                    log_lines.append(
                        f"WARNING: {table_name} has {dupes} duplicate key(s) on "
                        f"{'/'.join(pk_cols)} - PK assumption may be wrong."
                    )

                prev_cols, prev_dict = read_snapshot_csv(csv_path, pk_cols)

                if prev_cols is not None:
                    new_rows, changed_rows = diff_tables(prev_dict, curr_dict, cols)
                    report_blocks.extend(
                        format_report_block(run_stamp, table_name, pk_cols, new_rows, changed_rows)
                    )
                    log_lines.append(
                        f"{table_name}: {len(rows)} rows total, "
                        f"{len(new_rows)} new, {len(changed_rows)} changed since last run"
                    )
                else:
                    log_lines.append(f"{table_name}: baseline established, {len(rows)} rows (first run)")

                write_snapshot_csv(cols, rows, csv_path)

            if report_blocks:
                if not REPORT_LOCAL.exists():
                    header = [
                        "# Production Entry Behavior Analysis",
                        "",
                        "Auto-generated, append-only. Every block below is a real, ",
                        "naturally-occurring change observed between two scheduled ",
                        "read-only snapshots (never triggered artificially) of ",
                        "`DOSTEX.WPRDTM`/`DOSTEX.WPRDTD` - the tables confirmed this ",
                        "session to be what the Production Entry form actually writes ",
                        "to (see `WV_PROD_V`'s view SQL and the `INSERT_WPRDTD_SR_NO` ",
                        "trigger in the PL/SQL atlas). Nothing here was written by ",
                        "this project - it's purely observed.",
                        "",
                        "---",
                        "",
                    ]
                    append_text(REPORT_LOCAL, header)
                append_text(REPORT_LOCAL, report_blocks)

                # Mirror to the shared Drive folder every time something new
                # was captured (not every run - no point re-syncing an
                # unchanged file).
                out_folder = Path(cfg.get("output", "folder"))
                out_folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPORT_LOCAL, out_folder / "production_entry_behavior_analysis.md")
                log_lines.append("Captured changes - report block appended and synced to Drive.")
            elif first_run:
                log_lines.append("First run - baseline only, nothing to report yet.")
            else:
                log_lines.append("No changes detected this run.")

        finally:
            conn.close()

    except Exception:
        log_lines.append("ERROR - run failed:")
        log_lines.append(traceback.format_exc())
        append_text(WATCH_LOG, log_lines)
        sys.exit(1)

    append_text(WATCH_LOG, log_lines)
    print("\n".join(log_lines))


if __name__ == "__main__":
    main()
