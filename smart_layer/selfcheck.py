"""
Deep self-check: after a sync, prove the canonical data is sane before anyone trusts a report.

`check` (in cli) proves each mapping's SQL *runs* and returns the right columns - structural correctness.
This proves the *values* look right: rows actually synced, dates are current, the ledger balances, efficiencies
are in range, stock is not impossibly negative, key fields are not mostly empty. It is how "reports are wrong
until I tune them" becomes "the app tells me which mapping is off, the moment it is off" - no waiting to notice a
wrong number on a report. Every check is deterministic (no LLM), reads the local store only, and names the entity
and the mapping field to look at.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import schema

OK, INFO, WARN, BAD = "OK", "INFO", "WARN", "BAD"


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(con, table, where="", args=()):
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table} {where}", args).fetchone()[0]
    except Exception:
        return None


def _one(con, sql, args=()):
    try:
        row = con.execute(sql, args).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def run(con, cfg, as_of=None):
    """Return a list of findings: (severity, area, message). BAD/WARN mean a mapping very likely needs attention."""
    as_of = as_of or date.today()
    out = []
    tables = _tables(con)
    mapped = set()
    try:
        synced = {r[0] for r in con.execute("SELECT DISTINCT entity FROM sync_log")}
    except Exception:
        synced = set()

    # 1. every mapped entity actually produced rows
    for ent in schema.ENTITIES:
        if ent.name not in tables:
            continue
        n = _count(con, ent.name)
        if ent.name in synced:
            mapped.add(ent.name)
            if not n:
                out.append((WARN, ent.name, "synced but 0 rows - the mapping may point at the wrong table or filter"))

    # 2. date-windowed entities should have recent data (not stale / wrong date column)
    for ent in schema.ENTITIES:
        if ent.date_column and ent.name in mapped and _count(con, ent.name):
            latest = _one(con, f"SELECT MAX({ent.date_column}) FROM {ent.name}")
            if latest:
                try:
                    gap = (as_of - date.fromisoformat(str(latest)[:10])).days
                    if gap > 30:
                        out.append((INFO, ent.name, f"latest {ent.date_column} is {latest} ({gap} days old) - sync may be behind, or the date column is wrong"))
                except ValueError:
                    out.append((WARN, ent.name, f"{ent.date_column} is not a date ({latest!r}) - wrong column mapped"))

    # 3. ledger double-entry: debits should equal credits overall
    if "ledger_entries" in mapped:
        total = _one(con, "SELECT SUM(amount) FROM ledger_entries") or 0
        turnover = _one(con, "SELECT SUM(ABS(amount)) FROM ledger_entries") or 0
        if turnover and abs(total) > 0.01 * turnover:
            out.append((WARN, "ledger_entries", f"debits - credits = {total:,.0f} on {turnover:,.0f} turnover - the ledger does not balance (sign or a missing leg)"))

    # 4. account groups: every balance account should be classified into a known group
    if "accounts" in mapped:
        groups = {r[0] for r in con.execute("SELECT DISTINCT account_group FROM accounts")}
        unknown = groups - set(schema.ACCOUNT_GROUPS) - {None}
        if unknown:
            out.append((WARN, "accounts", f"account_group has values outside the standard set: {sorted(unknown)} - classification mapping is off"))
        money = _count(con, "accounts", "WHERE account_group IN ('cash','bank')")
        if money == 0:
            out.append((WARN, "accounts", "no account is classified as cash or bank - the account-code classification needs calibrating"))

    # 5. loom efficiency in a believable range (scoreboard formula / units)
    if "loom_monitor" in mapped and _count(con, "loom_monitor"):
        bad = _count(con, "loom_monitor", "WHERE efficiency_pct > 120 OR efficiency_pct < 0")
        if bad:
            out.append((WARN, "loom_monitor", f"{bad} readings have efficiency outside 0-120% - the efficiency formula or RPM handling is off"))

    # 6. production should name real looms, not a code that never maps
    if "loom_production" in mapped and "looms" in tables:
        orphan = _one(con, "SELECT COUNT(DISTINCT p.loom_code) FROM loom_production p LEFT JOIN looms l ON l.loom_code=p.loom_code WHERE l.loom_code IS NULL")
        if orphan:
            out.append((INFO, "loom_production", f"{orphan} loom codes in production are not in the looms master - a join or code format may differ"))

    # 7. stock should not be wildly negative (negative = issues keyed without receipts, or wrong opening)
    if "stock_balances" in mapped:
        neg = _count(con, "stock_balances", "WHERE qty < 0")
        if neg:
            out.append((INFO, "stock_balances", f"{neg} items show negative stock - usually a missing opening balance or receipt in the mapping"))

    # 8. key identifying fields should not be mostly empty (wrong column mapped)
    for ent_name, col in (("employees", "department"), ("parties", "party_name"), ("items", "item_name")):
        if ent_name in mapped and _count(con, ent_name):
            total = _count(con, ent_name)
            filled = _count(con, ent_name, f"WHERE {col} IS NOT NULL AND TRIM({col}) <> ''")
            if filled is not None and total and filled / total < 0.5:
                out.append((WARN, ent_name, f"{col} is empty for {total - filled} of {total} rows - likely the wrong column or a broken join"))

    if not out:
        out.append((OK, "all", "no anomalies found - the synced data looks consistent"))
    return out


def summary(findings):
    """One block of text for the CLI / install log."""
    order = {BAD: 0, WARN: 1, INFO: 2, OK: 3}
    lines = []
    for sev, area, msg in sorted(findings, key=lambda f: order.get(f[0], 9)):
        lines.append(f"  [{sev:<4}] {area:<16} {msg}")
    n_bad = sum(1 for f in findings if f[0] in (BAD, WARN))
    head = "Self-check: all clear." if not n_bad else f"Self-check: {n_bad} thing(s) to look at."
    return head + "\n" + "\n".join(lines)
