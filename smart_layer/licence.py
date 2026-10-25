"""
Licences for Project Smart Layer by Azam Puri.

A licence is a short signed text (also saved as license.key next to config.toml):

    SLIC1.<payload>.<signature>        payload = {"mill", "machine", "issued", "expires", "id"}

It is signed with Azam Puri's private key (tools/issue_license.py, kept on his own computer). The product only
holds the public key (licence_key.pub), so a licence can be checked but not created or changed by anyone else.
It is tied to one server's machine ID ("*" = any server) and has an expiry date:

  valid      everything works
  expiring   works; warning on the dashboard and a phone reminder from 15 days before expiry
  grace      expired up to 7 days ago: still works, with a clear warning
  expired / invalid / missing / wrong server    sync, reports and the dashboard stop (the mill's data stays safe)

The bundled demo (demo/demo_erp.db) runs without a licence.
"""

import base64
import hashlib
import json
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

PREFIX = "SLIC1"
PUBLIC_KEY_FILE = Path(__file__).with_name("licence_key.pub")
WARN_DAYS = 15
GRACE_DAYS = 7
CONTACT = "Azam Puri"


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def machine_id():
    """A stable fingerprint of this computer (the same for every user account, including SYSTEM)."""
    raw = ""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            raw = ""
    else:
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if Path(p).exists():
                raw = Path(p).read_text().strip()
                break
    if not raw:
        raw = f"{uuid.getnode():x}-{platform.node()}"
    h = hashlib.sha256(f"smart-layer:{raw}".encode()).hexdigest().upper()[:16]
    return "-".join(h[i:i + 4] for i in range(0, 16, 4))


def public_key():
    if not PUBLIC_KEY_FILE.exists():
        return None
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    return load_pem_public_key(PUBLIC_KEY_FILE.read_bytes())


def sign(payload, private_key):
    """Used by tools/issue_license.py on the issuer's computer."""
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = private_key.sign(f"{PREFIX}.{body}".encode())
    return f"{PREFIX}.{body}.{_b64(sig)}"


@dataclass
class Status:
    state: str                       # valid | expiring | grace | expired | invalid | missing | wrong_machine | demo
    message: str
    mill: str = ""
    expires: str = ""
    days_left: int = 0
    licence_id: str = ""

    @property
    def allowed(self):
        return self.state in ("valid", "expiring", "grace", "demo")

    @property
    def warning(self):
        return self.state in ("expiring", "grace")


def check_text(text, today=None, key=None, machine=None):
    """Verify a licence text. Returns a Status (never raises for a bad licence)."""
    today = today or date.today()
    key = key or public_key()
    if key is None:
        return Status("invalid", "This copy has no licence public key - it was not built for sale. Contact " + CONTACT + ".")
    text = (text or "").strip()
    parts = text.split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        return Status("invalid", "The licence is not in the right format - paste the whole code you were sent.")
    try:
        key.verify(_unb64(parts[2]), f"{parts[0]}.{parts[1]}".encode())
        p = json.loads(_unb64(parts[1]))
    except Exception:
        return Status("invalid", f"The licence is not genuine or was changed. Contact {CONTACT} for a new one.")
    mill, exp, lid = p.get("mill", ""), p.get("expires", ""), p.get("id", "")
    want = p.get("machine", "")
    here = machine or machine_id()
    if want not in ("*", here):
        return Status("wrong_machine", f"This licence is for another server ({want}); this server is {here}. "
                                       f"Send this server's ID to {CONTACT} for a licence.", mill, exp, 0, lid)
    try:
        end = date.fromisoformat(exp)
    except ValueError:
        return Status("invalid", "The licence has no valid expiry date.", mill, exp, 0, lid)
    left = (end - today).days
    if left < -GRACE_DAYS:
        return Status("expired", f"The licence for {mill} ended on {end:%d-%b-%Y}. Contact {CONTACT} to renew; "
                                 f"your data is safe and everything works again as soon as a new licence is added.",
                      mill, exp, left, lid)
    if left < 0:
        return Status("grace", f"The licence for {mill} ended on {end:%d-%b-%Y} - {GRACE_DAYS + left} day(s) of grace left. "
                               f"Contact {CONTACT} to renew.", mill, exp, left, lid)
    if left <= WARN_DAYS:
        return Status("expiring", f"The licence for {mill} ends on {end:%d-%b-%Y} ({left} days). Contact {CONTACT} to renew.",
                      mill, exp, left, lid)
    return Status("valid", f"Licensed to {mill} until {end:%d-%b-%Y}.", mill, exp, left, lid)


