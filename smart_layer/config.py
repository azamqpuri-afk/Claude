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
    "sync": {"history_days": 400, "live_days": 3,    # live_days: the short window the 5-minute live sync re-reads
             "stale_live_minutes": 30,               # dashboard shows the sync time red once the live sync is older
             "stale_daily_hours": 26,                # ... or the daily sync is older than this
             "stale_alert_minutes": 90},             # the dashboard pushes one ntfy alert if no sync for this long
    "notify": {"channels": ["console"]},
    "reports": {"tracked_issue_days": 90, "reorder_cover_months": 1, "output_dir": "",
                "retention_weeks": 26},           # saved report files older than this are pruned (0 = keep all)
    "sensor": {                                   # loom sensor duplicate-timings (sensor-swap) check - needs loom_stops
        "tol_seconds": 2,                         # two looms "twin" if they stop AND restart within this many seconds
        "gap_minutes": 20,                        # twin stops of one pair no more than this apart are one episode
        "min_twins": 5,                           # an episode needs this many twin stops to be confirmed
        "min_share": 0.60,                        # ... and the twins must be this share of the smaller loom's stops
        "max_simultaneous": 3,                    # stops hitting more looms than this at once = shed-wide event, skipped
        "open_stop_seconds": 5,                   # a restart within this of the latest restart = still-open stop, skipped
        "stale_hours": 2,                         # push once if the monitor's newest stop is older than this (gone blind)
        "sheds": {},                              # optional floor layout: [sensor.sheds.1] columns = [[even looms],[odd]]
        "layout": {},                             # optional explicit layout: "<loom>" = [shed, column, row]
    },
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
    "ask": {"model": "claude-opus-5-5", "effort": "high", "max_rows": 200, "max_steps": 12,
            "query_timeout_sec": 8,                       # abort any single SQL that runs longer than this
            "deny_functions": ["randomblob", "zeroblob"]},  # functions that can allocate huge blobs / hang the query
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
# The side file holds two levels:
#   [yarns."<code>"]                     a yarn's default warp/weft count, for every party
#   [parties."<name>".yarns."<code>"]    a count for one party + yarn, which wins over the default for that party
# Each entry also carries the audit fields changed / by / name (ignored when merging - only the counts are read).
COUNTS_SIDECAR = "closing_counts.local.toml"
_COUNT_KEYS = ("warp_closing_count", "weft_closing_count")
_AUDIT_KEYS = ("changed", "by", "name")


class SidecarError(ValueError):
    """The closing-counts side file is present but cannot be parsed - we refuse to overwrite it and lose data."""


def _toml_str(s):
    """A TOML basic string (double-quoted, properly escaped). JSON string syntax is a valid subset, so json.dumps
    gives us correct escaping for quotes, backslashes and control characters."""
    import json as _json
    return _json.dumps(str(s))


def _only_counts(entry):
    return {k: entry[k] for k in _COUNT_KEYS if k in entry and isinstance(entry.get(k), (int, float))}


def merge_closing_counts(data, extra):
    """Fold the side file into config [closing]: [yarns] become defaults in [closing.yarns]; each
    [parties."<name>".yarns] becomes a party override (appended after config's own, so the edited value wins).
    Only the two count keys are taken. Party names are matched literally (re.escape), not as a regex."""
    closing = data.setdefault("closing", {})
    yarns = closing.setdefault("yarns", {})
    for code, vals in (extra.get("yarns") or {}).items():
        if isinstance(vals, dict):
            yarns.setdefault(str(code), {}).update(_only_counts(vals))
    parties = closing.setdefault("parties", [])
    for name, pv in (extra.get("parties") or {}).items():
        pyarns = (pv or {}).get("yarns") or {}
        clean = {str(c): _only_counts(v) for c, v in pyarns.items() if isinstance(v, dict) and _only_counts(v)}
        if not clean:
            continue
        match = "^" + re.escape(str(name)) + "$"      # the FULL party name, not a substring (anchored, case-insensitive)
        entry = next((p for p in parties if p.get("_sidecar") and p.get("match") == match), None)
        if entry is None:
            entry = {"match": match, "yarns": {}, "_sidecar": True}
            parties.append(entry)
        for code, vals in clean.items():
            entry["yarns"].setdefault(code, {}).update(vals)


def read_closing_sidecar(config_dir):
    """Parse the side file. Returns {} when absent; raises SidecarError when present but broken (so a caller can
    warn and refuse to overwrite it rather than silently wiping saved counts)."""
    side = Path(config_dir) / COUNTS_SIDECAR
    if not side.exists():
        return {}
    try:
        return tomllib.loads(read_text_any(side))
    except tomllib.TOMLDecodeError as e:
        raise SidecarError(f"{COUNTS_SIDECAR} is not valid ({e}); fix or delete it") from None


def _fmt_count(v):
    f = float(v)
    return str(int(f)) if f == int(f) else repr(f)


