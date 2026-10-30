"""
Local store: a SQLite copy of the standard entities, refreshed from the ERP.

Reports, audits and "ask the ERP" read only this copy, so they never load the
live ERP and never depend on its SQL dialect. Claude's queries run here too,
on a read-only connection.

Sync: entities with a date column are re-pulled for a rolling window
(sync.history_days), replacing what was there, so corrections keyed later in
the ERP (backdated edits) are picked up. Other entities are replaced whole.
"""

import contextlib
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import schema


@contextlib.contextmanager
def sync_lock(data_dir, stale_sec=1800):
    """A cross-process guard so a manual 'Sync now' (dashboard process) and the scheduled sync (a separate process)
    never run at once. Yields True when the lock was taken (release on exit), False when another sync holds it. A
    lock older than stale_sec is treated as abandoned (a crashed run) and reclaimed."""
    lock = Path(data_dir) / "sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and (time.time() - lock.stat().st_mtime) >= stale_sec:
            lock.unlink()                                 # stale: a previous run died holding it
    except OSError:
        pass
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False
        return
    except OSError:
        yield True                                        # cannot lock (read-only fs etc.) - don't block the sync
        return
    try:
        os.write(fd, f"{os.getpid()} {datetime.now().isoformat(timespec='seconds')}".encode())
        os.close(fd)
    except OSError:
        pass
    try:
        yield True
    finally:
        try:
            lock.unlink()
        except OSError:
            pass

SQL_TYPES = {"TEXT": "TEXT", "REAL": "REAL", "DATE": "TEXT", "DATETIME": "TEXT"}


def open_store(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)            # wait, don't error, if another process is writing
    con.execute("PRAGMA journal_mode=WAL")             # readers (the dashboard) never block the 5-minute sync
    con.execute("PRAGMA busy_timeout=30000")
    create(con)
    return con


def open_read_only(path, deny_functions=(), timeout_sec=None):
    """Read-only connection that also refuses anything except reading (for Claude's SQL).

    deny_functions: SQL functions to block outright (e.g. randomblob/zeroblob, which can allocate huge blobs and
    hang or exhaust memory). timeout_sec: abort any query that runs longer than this, so one bad question cannot peg
    the server (via a progress handler - pure wall-clock, no thread)."""
    con = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True, timeout=30)
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
    deny = {str(f).lower() for f in (deny_functions or ())}

    def authorizer(action, arg1, arg2, *_):
        if action == sqlite3.SQLITE_FUNCTION and arg2 and str(arg2).lower() in deny:
            return sqlite3.SQLITE_DENY                 # a blocked function (e.g. randomblob) - refuse the whole query
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    con.set_authorizer(authorizer)
    if timeout_sec:
        import time
        deadline = time.monotonic() + float(timeout_sec)
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)   # abort a runaway query
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
                 ("beam_movements", "beam_no"), ("loom_monitor", "loom_code"),
                 ("loom_stops", "loom_code"), ("loom_stops", "stop_time")):
        con.execute(f"CREATE INDEX IF NOT EXISTS ix_{t}_{c} ON {t} ({c})")
    con.execute("CREATE TABLE IF NOT EXISTS sync_log (entity TEXT, synced_at TEXT, since TEXT, rows INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)")
    # Month-end fabric stock, read straight from the ERP's own stock view as of each month-end (never recomputed).
    con.execute("CREATE TABLE IF NOT EXISTS fabric_stock_history (month TEXT PRIMARY KEY, meters REAL, captured_on TEXT)")
    con.commit()


FULL_HISTORY = ("yarn_movements", "fabric_dispatches", "beam_movements",     # contract closing needs each contract's whole life
                "loom_production",                                           # fabric stock = all-time woven - all-time dispatched; a window makes old contracts show negative
                "store_receipts",                                            # the reorder list shows each item's LAST purchase party/rate - slow-moving spares were often last bought > a year ago
                "store_issues",                                              # the store room's "last issue" + the item issue-history drill-down need each item's whole issue trail, not just the window

                "ledger_entries",                                            # party ledgers show the opening (old-ERP) balance and full running balance
                "employee_loans")                                            # a loan given years ago can still be open; its balance = amount - all-time salary deductions

