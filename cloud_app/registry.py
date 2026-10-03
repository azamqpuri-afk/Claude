"""
The cloud's list of mills. One small SQLite file (cloud.db) next to the server.

Each mill has: an id (used in the URL, e.g. /sitara/), a display name, a secret upload token (only the mill's
agent knows it; we store its hash), a dashboard password for people who log in to view (hash stored), whether it
is enabled, and when it last uploaded. Turning a mill off here blocks both uploading and viewing at once - this is
how Azam Puri controls access centrally, instead of a licence file on the mill's server.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _hash(value):
    return hashlib.sha256(("smart-layer-cloud:" + value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    def __init__(self, path):
        self.path = Path(path)
        self.con = sqlite3.connect(self.path, check_same_thread=False)
        self.con.execute("""CREATE TABLE IF NOT EXISTS mills (
            id TEXT PRIMARY KEY, name TEXT, token_hash TEXT, view_hash TEXT,
            enabled INTEGER DEFAULT 1, created TEXT, last_upload TEXT)""")
        self.con.commit()

    def add(self, name, mill_id=None):
        mill_id = (mill_id or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))[:40] or "mill"
        if self.get(mill_id):
            raise SystemExit(f"A mill with id '{mill_id}' already exists.")
        token = secrets.token_urlsafe(24)
        view = secrets.token_urlsafe(9)
        self.con.execute("INSERT INTO mills (id, name, token_hash, view_hash, created) VALUES (?,?,?,?,?)",
                         (mill_id, name, _hash(token), _hash(view), _now()))
        self.con.commit()
        return {"id": mill_id, "name": name, "token": token, "view_password": view}

    def get(self, mill_id):
        r = self.con.execute("SELECT id, name, enabled, last_upload FROM mills WHERE id=?", (mill_id,)).fetchone()
        return {"id": r[0], "name": r[1], "enabled": bool(r[2]), "last_upload": r[3]} if r else None

    def all(self):
        return [{"id": r[0], "name": r[1], "enabled": bool(r[2]), "last_upload": r[3]}
                for r in self.con.execute("SELECT id, name, enabled, last_upload FROM mills ORDER BY name")]

    def set_enabled(self, mill_id, on):
        self.con.execute("UPDATE mills SET enabled=? WHERE id=?", (1 if on else 0, mill_id))
        self.con.commit()

    def reset_view_password(self, mill_id):
        view = secrets.token_urlsafe(9)
        self.con.execute("UPDATE mills SET view_hash=? WHERE id=?", (_hash(view), mill_id))
        self.con.commit()
        return view

    def check_token(self, mill_id, token):
        r = self.con.execute("SELECT token_hash, enabled FROM mills WHERE id=?", (mill_id,)).fetchone()
        return bool(r) and bool(r[1]) and hmac.compare_digest(r[0], _hash(token or ""))

    def check_view(self, mill_id, password):
        r = self.con.execute("SELECT view_hash, enabled FROM mills WHERE id=?", (mill_id,)).fetchone()
        return bool(r) and bool(r[1]) and hmac.compare_digest(r[0], _hash(password or ""))

    def mark_upload(self, mill_id):
        self.con.execute("UPDATE mills SET last_upload=? WHERE id=?", (_now(), mill_id))
        self.con.commit()
