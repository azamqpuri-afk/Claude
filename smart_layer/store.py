"""
Local store: a SQLite copy of the standard entities, refreshed from the ERP.

Reports, audits and "ask the ERP" read only this copy, so they never load the
live ERP and never depend on its SQL dialect. Claude's queries run here too,
on a read-only connection.

Sync: entities with a date column are re-pulled for a rolling window
(sync.history_days), replacing what was there, so corrections keyed later in
the ERP (backdated edits) are picked up. Other entities are replaced whole.
"""

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import schema

SQL_TYPES = {"TEXT": "TEXT", "REAL": "REAL", "DATE": "TEXT", "DATETIME": "TEXT"}


def open_store(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)            # wait, don't error, if another process is writing
    con.execute("PRAGMA journal_mode=WAL")             # readers (the dashboard) never block the 5-minute sync
    con.execute("PRAGMA busy_timeout=30000")
    create(con)
    return con


def open_read_only(path):
    """Read-only connection that also refuses anything except reading (for Claude's SQL)."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, timeout=30)
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorizer(action, *_):
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    con.set_authorizer(authorizer)
    return con


def create(con):
    for e in schema.ENTITIES:
        cols = ", ".join(f"{c.name} {SQL_TYPES[c.type]}" for c in e.columns)
        con.execute(f"CREATE TABLE IF NOT EXISTS {e.name} ({cols})")
        have = [r[1] for r in con.execute(f"PRAGMA table_info({e.name})")]
        if have != e.column_names:                   # a newer version added columns: rebuild the table in schema order
            keep = [c for c in e.column_names if c in have]
            con.execute(f"ALTER TABLE {e.name} RENAME TO _old_{e.name}")
            con.execute(f"CREATE TABLE {e.name} ({cols})")
            if keep:
                con.execute(f"INSERT INTO {e.name} ({', '.join(keep)}) SELECT {', '.join(keep)} FROM _old_{e.name}")
            con.execute(f"DROP TABLE _old_{e.name}")
        if e.date_column:
            con.execute(f"CREATE INDEX IF NOT EXISTS ix_{e.name}_date ON {e.name} ({e.date_column})")
    for t, c in (("store_receipts", "item_code"), ("store_issues", "item_code"),
                 ("ledger_entries", "voucher_id"), ("ledger_entries", "account_code"),
                 ("loom_production", "loom_code"), ("loom_production", "contract_no"),
                 ("yarn_movements", "contract_no"), ("fabric_dispatches", "contract_no"),
                 ("beam_movements", "beam_no"), ("loom_monitor", "loom_code")):
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_{c} ON {t} ({c})")
    con.execute("CREATE TABLE IF NOT EXISTS sync_log (entity TEXT, synced_at TEXT, since TEXT, rows INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    # Month-end fabric stock, read straight from the ERP's own stock view as of each month-end (never recomputed).
    con.execute("CREATE TABLE IF NOT EXISTS fabric_stock_history (month TEXT PRIMARY KEY, meters REAL, captured_on TEXT)")
    con.commit()


FULL_HISTORY = ("yarn_movements", "fabric_dispatches", "beam_movements",     # contract closing needs each contract's whole life
                "loom_production",                                           # fabric stock = all-time woven - all-time dispatched; a window makes old contracts show negative
                "store_receipts",                                            # the reorder list shows each item's LAST purchase party/rate - slow-moving spares were often last bought > a year ago
                "ledger_entries",                                            # party ledgers show the opening (old-ERP) balance and full running balance
                "employee_loans")                                            # a loan given years ago can still be open; its balance = amount - all-time salary deductions

# The 5-minute live refresh touches ONLY these fast-changing, window-safe entities. Everything else (ledgers and
# other entities that carry an all-time "opening" row, attendance, the month-end fabric history) is left to the
# daily full sync: windowing an entity whose query also returns opening rows would re-insert those opening rows
# every run (they fall outside the delete window), duplicating balances. Snapshots here are replaced wholesale.
LIVE_ENTITIES = ("loom_production", "loom_monitor", "gate_passes", "stock_balances",
                 "store_issues", "store_receipts", "yarn_stock", "fabric_stock")


def sync(con, erp, history_days, entities=None, log=print, full_history=FULL_HISTORY, fabric_history=True):
    window = date.today() - timedelta(days=int(history_days))
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    result, failed = {}, []
    for e in schema.ENTITIES:
        if entities and e.name not in entities:
            continue
        if e.name not in erp.mapping.entities:
            log(f"  {e.name:<17} skipped (not in mapping)")
            continue
        since = date(1900, 1, 1) if e.name in full_history else window
        try:
            rows = list(erp.fetch(e.name, since))
        except Exception as exc:                          # one bad mapping must not lose every other entity's data
            log(f"  {e.name:<17} FAILED ({str(exc).splitlines()[0][:80]}) - kept the previous data")
            result[e.name] = None
            failed.append(e.name)
            continue
        with con:
            if e.date_column:
                con.execute(f"DELETE FROM {e.name} WHERE {e.date_column} >= ?", (since.isoformat(),))
            else:
                con.execute(f"DELETE FROM {e.name}")
            marks = ", ".join("?" * len(e.columns))
            con.executemany(f"INSERT INTO {e.name} VALUES ({marks})", rows)
            con.execute("INSERT INTO sync_log VALUES (?, ?, ?, ?)",
                        (e.name, now, since.isoformat() if e.date_column else None, len(rows)))
        result[e.name] = len(rows)
        log(f"  {e.name:<17} {len(rows):>8,} rows")
    if fabric_history:
        _sync_fabric_history(con, erp, now, log, entities)
    if failed:
        log(f"  WARNING: {len(failed)} data set(s) did not sync and still hold OLD data: {', '.join(failed)}")
    result["_failed"] = failed
    return result


def _month_ends(n=12, today=None):
    import calendar
    today = today or date.today()
    out = []
    for i in range(n - 1, -1, -1):
        yy, mm = divmod(today.year * 12 + today.month - 1 - i, 12)
        mm += 1
        out.append(date(yy, mm, calendar.monthrange(yy, mm)[1]))
    return out


def _sync_fabric_history(con, erp, now, log, entities, months=12):
    """Read the ERP's own fabric-stock view as of each of the last `months` month-ends and store the total.
    The month-end figure comes straight from the ERP (bound as-of that date); we never recompute it."""
    if (entities and "fabric_stock" not in entities) or "fabric_stock" not in erp.mapping.entities:
        return
    mi = schema.BY_NAME["fabric_stock"].column_names.index("meters")
    try:
        today = date.today()
        for me in _month_ends(months):
            as_of = min(me, today)                        # the current month shows stock as of today, not a future date
            total = sum((r[mi] or 0) for r in erp.fetch("fabric_stock", date(1900, 1, 1), as_of=as_of))
            with con:
                con.execute("INSERT OR REPLACE INTO fabric_stock_history VALUES (?, ?, ?)",
                            (me.strftime("%Y-%m"), round(total, 2), now))
    except Exception as exc:
        log(f"  fabric_stock_history FAILED ({str(exc).splitlines()[0][:80]})")


def get_state(con, key, default=None):
    row = con.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(con, key, value):
    with con:
        con.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))