# The 5-minute live refresh touches ONLY these fast-changing, window-safe entities. Everything else (ledgers and
# other entities that carry an all-time "opening" row, attendance, the month-end fabric history) is left to the
# daily full sync: windowing an entity whose query also returns opening rows would re-insert those opening rows
# every run (they fall outside the delete window), duplicating balances. Snapshots here are replaced wholesale.
LIVE_ENTITIES = ("loom_production", "loom_monitor", "loom_stops", "gate_passes", "stock_balances",
                 "store_issues", "store_receipts", "yarn_stock", "fabric_stock")


# Entities whose history is capped SHORTER than the main window, no matter how large history_days is: the raw
# loom stop log is huge (~1M rows at 400 days) and the twin check only needs the last few days. The cap is
# min(history_days, N), so the live sync (small window) still pulls even less. Override N via [sync] loom_stops_days.
SHORT_HISTORY = {"loom_stops": 14}


def sync(con, erp, history_days, entities=None, log=print, full_history=FULL_HISTORY, fabric_history=True,
         short_history=None):
    window = date.today() - timedelta(days=int(history_days))
    short_history = SHORT_HISTORY if short_history is None else short_history
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    result, failed = {}, []
    for e in schema.ENTITIES:
        if entities is not None and e.name not in entities:   # None = every entity; [] = none (never "all")
            continue
        if e.name not in erp.mapping.entities:
            log(f"  {e.name:<17} skipped (not in mapping)")
            continue
        if e.name in full_history:
            since = date(1900, 1, 1)
        elif e.name in short_history:
            since = date.today() - timedelta(days=min(int(history_days), int(short_history[e.name])))
        else:
            since = window
        try:
            rows = list(erp.fetch(e.name, since))
        except Exception as exc:                          # one bad mapping must not lose every other entity's data
            log(f"  {e.name:<17} FAILED ({str(exc).splitlines()[0][:80]}) - kept the previous data")
            result[e.name] = None
            failed.append(e.name)
            continue
        if e.date_column:
            # a windowed sync only OWNS rows inside its window; drop any the query returned that fall before it
            # (e.g. all-time 'opening' rows) - the delete below only clears the window, so inserting them would
            # duplicate them on every run. The daily full-history sync (since = 1900) keeps everything.
            di = e.column_names.index(e.date_column)
            s = since.isoformat()
            rows = [r for r in rows if r[di] is None or str(r[di]) >= s]
        with con:
            if e.date_column:
                con.execute(f"DELETE FROM {e.name} WHERE {e.date_column} >= ?", (since.isoformat(),))
            else:
                con.execute(f"DELETE FROM {e.name}")
            marks = ", ".join("?" * len(e.columns))
            con.executemany(f"INSERT INTO {e.name} VALUES ({marks})", rows)
            # short-history entities (e.g. the huge raw loom stop log) are PRUNED to their cap: the windowed delete
            # above only clears the window, so rows older than the cap would otherwise pile up for ever.
            if e.date_column and e.name in short_history:
                con.execute(f"DELETE FROM {e.name} WHERE {e.date_column} < ?", (since.isoformat(),))
            con.execute("INSERT INTO sync_log VALUES (?, ?, ?, ?)",
                        (e.name, now, since.isoformat() if e.date_column else None, len(rows)))
        result[e.name] = len(rows)
        log(f"  {e.name:<17} {len(rows):>8,} rows")
    if fabric_history:
        _sync_fabric_history(con, erp, now, log, entities)
    set_state(con, "synced_mapping", getattr(erp, "mapping", None) and erp.mapping.name)   # for the demo-licence guard
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
