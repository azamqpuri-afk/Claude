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
    "sync": {"history_days": 400},
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
        "dismiss": [],                    # [{match = "regex", why = "normal practice ..."}]
    },
    "closing": {},                        # the mill's closing method - see smart_layer/textile/method.py
    "pilferage": {},                      # thresholds - see smart_layer/textile/pilferage.py
    "serve": {"host": "127.0.0.1", "port": 8080},
    "ask": {"model": "claude-opus-5-5", "effort": "high", "max_rows": 200, "max_steps": 12},
}

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


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
        raise FileNotFoundError(f"{path} not found - copy config.example.toml to config.toml and fill it in")
    _load_dotenv(path.parent / ".env")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Config(_merge(DEFAULTS, _expand(data)), path.parent)
