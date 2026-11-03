"""
Manual inputs the ERP does not hold - e.g. a sized set's yarn-rewind weight, gullay weight/cone count, or an
owner-set weft count per quality. Kept only in Smart Layer's own store (never written back to the ERP), append-
only so there is an edit history, and included in backups (it is just a table in the store DB).

A value is addressed by (scope, key, field): scope is the kind of thing ("set", "quality"), key its id (the set
number, the quality text), field the value name ("rewind_kg", "gullay_kg", "gullay_cones", "weft_count").
"""

from datetime import datetime


def set_value(con, scope, key, field, value, who="", note=""):
    """Record a value (append-only, so the previous value stays in the history). value None/'' deletes nothing -
    it records a blank, which get() then ignores. Returns the stored row's id."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = None
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    with con:
        cur = con.execute("INSERT INTO manual_inputs (scope, key, field, value, note, who, at) VALUES (?,?,?,?,?,?,?)",
                          (str(scope), str(key), str(field), v, str(note)[:200], str(who)[:60], now))
    return cur.lastrowid


def get(con, scope, key, field, default=None):
    """The latest value for (scope, key, field), or default."""
    row = con.execute("""SELECT value FROM manual_inputs WHERE scope=? AND key=? AND field=? AND value IS NOT NULL
                         ORDER BY id DESC LIMIT 1""", (str(scope), str(key), str(field))).fetchone()
    return row[0] if row else default


def latest(con, scope, field=None):
    """Latest value per key for a scope (optionally one field): {key: {field: value}}."""
    where = "WHERE scope=?" + (" AND field=?" if field else "")
    params = (str(scope),) + ((str(field),) if field else ())
    out = {}
    # rows newest first; first seen (key,field) wins
    for key, fld, value in con.execute(f"SELECT key, field, value FROM manual_inputs {where} AND value IS NOT NULL ORDER BY id DESC", params):
        out.setdefault(key, {}).setdefault(fld, value)
    return out


def history(con, scope, key, field=None, limit=100):
    """Full edit history for one key (newest first)."""
    where = "WHERE scope=? AND key=?" + (" AND field=?" if field else "")
    params = (str(scope), str(key)) + ((str(field),) if field else ())
    return [{"field": f, "value": v, "note": n, "who": w, "at": a}
            for f, v, n, w, a in con.execute(
                f"SELECT field, value, note, who, at FROM manual_inputs {where} ORDER BY id DESC LIMIT ?", params + (int(limit),))]
