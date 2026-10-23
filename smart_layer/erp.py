"""
ERP connector: reads the customer's ERP through a mapping file.

A mapping file (mappings/*.toml) holds one SELECT per standard entity, written
in the ERP's own SQL dialect, whose column aliases are the standard column
names from schema.py. That file is the only thing written per ERP.

Safety: only SELECT / WITH statements are accepted, one statement each, and
the session is put in read-only mode where the database supports it. Use a
read-only database user as well.
"""

import os
import re
import shutil
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
        from .secret import read_mapping_text    # sealed <name>.map in the product, plain <name>.toml in dev
        data = tomllib.loads(read_mapping_text(self.path))
        meta = data.get("mapping", {})
        self.name = meta.get("name", self.path.stem)
        self.notes = meta.get("notes", "")
        self.entities = {}
        self.source = {}                             # entity -> source name ("erp" = the main ERP connection)
        for name, spec in data.get("entities", {}).items():
            if name not in schema.BY_NAME:
                raise MappingError(f"{self.path.name}: unknown entity '{name}' (known: {', '.join(schema.BY_NAME)})")
            self.entities[name] = check_select(spec["sql"])
            self.source[name] = spec.get("source", "erp")

    def missing(self):
        return [e.name for e in schema.ENTITIES if e.name not in self.entities]


def connect(erp_cfg, base_dir="."):
    """Open a connection to the ERP database. Drivers are imported only when used."""
    driver = erp_cfg.get("driver", "sqlite").lower()
    if erp_cfg.get("copy_first") and driver in ("sqlite", "access"):
        # read a copy, never the live file (e.g. a loom scoreboard database another program keeps open)
        src = Path(erp_cfg["path"])
        src = src if src.is_absolute() else Path(base_dir) / src
        dst = Path(base_dir) / "data" / f"source_copy_{src.stem}{src.suffix}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        erp_cfg = {**erp_cfg, "path": str(dst)}
    if driver != "sqlite" and "password" in erp_cfg and not erp_cfg["password"]:
        raise SystemExit("The ERP password is empty: put ERP_PASSWORD=... in the .env file next to config.toml "
                         "(check it is named .env, not .env.txt).")
    if driver == "sqlite":
        p = Path(erp_cfg["path"])
        p = p if p.is_absolute() else Path(base_dir) / p
        if not p.exists():
            raise FileNotFoundError(p)
        return sqlite3.connect(f"file:{p}?mode=ro", uri=True), "named"
    if driver == "oracle":
        import oracledb
        # Where the TNS names (e.g. "ORCL") are defined: config tns_admin, else the client's network\admin folder.
        # Scheduled tasks run as SYSTEM, which may not have the TNS_ADMIN / ORACLE_HOME settings of the logged-in user.
        tns = erp_cfg.get("tns_admin")
        lib = erp_cfg.get("client_lib_dir")
        if not tns and lib:
            for cand in (Path(lib).parent / "network" / "admin", Path(lib) / "network" / "admin"):
                if (cand / "tnsnames.ora").exists():
                    tns = str(cand)
                    break
        if tns:
            os.environ.setdefault("TNS_ADMIN", tns)
        if erp_cfg.get("thick") or lib:              # Oracle Client mode, needed for 11g and older
            oracledb.init_oracle_client(lib_dir=lib or None, config_dir=tns or None)
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
    if driver == "csv":                              # exported files (many scoreboard / sensor vendors only export CSV)
        return csv_connection(erp_cfg, base_dir), "named"
    if driver == "access":                           # MS Access .mdb / .accdb (Windows, Access ODBC driver)
        import pyodbc
        p = Path(erp_cfg["path"])
        p = p if p.is_absolute() else Path(base_dir) / p
        return pyodbc.connect(rf"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={p};ReadOnly=1;", readonly=True), "qmark"
    if driver == "mysql":
        import pymysql
        con = pymysql.connect(host=erp_cfg["host"], user=erp_cfg["user"], password=erp_cfg["password"],
                              database=erp_cfg["database"], port=int(erp_cfg.get("port", 3306)))
        con.cursor().execute("SET SESSION TRANSACTION READ ONLY")
        return con, "pyformat"
    raise MappingError(f"unsupported driver '{driver}' (sqlite, oracle, postgres, mssql, mysql, access, csv)")


def csv_connection(src_cfg, base_dir="."):
    """Load CSV files (a folder / pattern) into an in-memory table so the mapping's SELECT can read them.
    [sources.x] driver = "csv", files = 'C:\\scoreboard\\export\\*.csv', table = "readings" (default "data")."""
    import csv
    import glob
    pattern = str(src_cfg["files"])
    if not Path(pattern).is_absolute():
        pattern = str(Path(base_dir) / pattern)
    table = re.sub(r"\W", "_", src_cfg.get("table", "data"))
    rows, cols = [], []
    for f in sorted(glob.glob(pattern)):
        from .config import read_text_any
        reader = csv.DictReader(read_text_any(f).splitlines(), delimiter=src_cfg.get("delimiter", ","))
        for r in reader:
            for k in r:
                if k and k not in cols:
                    cols.append(k)
            rows.append(r)
    con = sqlite3.connect(":memory:")
    safe = [re.sub(r"\W", "_", c.strip()) for c in cols] or ["empty"]
    con.execute(f"CREATE TABLE {table} ({', '.join(safe)})")
    if cols:
        con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})",
                        [[(r.get(c) or None) for c in cols] for r in rows])
    return con


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
    def __init__(self, con, style, mapping, sources=None, base_dir="."):
        self.con, self.style, self.mapping = con, style, mapping
        self.sources_cfg, self.base_dir = sources or {}, base_dir
        self._open = {"erp": (con, style)}

    def connection(self, entity_name):
        name = self.mapping.source.get(entity_name, "erp")
        if name not in self._open:
            if name not in self.sources_cfg:
                raise MappingError(f"{entity_name} reads source '{name}' - add [sources.{name}] to config.toml")
            self._open[name] = connect(self.sources_cfg[name], self.base_dir)
        return self._open[name]

    def fetch(self, entity_name, since, as_of=None):
        """Rows of one entity as tuples in schema column order.
        as_of overrides the :today bind, so a snapshot view (e.g. fabric_stock) can be read as of a past date."""
        entity = schema.BY_NAME[entity_name]
        con, style = self.connection(entity_name)
        params = {"since": since, "today": as_of or date.today()}
        if isinstance(con, sqlite3.Connection):          # SQLite keeps dates as ISO text
            params = {k: v.isoformat() for k, v in params.items()}
        elif style == "qmark":                            # ODBC (SQL Server, Access) binds datetimes most reliably
            params = {k: datetime(v.year, v.month, v.day) for k, v in params.items()}
        sql, params = bind(self.mapping.entities[entity_name], params, style)
        cur = con.cursor()
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