def licence_path(cfg):
    return cfg.path(cfg["licence"].get("file", "license.key"))


DEMO_TOKEN = "Sitara-Weaving-demo-v1"


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# SHA-256 of the exact demo database that ships with the app (demo/demo_erp.db). Baked in so the check does not
# depend on a reference file being present (the compiled build may not carry one). Regenerating the demo DB changes
# this; the shipped file is fixed in git.
DEMO_SHA256 = "53da92ce009165aad183fc275eab9c6eff082b7294c0757c479fde570a29f2f7"
_demo_hash_cache = {}


def is_demo(cfg):
    """True ONLY for the exact fictional demo database that ships with the app. The configured ERP file must hash to
    DEMO_SHA256 AND carry the demo marker, AND the local store must not have been synced from a real ERP mapping -
    so a real ERP (even renamed), a hand-built marker file, or the demo ERP with the store pointed at real data all
    need a licence."""
    import sqlite3
    e = cfg["erp"]
    if e.get("driver") != "sqlite":
        return False
    p = cfg.path(str(e.get("path", "")))
    if not Path(p).exists():
        return False
    try:
        st = Path(p).stat()
        ck = (str(p), st.st_mtime, st.st_size)
        h = _demo_hash_cache.get(ck) or _demo_hash_cache.setdefault(ck, _sha256(p))
        if h != DEMO_SHA256:
            return False
    except OSError:
        return False
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            if con.execute("SELECT token FROM _smartlayer_demo").fetchone()[0] != DEMO_TOKEN:
                return False
        finally:
            con.close()
    except sqlite3.Error:
        return False
    # the dashboard reads the local STORE, not the ERP file: if that store was built from a real ERP mapping, this
    # is not a demo however the ERP path is set (blocks pointing the demo ERP at a real synced store).
    try:
        sp = cfg.path(str((cfg["store"] or {}).get("path", "")))
        if Path(sp).exists():
            sc = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
            try:
                row = sc.execute("SELECT value FROM state WHERE key = 'synced_mapping'").fetchone()
            finally:
                sc.close()
            if row:
                import json
                m = json.loads(row[0])
                if m and str(m) != "demo_sqlite":
                    return False
    except (sqlite3.Error, ValueError, OSError):
        pass
    return True


def status(cfg, today=None):
    if is_demo(cfg):
        return Status("demo", "Demo mill - no licence needed.")
    p = licence_path(cfg)
    if not p.exists():
        return Status("missing", f"No licence installed. This server's ID is {machine_id()} - send it to {CONTACT}, "
                                 f"then install the licence (dashboard > Licence, or: python -m smart_layer licence install <file>).")
    from .config import read_text_any
    return check_text(read_text_any(p), today)


def install(cfg, text, today=None):
    """Check a licence text and save it as license.key (the old one is kept as license.key.bak)."""
    from pathlib import Path as _P
    candidate = _P(text.strip())
    if len(text.strip()) < 260 and candidate.exists():          # a path to a licence file
        from .config import read_text_any
        text = read_text_any(candidate)
    st = check_text(text, today)
    if not st.allowed:
        return st
    p = licence_path(cfg)
    if p.exists():
        p.with_name(p.name + ".bak").write_bytes(p.read_bytes())
    p.write_text(text.strip() + "\n", encoding="ascii")
    return st


def require(cfg):
    """Stop with a clear message when the licence does not allow use."""
    st = status(cfg)
    if not st.allowed:
        raise SystemExit(f"Licence: {st.message}")
    if st.warning:
        print(f"Licence warning: {st.message}")
    return st


def new_licence(mill, machine, expires, private_key, issued=None):
    payload = {"mill": mill, "machine": machine, "expires": expires if isinstance(expires, str) else expires.isoformat(),
               "issued": (issued or date.today()).isoformat(), "id": uuid.uuid4().hex[:10]}
    return sign(payload, private_key)


def renewal_due(cfg, today=None):
    st = status(cfg, today)
    return st if st.warning or not st.allowed else None


__all__ = ["machine_id", "check_text", "status", "install", "require", "new_licence", "Status", "timedelta"]