def _apply_edits(bucket, counts, who, when, current):
    """Set the changed counts on one level's {code: entry} bucket. A value equal to the current effective value is
    NOT written (so an unchanged row never freezes config.toml). Returns the list of codes actually changed."""
    saved = []
    for code, vals in (counts or {}).items():
        code, vals = str(code), (vals or {})
        entry = dict(bucket.get(code, {}))
        touched = False
        for k in _COUNT_KEYS:
            raw = vals.get(k)
            if raw in (None, "", "-"):
                continue
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            if not 0 < num <= 500:                    # a closing count is a yarn count; outside this is a typo
                continue
            cur = (current or {}).get(code, {}).get(k)
            if cur is not None and float(cur) == num:
                continue                              # same as the effective config value - nothing to save
            if entry.get(k) != num:
                touched = True
            entry[k] = num
        if touched:
            entry["changed"] = when
            if who:
                entry["by"] = who
            if vals.get("yarn_name"):
                entry["name"] = str(vals["yarn_name"])[:40]
            bucket[code] = entry
            saved.append(code)
    return saved


def _write_entry(lines, header, entry):
    lines.append(header)
    for k in _COUNT_KEYS:
        if k in entry:
            lines.append(f"{k} = {_fmt_count(entry[k])}")
    for k in _AUDIT_KEYS:
        if entry.get(k):
            lines.append(f"{k} = {_toml_str(entry[k])}")
    lines.append("")


def save_closing_counts(config_dir, counts, who="", party="", scope="yarn", clear=(), current=None, when=None):
    """Save edited closing counts to the side file next to config.toml, with the date and who changed them.
    scope "yarn" writes [yarns] defaults; scope "party" writes [parties."<party>".yarns] for the named party.
    `clear` removes party counts (scope "party") so they fall back to the yarn default. Only changed rows are
    written; config.toml is never touched. Returns {ok, saved, cleared, scope, party, file, when, who} or, when the
    side file is present but unreadable, {ok: False, error} without overwriting it."""
    from datetime import date as _date
    when = when or _date.today().isoformat()
    side = Path(config_dir) / COUNTS_SIDECAR
    try:
        existing = read_closing_sidecar(config_dir)
    except SidecarError as e:
        return {"ok": False, "error": str(e)}         # do NOT overwrite a file we could not read - preserve the data
    yarns = {str(c): dict(v) for c, v in (existing.get("yarns") or {}).items() if isinstance(v, dict)}
    parties = {}
    for pname, pv in (existing.get("parties") or {}).items():
        py = {str(c): dict(v) for c, v in ((pv or {}).get("yarns") or {}).items() if isinstance(v, dict)}
        if py:
            parties[str(pname)] = {"yarns": py}
    saved, cleared = [], []
    if scope == "party":
        if not str(party).strip():
            return {"ok": False, "error": "no party selected for a party-specific save"}
        bucket = parties.setdefault(str(party), {"yarns": {}})["yarns"]
        saved = _apply_edits(bucket, counts, who, when, current)
        for code in (clear or []):
            if str(code) in bucket:
                del bucket[str(code)]
                cleared.append(str(code))
        if not bucket:
            parties.pop(str(party), None)
    else:
        saved = _apply_edits(yarns, counts, who, when, current)
    lines = ["# Closing counts saved from the Smart Layer dashboard. Safe to read, edit or delete.",
             "# [yarns.\"<code>\"]  sets a yarn's default warp/weft count for every party.",
             "# [parties.\"<name>\".yarns.\"<code>\"]  sets a count for one party + yarn (it wins for that party).", ""]
    for code in sorted(yarns, key=lambda c: (yarns[c].get("name") or c)):
        _write_entry(lines, f'[yarns.{_toml_str(code)}]', yarns[code])
    for pname in sorted(parties):
        for code in sorted(parties[pname]["yarns"]):
            _write_entry(lines, f'[parties.{_toml_str(pname)}.yarns.{_toml_str(code)}]', parties[pname]["yarns"][code])
    side.write_text("\n".join(lines), encoding="utf-8")
    return {"ok": True, "saved": saved, "cleared": cleared, "scope": scope, "party": party,
            "file": side.name, "when": when, "who": who}


class Config:
    def __init__(self, data, base_dir, source_path=None):
        self.data = data
        self.base_dir = Path(base_dir)
        self.source_path = Path(source_path) if source_path else None   # the config.toml this was loaded from

    def __getitem__(self, section):
        return self.data.get(section, {})

    def path(self, value):
        """A path from the config, relative to the config file's folder."""
        p = Path(value)
        return p if p.is_absolute() else self.base_dir / p

    def output_dir(self):
        """Where saved reports (PDF/CSV, the dashboard, the Reports page's files) are written. If [reports]
        output_dir is set, that path is used. Otherwise a writable per-machine default: on Windows
        C:\\ProgramData\\Smart Layer\\output (Program Files is read-only and the tasks run as a normal user or
        SYSTEM), elsewhere 'output' beside config.toml. Pointing output_dir at a synced OneDrive / Drive-for-desktop
        folder is how a mill gets its reports in the cloud - Smart Layer itself stays local-only, no cloud API."""
        value = (self["reports"].get("output_dir") or "").strip()
        if value:
            return self.path(value)
        if os.name == "nt":
            base = os.environ.get("ProgramData", r"C:\ProgramData")
            return Path(base) / "Smart Layer" / "output"
        return self.base_dir / "output"


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
    try:                                           # closing counts the owner edited in the dashboard, if any
        extra = read_closing_sidecar(path.parent)
        if extra:
            merge_closing_counts(merged, extra)
    except SidecarError as e:                      # present but broken: warn loudly, don't crash, don't lose it
        import sys
        print(f"WARNING: {e}. Edited closing counts are being ignored until it is fixed.", file=sys.stderr)
    return Config(merged, path.parent, source_path=path)
