"""
ERP connector: reads the customer's ERP through a mapping file.

A mapping file (mappings/*.toml) holds one SELECT per standard entity, written
in the ERP's own SQL dialect, whose column aliases are the standard column
names from schema.py. That file is the only thing written per ERP.

Safety: only SELECT / WITH statements are accepted, one statement each, and
the session is put in read-only mode where the database supports it. Use a
read-only database user as well.
"""

import re
import sqlite3
import tomllib
from datetime import date, datetime
from pathlib import Path

from . import schema

PARAM = re.compile(r"(?<![:\w]):(since|today)\b")
READ_ONLY_START = re.compile(r"^\s*(--[^\n]*\n\s*|/\*.*?\*/\s*)*(select|with)\b", re.I | re.S)


class MappingError(Exception):
    pass


def check_select(sql):
    """Raise unless sql is a single read-only SELECT / WITH statement."""
    body = sql.strip().rstrip(";")
    if not READ_ONLY_START.match(body) or ";" in body:
        raise MappingError("only a single SELECT / WITH statement is allowed")
    return body


class Mapping:
    def __init__(self, path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            data = tomllib.load(f)
        meta = data.get("mapping", {})
        self.name = meta.get("name", self.path.stem)
        self.notes = meta.get("notes", "")
        self.entities = {}
        for name, spec in data.get("entities", {}).items():
            if name not in schema.BY_NAME:
                raise MappingError(f"{self.path.name}: unknown entity '{name}' (known: {', '.join(schema.BY_NAME)})")
            self.entities[name] = check_select(spec["sql"])

    def missing(self):
        return [e.name for e in schema.ENTITIES if e.name not in self.entities]


def connect(erp_cfg, base_dir="."):
    """Open a connection to the ERP database. Drivers are imported only when used."""
    driver = erp_cfg.get("driver", "sqlite").lower()
    if driver == "sqlite":
        p = Path(erp_cfg["path"])
        p = p if p.is_absolute() else Path(base_dir) / p
        if not p.exists():
            raise FileNotFoundError(p)
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True), "named"
    if driver == "oracle":
        import oracledb
        if erp_cfg.get("thick") or erp_cfg.get("client_lib_dir"):   # Oracle Client mode, needed for 11g and older
            oracledb.init_oracle_client(lib_dir=erp_cfg.get("client_lib_dir") or None)
        con = oracledb.connect(user=erp_cfg["user"], password=erp_cfg["password"], dsn=erp_cfg["dsn"])
        con.cursor().execute("SET TRANSACTION READ ONLY")
        return con, "named"
    if driver == "postgres":
        import psycopg
        con = psycopg.connect(erp_cfg["dsn"])
        con.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        return con, "pyformat"
    if driver == "mssql":
        import pyodbc
        return pyodbc.connect(erp_cfg["dsn"], readonly=True), "qmark"
    if driver == "mysql":
        import pymysql
        con = pymysql.connect(host=erp_cfg["host"], user=erp_cfg["user"], password=erp_cfg["password"],
                              database=erp_cfg["database"], port=int(erp_cfg.get("port", 3306)))
        con.cursor().execute("SET SESSION TRANSACTION READ ONLY")
        return con, "pyformat"
    raise MappingError(f"unsupported driver '{driver}' (sqlite, oracle, postgres, mssql, mysql)")


def bind(sql, params, style):
    """Rewrite :since / :today into the driver's parameter style."""
    if style == "named":
        used = set(PARAM.findall(sql))
        return sql, {k: v for k, v in params.items() if k in used}
    if style == "pyformat":
        return PARAM.sub(lambda m: f"%({m.group(1)})s", sql.replace("%", "%%")), params
    order = PARAM.findall(sql)
    return PARAM.sub("?", sql), [params[k] for k in order]


def normalise(value, col_type):
    if value is None:
        return None
    if col_type == "DATE":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)[:10]
    if col_type == "DATETIME":
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ")[:19]
        s = str(value)
        return s[:19] if re.match(r"\d{4}-\d{2}-\d{2}", s) else None   # some views return non-dates here
    if col_type == "REAL":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return str(value).strip()


class ERP:
    def __init__(self, con, style, mapping):
        self.con, self.style, self.mapping = con, style, mapping

    def fetch(self, entity_name, since):
        """Rows of one entity as tuples in schema column order."""
        entity = schema.BY_NAME[entity_name]
        params = {"since": since, "today": date.today()}
        if isinstance(self.con, sqlite3.Connection):     # SQLite keeps dates as ISO text
            params = {k: v.isoformat() for k, v in params.items()}
        sql, params = bind(self.mapping.entities[entity_name], params, self.style)
        cur = self.con.cursor()
        cur.execute(sql, params)
        got = [d[0].lower() for d in cur.description]
        extra = set(got) - set(entity.column_names)
        if extra:
            raise MappingError(f"{entity_name}: unknown columns {sorted(extra)} - alias them to: {entity.column_names}")
        missing = [c.name for c in entity.columns if c.required and c.name not in got]
        if missing:
            raise MappingError(f"{entity_name}: required columns missing {missing}")
        idx = {c: i for i, c in enumerate(got)}
        for row in cur:
            yield tuple(normalise(row[idx[c.name]], c.type) if c.name in idx else None for c in entity.columns)
