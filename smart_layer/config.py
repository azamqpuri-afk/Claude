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
    "reports": {"tracked_issue_days": 60, "reorder_cover_months": 2, "output_dir": "output"},
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


def load(path="config.toml"):
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run  python -m smart_layer setup  (or copy config.example.toml)")
    _load_dotenv(path.parent / ".env")
    try:
        data = tomllib.loads(read_text_any(path))
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{path.name} has a mistake: {e}. Tip: Windows paths need single quotes, e.g. 'C:\\oracle\\bin'")
    return Config(_merge(DEFAULTS, _expand(data)), path.parent)
