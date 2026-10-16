"""
ERP auto-profiling (pure Python, offline, run once at install).

The problem: Smart Layer's reports run on a fixed 20-entity canonical model, but every mill's ERP stores that data
in differently named tables and columns. Writing the mapping by hand is what makes reports "wrong until tuned".

This module reads any connected ERP's own catalog - every table, column, type and row count, with small samples -
entirely in Python, no LLM and no internet. From that it:

  * fingerprints the ERP: does its schema match a known ERP we already have a perfected mapping for? (a mill on a
    known ERP then needs zero tuning - we just load that profile and calibrate the per-mill bits from the data);
  * for an unknown ERP, scores candidate source columns for each canonical field by name + type similarity, so the
    installer can draft a mapping and flag only the few fields it is unsure about for a one-time human confirm.

Business meaning that is not written anywhere in the schema (account-code prefix schemes, efficiency formulas,
cone/bag packing) cannot be derived by any amount of scanning - that lives in the per-ERP profile. So this does the
~80% that is mechanical automatically and surfaces the ~20% that needs confirming, instead of guessing silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import schema


# --------------------------------------------------------------------------------------------------------------
# Reading a database's own catalog, in whatever dialect it speaks.
# --------------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Col:
    name: str
    type: str
    nullable: bool = True


@dataclass
class Table:
    name: str
    columns: list = field(default_factory=list)   # list[Col]
    row_count: int = 0
    is_view: bool = False

    @property
    def colnames(self):
        return [c.name for c in self.columns]


@dataclass
class DbSchema:
    driver: str
    tables: dict = field(default_factory=dict)     # upper(name) -> Table

    def get(self, name):
        return self.tables.get(name.upper())


def _count(con, name):
    try:
        row = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return -1                                  # unreadable (permissions, locked); keep the table, note it


def read_schema(con, driver):
    """Read a database's tables/views, their columns and row counts. Read-only; never touches the data's meaning."""
    driver = (driver or "sqlite").lower()
    if driver == "sqlite":
        return _read_sqlite(con)
    if driver == "oracle":
        return _read_oracle(con)
    if driver == "access":
        return _read_access(con)
    if driver in ("postgres", "mysql", "mssql"):
        return _read_information_schema(con, driver)
    raise ValueError(f"auto-profile does not support driver '{driver}' yet")


def _read_sqlite(con):
    db = DbSchema("sqlite")
    for name, kind in con.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'").fetchall():
        cols = [Col(r[1], (r[2] or "").upper(), not r[3]) for r in con.execute(f"PRAGMA table_info({name})").fetchall()]
        db.tables[name.upper()] = Table(name, cols, _count(con, name), kind == "view")
    return db


def _read_oracle(con):
    db = DbSchema("oracle")
    cur = con.cursor()
    views = {r[0] for r in cur.execute("SELECT view_name FROM user_views").fetchall()}
    cols_by_table = {}
    for tname, cname, dtype, nullable in cur.execute(
            "SELECT table_name, column_name, data_type, nullable FROM user_tab_columns ORDER BY table_name, column_id").fetchall():
        cols_by_table.setdefault(tname, []).append(Col(cname, (dtype or "").upper(), nullable == "Y"))
    for tname, cols in cols_by_table.items():
        db.tables[tname.upper()] = Table(tname, cols, _count(con, tname), tname in views)
    return db


def _read_access(con):
    db = DbSchema("access")
    cur = con.cursor()
    tables = [r.table_name for r in cur.tables(tableType="TABLE")] + [r.table_name for r in cur.tables(tableType="VIEW")]
    views = {r.table_name for r in cur.tables(tableType="VIEW")}
    for tname in tables:
        if tname.startswith("MSys"):
            continue
        cols = [Col(r.column_name, str(r.type_name).upper(), bool(r.nullable)) for r in cur.columns(table=tname)]
        db.tables[tname.upper()] = Table(tname, cols, _count(con, f"[{tname}]"), tname in views)
    return db


def _read_information_schema(con, driver):
    db = DbSchema(driver)
    rows = con.execute("""SELECT table_name, column_name, data_type, is_nullable
                          FROM information_schema.columns
                          WHERE table_schema NOT IN ('pg_catalog','information_schema','sys','mysql','performance_schema')
                          ORDER BY table_name, ordinal_position""").fetchall()
    cols_by_table = {}
    for tname, cname, dtype, nullable in rows:
        cols_by_table.setdefault(tname, []).append(Col(cname, (dtype or "").upper(), str(nullable).upper() in ("YES", "1", "TRUE")))
    for tname, cols in cols_by_table.items():
        db.tables[tname.upper()] = Table(tname, cols, _count(con, tname))
    return db


# --------------------------------------------------------------------------------------------------------------
# Fingerprinting: is this schema a known ERP we already have a perfected mapping for?
# A profile is recognised by signature tables that are distinctive to that ERP product. Match = (found / total).
# --------------------------------------------------------------------------------------------------------------

PROFILES = {
    # Dost Textiles' weaving ERP (schema DOSTEX) - the mapping in mappings/dost_textiles_oracle.toml.
    "dost_textiles_oracle": {
        "label": "Dost-style weaving ERP (Oracle, DOSTEX)",
        "mapping": "dost_textiles_oracle.toml",
        "signature": ["WYTM", "WYTD", "WPRDTM", "WPRDTD", "WYBEAMS", "ACH", "ACL", "CONTM"],
    },
    # The bundled demo ERP ("MillPro") - lets the auto-profiler be exercised end-to-end offline.
    "demo_sqlite": {
        "label": "MillPro demo ERP (SQLite)",
        "mapping": "demo_sqlite.toml",
        "signature": ["YRN_TXN", "YRN_TXN_LINE", "PRD_SHIFT_LOG", "INV_ITEM_MASTER", "GL_VCH_DTL"],
    },
}


@dataclass
class Match:
    profile: str
    label: str
    mapping: str
    score: float                       # fraction of signature tables present
    found: list = field(default_factory=list)
    missing: list = field(default_factory=list)


def fingerprint(db, threshold=0.7):
    """Best-matching known ERP profile for this schema, or None. Score = signature tables found / total."""
    best = None
    for name, p in PROFILES.items():
        sig = [t.upper() for t in p["signature"]]
        found = [t for t in sig if t in db.tables]
        m = Match(name, p["label"], p["mapping"], len(found) / len(sig) if sig else 0.0,
                  found, [t for t in sig if t not in db.tables])
        if best is None or m.score > best.score:
            best = m
    if best and best.score >= threshold:
        return best
    return None


# --------------------------------------------------------------------------------------------------------------
# Heuristic column matching for an UNKNOWN ERP: score each source column against what a canonical field needs.
# This only drafts candidates (name + type overlap); it never asserts business meaning.
# --------------------------------------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9]+")


def _tokens(name):
    # split snake_case / camelCase / abbreviations into comparable word pieces
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    return set(_WORD.findall(s.lower()))


# hints: canonical column name -> extra words that commonly name the same thing in an ERP
_HINTS = {
    "amount": {"amt", "value", "val", "dr", "cr", "debit", "credit"},
    "meters": {"mtr", "mtrs", "qty", "length", "len"},
    "party_name": {"party", "customer", "supplier", "account", "ac", "an", "vendor"},
    "bags": {"bag", "qty"},
    "cones": {"cone"},
    "net_pay": {"net", "pay", "netpay"},
    "monthly_salary": {"salary", "basic", "wage", "pay"},
    "voucher_date": {"date", "dt", "td", "posted", "trans"},
    "loom_code": {"loom", "machine", "mach", "lcode"},
    "efficiency_pct": {"eff", "efficiency"},
}


def score_column(canon_col, src: Col):
    """0..1 - how well a source column could be a canonical field, by name-word overlap and type agreement."""
    cwords = _tokens(canon_col.name) | _HINTS.get(canon_col.name, set())
    swords = _tokens(src.name)
    if not swords:
        return 0.0
    overlap = len(cwords & swords) / max(len(cwords), 1)
    # light type agreement: numeric canon wants numeric source, date wants date
    t = src.type
    numeric = any(k in t for k in ("INT", "NUM", "DEC", "FLOAT", "DOUBLE", "REAL", "MONEY"))
    datey = any(k in t for k in ("DATE", "TIME"))
    want = canon_col.type
    bonus = 0.0
    if want in ("REAL",) and numeric:
        bonus = 0.15
    elif want in ("DATE", "DATETIME") and datey:
        bonus = 0.15
    return min(overlap + bonus, 1.0)


def suggest_columns(db, entity, top=3):
    """For one canonical entity, the best-scoring source columns for each of its fields (draft only)."""
    pool = [(t.name, c) for t in db.tables.values() for c in t.columns]
    out = {}
    for col in entity.columns:
        ranked = sorted(((round(score_column(col, c), 3), tbl, c.name) for tbl, c in pool), reverse=True)
        out[col.name] = [(tbl, cname, sc) for sc, tbl, cname in ranked[:top] if sc > 0]
    return out


# --------------------------------------------------------------------------------------------------------------
# One call that ties it together for the installer / a diagnostic command.
# --------------------------------------------------------------------------------------------------------------

def profile(con, driver):
    """Read the schema and identify the ERP. Returns a dict the CLI/installer can act on or print."""
    db = read_schema(con, driver)
    match = fingerprint(db)
    return {
        "driver": db.driver,
        "tables": len(db.tables),
        "columns": sum(len(t.columns) for t in db.tables.values()),
        "recognised": match.profile if match else None,
        "label": match.label if match else None,
        "mapping": match.mapping if match else None,
        "confidence": round(match.score, 2) if match else 0.0,
        "signature_found": match.found if match else [],
        "signature_missing": match.missing if match else [],
        "schema": db,
    }
