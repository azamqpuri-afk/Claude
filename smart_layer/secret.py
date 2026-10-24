"""
Protection for the packaged product.

Two different jobs, two different keys:

1. Mapping files (the ERP SQL - your knowledge).  Shipped encrypted as <name>.map and readable only by the
   built program, which carries the mapping key baked in at build time (smart_layer/_buildkey.py, written from a
   GitHub secret, never in the repository).  On your own computer, with no baked key, the program reads the plain
   mappings/*.toml as before, so development and troubleshooting stay easy.

2. The ERP password in .env.  Sealed on the mill's own server with Windows DPAPI (CryptProtectData): only that
   Windows account on that machine can read it back, so a copied .env is useless elsewhere.  Enter the password
   once in setup; it is written sealed.  Off Windows this is a no-op (plain), so the demo and tests still run.
"""

import base64
import os
from pathlib import Path

MAP_SUFFIX = ".map"
DPAPI_PREFIX = "DPAPI:"


def _mapping_key():
    """The Fernet key the built product uses for mappings, or None when running from source (dev / troubleshooting)."""
    try:
        from ._buildkey import MAPPING_KEY        # written at build time, git-ignored, inside the compiled exe
        return MAPPING_KEY.encode() if isinstance(MAPPING_KEY, str) else MAPPING_KEY
    except Exception:
        env = os.environ.get("SMARTLAYER_MAPPING_KEY")      # used by the release packer
        return env.encode() if env else None


def have_mapping_key():
    return _mapping_key() is not None


def encrypt_mapping(text):
    from cryptography.fernet import Fernet
    key = _mapping_key()
    if not key:
        raise SystemExit("No mapping key: set SMARTLAYER_MAPPING_KEY (python tools/issue_license.py mapkey) first.")
    return Fernet(key).encrypt(text.encode("utf-8"))


def decrypt_mapping(token):
    from cryptography.fernet import Fernet
    key = _mapping_key()
    if not key:
        raise SystemExit("This copy cannot open a sealed mapping (no mapping key). Use the plain .toml mapping, "
                         "or the built product.")
    return Fernet(key).decrypt(token).decode("utf-8")


def read_mapping_text(path):
    """Mapping text whether it is sealed (<name>.map, preferred in the product) or plain (<name>.toml, in dev)."""
    path = Path(path)
    sealed = path if path.suffix == MAP_SUFFIX else path.with_suffix(MAP_SUFFIX)
    if sealed.exists():
        return decrypt_mapping(sealed.read_bytes())
    from .config import read_text_any
    return read_text_any(path if path.suffix != MAP_SUFFIX else path.with_suffix(".toml"))


# ---- ERP password: sealed to this Windows machine + account -------------------------------------------------

def _dpapi(data, unprotect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(b):
        buf = ctypes.create_string_buffer(b, len(b))
        return BLOB(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    # CRYPTPROTECT_LOCAL_MACHINE: seal to the MACHINE, not the logged-in account, so the scheduled tasks (which run
    # as SYSTEM) can read the password that the admin sealed during setup. Without this every task stops at startup.
    LOCAL_MACHINE = 0x4
    src, out = blob(data), BLOB()
    fn = ctypes.windll.crypt32.CryptUnprotectData if unprotect else ctypes.windll.crypt32.CryptProtectData
    args = ([ctypes.byref(src), None, None, None, None, LOCAL_MACHINE, ctypes.byref(out)] if unprotect
            else [ctypes.byref(src), u"Smart Layer ERP password", None, None, None, LOCAL_MACHINE, ctypes.byref(out)])
    if not fn(*args):
        raise OSError("Windows could not " + ("read" if unprotect else "seal") + " the sealed password "
                      "(a copy from another computer cannot be read here).")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def seal_secret(text):
    """Seal a secret for storage in .env. On Windows: DPAPI, machine+account bound. Elsewhere: plain (demo/tests)."""
    if os.name != "nt" or not text:
        return text
    return DPAPI_PREFIX + base64.b64encode(_dpapi(text.encode("utf-8"), unprotect=False)).decode("ascii")


def open_secret(value):
    """Reverse of seal_secret. Plain values pass through, so old .env files keep working."""
    if not isinstance(value, str) or not value.startswith(DPAPI_PREFIX):
        return value
    return _dpapi(base64.b64decode(value[len(DPAPI_PREFIX):]), unprotect=True).decode("utf-8")
