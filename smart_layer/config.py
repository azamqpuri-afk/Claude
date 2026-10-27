"""
Settings. One TOML file per installation (config.toml, never committed).
Secrets are not written in the file: use "${ENV_VAR}" and set the variable
(or put it in a .env file next to the config, also never committed).
"""

import os
import re
import tomllib
from pathlib import Path

DEFAULTS = {
    "company": {"name": "My Mill", "currency": "Rs"},
    "erp": {"driver": "sqlite"},
    "store": {"path": "data/smart_layer.db"},
    "sync": {"history_days": 400, "live_days": 3},   # live_days: the short window the 5-minute live sync re-reads
    "notify": {"channels": ["console"]},
    "reports": {"tracked_issue_days": 90, "reorder_cover_months": 1, "output_dir": "output"},
    "audit": {
        "large_payment": 500000,          # payments at or above this are listed (WATCH)
        "duplicate_payment_min": 5000,    # same party + same amount within 3 days
        "backdate_min_days": 7,           # never flag a voucher keyed sooner than this
        "rate_jump_ratio": 1.5,           # purchase rate vs last purchase of the same item
        "cheaper_elsewhere_ratio": 1.25,  # vs cheapest other supplier in the last 12 months
        "cheaper_elsewhere_min_extra": 1000,
        "repeat_issue_days": 2,           # same single part to the same machine again
        "rejection_pct": 15,
        "rejection_min_meters": 200,
        "idle_loom_min_days": 5,
        "loom_silent_days": 10,           # a loom silent this many days is an urgent (send-once) phone alert
        "trial_balance_tolerance": 10,    # rounding differences below this are not an error
        "dismiss": [],                    # [{match = "regex", why = "normal practice ..."}]
    },
    "closing": {},                        # the mill's closing method - see smart_layer/textile/method.py
    "pilferage": {},                      # thresholds - see smart_layer/textile/pilferage.py
    "beams": {},                          # see smart_layer/textile/beams.py
    "reconcile": {},                      # see smart_layer/reports/reconcile.py
    "licence": {},                        # file = "license.key" (next to config.toml)
    "sources": {},                        # extra databases, e.g. [sources.scoreboard] for a loom monitoring system
    "serve": {"host": "127.0.0.1", "port": 8080},
    "ask": {"model": "claude-opus-5-5", "effort": "high", "max_rows": 200, "max_steps": 12},
}

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def read_text_any(path):
    """Text of a file saved by any Windows tool: UTF-8 with or without BOM, or UTF-16 (PowerShell's default)."""
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _load_dotenv(path):
    if not path.exists():
        return
    for line in read_text_any(path).splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            if v.startswith("DPAPI:"):          # a secret sealed to this machine (see smart_layer/secret.py)
                from .secret import open_secret
                try:
                    v = open_secret(v)
                except OSError as e:
                    raise SystemExit(f"{k.strip()}: {e}  Re-run setup on this server to enter it again.")
            os.environ.setdefault(k.strip(), v)


def _expand(value):
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


# --- editable closing counts (saved from the dashboard, kept in a side file so the hand-written config.toml,
#     with all its comments, is never rewritten) -------------------------------------------------------------
COUNTS_SIDECAR = "closing_counts.local.toml"
_COUNT_KEYS = ("warp_closing_count", "weft_closing_count")


def merge_closing_counts(data, extra):
    """Fold the side file's [yarns."<code>"] closing counts into config [closing.yarns] (base defaults for every
    party). Only the two count keys are taken; a party-specific count in config.toml still wins for that party."""
    yarns = data.setdefault("closing", {}).setdefault("yarns", {})
    for code, vals in (extra.get("yarns") or {}).items():
        if not isinstance(vals, dict):
            continue
        entry = yarns.setdefault(str(code), {})
        for k in _COUNT_KEYS:
            if k in vals:
                entry[k] = vals[k]
    return {str(c): {k: v[k] for k in _COUNT_KEYS if k in v}
            for c, v in (extra.get("yarns") or {}).items() if isinstance(v, dict)}


