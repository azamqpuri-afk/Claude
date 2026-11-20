"""
Reliable push outbox (item B1).

Alerts used to be marked "already pushed" BEFORE they were sent: one failed send (a DNS blip on 20-Nov) stopped the
loop and every alert after it was silently lost - a morning summary, night sensor, zero-stock, weekly beams and the
urgent audit pushes all vanished. This outbox fixes that: a push is QUEUED first, SENT separately, and marked sent
only after the server confirms. A failed send stays pending and is retried with back-off; channels are isolated in
notify.send so one dead channel never blocks another. The dashboard can show what has failed in the last 24 h.

Generic: a small table in the local store. queue() is idempotent on a dedup key, so re-queuing the same alert (e.g.
the same audit finding every 5 minutes until it is delivered) never duplicates it.
"""

import json
from datetime import datetime, timedelta

from . import notify


def _ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS push_outbox (
        id INTEGER PRIMARY KEY, dedup_key TEXT UNIQUE, created_at TEXT, title TEXT, body TEXT,
        priority TEXT, click TEXT, filename TEXT, tags TEXT, channels TEXT, meta TEXT,
        status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, last_attempt TEXT, last_error TEXT, sent_at TEXT)""")


def queue(con, dedup_key, title, body, priority="default", click=None, filename=None, tags=None, meta=None):
    """Queue one push. Idempotent on dedup_key: if a row with this key is already pending or already sent, nothing is
    added (so the same finding queued every run is delivered exactly once). Returns True if a new row was added."""
    _ensure(con)
    row = con.execute("SELECT status FROM push_outbox WHERE dedup_key = ?", (dedup_key,)).fetchone()
    if row is not None:
        return False
    with con:
        con.execute("""INSERT INTO push_outbox (dedup_key, created_at, title, body, priority, click, filename, tags, meta)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (dedup_key, datetime.now().isoformat(timespec="seconds"), title, body, priority, click,
                     filename, tags, json.dumps(meta or {})))
    return True


def flush(con, cfg, now=None, max_attempts=8, backoff_minutes=5, limit=200):
    """Try to deliver every pending push whose back-off has elapsed. Marks a row sent only after notify.send confirms
    at least one channel; on failure it records the error and leaves the row pending (until max_attempts, then
    'failed'). Returns {sent:[meta,...], failed:[(key,err),...], pending:n}. Back-off: wait attempts*backoff_minutes
    between tries, so a long outage is retried a few times an hour, not every run."""
    _ensure(con)
    now = now or datetime.now()
    out = {"sent": [], "failed": [], "pending": 0}
    rows = con.execute("""SELECT id, dedup_key, title, body, priority, click, filename, tags, meta, attempts, last_attempt
                          FROM push_outbox WHERE status = 'pending' ORDER BY id LIMIT ?""", (limit,)).fetchall()
    for _id, key, title, body, priority, click, filename, tags, meta, attempts, last_attempt in rows:
        if last_attempt:                                      # respect the back-off window
            try:
                wait = timedelta(minutes=backoff_minutes * max(1, attempts))
                if datetime.fromisoformat(last_attempt) + wait > now:
                    out["pending"] += 1
                    continue
            except ValueError:
                pass
        try:
            notify.send(cfg, title, body, priority=priority or "default", click=click, tags=tags)
            with con:
                con.execute("UPDATE push_outbox SET status='sent', sent_at=?, last_attempt=?, attempts=attempts+1 WHERE id=?",
                            (now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), _id))
            try:
                out["sent"].append(json.loads(meta or "{}"))
            except ValueError:
                out["sent"].append({})
        except Exception as exc:
            err = str(exc).splitlines()[0][:300]
            na = (attempts or 0) + 1
            status = "failed" if na >= max_attempts else "pending"
            with con:
                con.execute("UPDATE push_outbox SET status=?, attempts=?, last_attempt=?, last_error=? WHERE id=?",
                            (status, na, now.isoformat(timespec="seconds"), err, _id))
            out["failed"].append((key, err))
            if status == "pending":
                out["pending"] += 1
    return out


def failed_recent(con, hours=24, now=None):
    """Pushes that are failing - for the dashboard 'failed pushes (24h)' panel. Returns the still-pending/failed rows
    that have at least one failed attempt in the window, newest first."""
    _ensure(con)
    now = now or datetime.now()
    since = (now - timedelta(hours=hours)).isoformat(timespec="seconds")
    rows = con.execute("""SELECT title, status, attempts, last_attempt, last_error FROM push_outbox
                          WHERE status IN ('pending','failed') AND attempts > 0 AND last_attempt >= ?
                          ORDER BY last_attempt DESC""", (since,)).fetchall()
    return [{"title": t, "status": s, "attempts": a, "last_attempt": la, "last_error": le}
            for t, s, a, la, le in rows]


def prune(con, days=14):
    """Drop sent rows older than `days` so the outbox does not grow forever (failed rows are kept for visibility)."""
    _ensure(con)
    cutoff = (datetime.now() - timedelta(days=int(days))).isoformat(timespec="seconds")
    with con:
        con.execute("DELETE FROM push_outbox WHERE status='sent' AND sent_at < ?", (cutoff,))