def _fmt_count(v):
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def save_closing_counts(config_dir, counts, who="", party="", when=None):
    """Write edited warp/weft closing counts to the side file next to config.toml, with the date and who changed them.
    `counts` is {yarn code: {warp_closing_count, weft_closing_count, yarn_name}}; blank / out-of-range values are
    skipped. Returns {ok, saved:[codes], yarns:{...}, file, when, who}. Never touches config.toml itself."""
    from datetime import date as _date
    when = when or _date.today().isoformat()
    side = Path(config_dir) / COUNTS_SIDECAR
    existing = {}
    if side.exists():
        try:
            existing = tomllib.loads(read_text_any(side))
        except tomllib.TOMLDecodeError:
            existing = {}
    yarns = {str(k): dict(v) for k, v in (existing.get("yarns") or {}).items() if isinstance(v, dict)}
    meta = {str(k): dict(v) for k, v in (existing.get("meta") or {}).items() if isinstance(v, dict)}
    saved = []
    for code, vals in (counts or {}).items():
        code, vals = str(code), (vals or {})
        entry, touched = yarns.get(code, {}), False
        for k in _COUNT_KEYS:
            raw = vals.get(k)
            if raw in (None, "", "-"):
                continue
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            if not 0 < num <= 500:                    # a closing count is a yarn count; anything outside this is a typo
                continue
            if entry.get(k) != num:
                touched = True
            entry[k] = num
        if entry:
            yarns[code] = entry
        if touched:
            m = meta.get(code, {})
            m["changed"] = when
            if who:
                m["by"] = who
            if vals.get("yarn_name"):
                m["name"] = str(vals["yarn_name"])[:40]
            meta[code] = m
            saved.append(code)
    lines = ["# Closing counts saved from the Smart Layer dashboard. Safe to read, edit or delete.",
             "# They set the default warp/weft closing count per yarn, on top of config.toml [closing.yarns].",
             "# A party-specific count in config.toml still wins for that party.", ""]
    for code in sorted(yarns, key=lambda c: (meta.get(c, {}).get("name") or c)):
        m = meta.get(code, {})
        tail = [t for t in (m.get("name"),
                            ("changed " + m["changed"] + (" by " + m["by"] if m.get("by") else "")) if m.get("changed") else "")
                if t]
        lines.append(f'[yarns."{code}"]' + ("  # " + " — ".join(tail) if tail else ""))
        for k in _COUNT_KEYS:
            if k in yarns[code]:
                lines.append(f"{k} = {_fmt_count(yarns[code][k])}")
        lines.append("")
    if meta:                                          # keep the audit as data too, so a later save preserves it
        lines.append("[meta]")
        for code in sorted(meta):
            parts = ", ".join(f'{k} = "{val}"' for k, val in meta[code].items())
            lines.append(f'"{code}" = {{ {parts} }}')
        lines.append("")
    side.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "saved": saved, "yarns": {c: {k: yarns[c][k] for k in _COUNT_KEYS if k in yarns[c]} for c in yarns},
            "file": side.name, "when": when, "who": who}


class Config:
    def __init__(self, data, base_dir):
        self.data = data
        self.base_dir = Path(base_dir)

    def __getitem__(self, section):
        return self.data.get(section, {})

    def path(self, value):
        """A path from the config, relative to the config file's folder."""
        p = Path(value)
        return p if p.is_absolute() else self.base_dir / p


def load(path="config.toml", expand=True):
    """Load a config. expand=False is for UNTRUSTED configs (e.g. a tenant's uploaded config.toml on the cloud):
    it does NOT read a .env and does NOT fill in ${VARS}, so a tenant file can never pull a value out of the
    server's own environment (which could leak the cloud's API key or other secrets into the mill's dashboard)."""
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run  python -m smart_layer setup  (or copy config.example.toml)")
    if expand:
        _load_dotenv(path.parent / ".env")
    try:
        data = tomllib.loads(read_text_any(path))
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{path.name} has a mistake: {e}. Tip: Windows paths need single quotes, e.g. 'C:\\oracle\\bin'")
    merged = _merge(DEFAULTS, _expand(data) if expand else data)
    side = path.parent / COUNTS_SIDECAR            # closing counts the owner edited in the dashboard, if any
    if side.exists():
        try:
            merge_closing_counts(merged, tomllib.loads(read_text_any(side)))
        except tomllib.TOMLDecodeError:
            pass
    return Config(merged, path.parent)
