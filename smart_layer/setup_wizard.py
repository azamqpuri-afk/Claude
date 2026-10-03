"""
Setup wizard: asks a few questions and writes config.toml and .env, so nobody edits files by hand.

    python -m smart_layer setup

Re-running it is safe: the old files are kept as config.toml.bak / .env.bak.
Passwords go only into .env (never into config.toml, never printed).
"""

import getpass as _getpass
import glob
import importlib
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

DRIVERS = [  # key, label, python package, import name
    ("oracle", "Oracle", "oracledb", "oracledb"),
    ("mssql", "Microsoft SQL Server", "pyodbc", "pyodbc"),
    ("mysql", "MySQL / MariaDB", "pymysql", "pymysql"),
    ("postgres", "PostgreSQL", "psycopg[binary]", "psycopg"),
    ("sqlite", "SQLite file", None, "sqlite3"),
]
ORACLE_CLIENT_GLOBS = [r"C:\oracle*\**\oci.dll", r"D:\oracle*\**\oci.dll", r"C:\app\**\oci.dll", r"D:\app\**\oci.dll",
                       r"C:\instantclient*\oci.dll", r"D:\ORACLE_DB\**\oci.dll", r"C:\ORACLE_DB\**\oci.dll"]


def toml_str(v):
    """A TOML string; single-quoted (literal) when it has backslashes, so Windows paths work as typed."""
    v = str(v)
    if "\\" in v and "'" not in v:
        return f"'{v}'"
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Wizard:
    def __init__(self, base_dir, ask=input, ask_secret=None, out=print, install=True):
        self.base = Path(base_dir)
        self.ask_raw, self.out, self.install = ask, out, install
        self.ask_secret = ask_secret or _getpass.getpass
        self.env = {}

    # -- prompts ---------------------------------------------------------------------------------
    def ask(self, question, default=None):
        a = self.ask_raw(f"{question}{f' [{default}]' if default not in (None, '') else ''}: ").strip()
        return a or (default if default is not None else "")

    def yes(self, question, default=True):
        a = self.ask(f"{question} (y/n)", "y" if default else "n").lower()
        return a.startswith("y")

    def choose(self, question, options, default=1):
        self.out(question)
        for i, label in enumerate(options, 1):
            self.out(f"  {i}. {label}")
        while True:
            a = self.ask("Choose a number", str(default))
            if a.isdigit() and 1 <= int(a) <= len(options):
                return int(a) - 1
            self.out("  Please type one of the numbers above.")

    def secret(self, question, allow_empty=False):
        while True:
            v = self.ask_secret(f"{question}: ").strip()
            if v or allow_empty:
                return v
            self.out("  This cannot be empty.")

    # -- steps -----------------------------------------------------------------------------------
    def erp_section(self):
        i = self.choose("\nWhich database does the mill's ERP use?", [d[1] for d in DRIVERS])
        key, label, package, module = DRIVERS[i]
        lines = [f'driver = "{key}"']
        if key == "oracle":
            self.out("Oracle connection (ask the ERP vendor if unsure). Examples: ORCL  or  192.168.1.10:1521/ORCL")
            lines.append(f"dsn = {toml_str(self.ask('Database (TNS name or host:port/service)', 'ORCL'))}")
            lines.append(f"user = {toml_str(self.ask('Database user (best: a read-only user)', 'smartlayer_ro'))}")
            if self.yes("Is the database Oracle 11g or older? (needs Oracle Client)", True):
                lines.append("thick = true")
                found = self.find_oracle_client()
                lib = self.ask("Folder of the Oracle Client (has oci.dll); leave empty if it is on PATH", found)
                if lib:
                    lines.append(f"client_lib_dir = {toml_str(lib)}")
            tns = self.ask("Folder with tnsnames.ora, if the database name is a TNS name (Enter = find it automatically)", "")
            if tns:
                lines.append(f"tns_admin = {toml_str(tns)}")
        elif key == "mssql":
            server = self.ask("SQL Server name or address", "localhost")
            db = self.ask("Database name")
            user = self.ask("Database user (best: a read-only user)", "smartlayer_ro")
            drv = self.ask("ODBC driver name", "ODBC Driver 17 for SQL Server")
            lines.append(f"dsn = {toml_str(f'DRIVER={{{drv}}};SERVER={server};DATABASE={db};UID={user};PWD=${{ERP_PASSWORD}}')}")
            lines.append('password = "${ERP_PASSWORD}"')
        elif key == "mysql":
            lines += [f"host = {toml_str(self.ask('Server address', 'localhost'))}",
                      f"port = {int(self.ask('Port', '3306'))}",
                      f"database = {toml_str(self.ask('Database name'))}",
                      f"user = {toml_str(self.ask('Database user (best: a read-only user)', 'smartlayer_ro'))}"]
        elif key == "postgres":
            host, port, db = self.ask("Server address", "localhost"), self.ask("Port", "5432"), self.ask("Database name")
            user = self.ask("Database user (best: a read-only user)", "smartlayer_ro")
            lines.append(f"dsn = {toml_str(f'host={host} port={port} dbname={db} user={user} password=${{ERP_PASSWORD}}')}")
            lines.append('password = "${ERP_PASSWORD}"')
        else:
            lines.append(f"path = {toml_str(self.ask('Path of the SQLite file', 'demo/demo_erp.db'))}")
        if key in ("oracle", "mysql"):
            lines.append('password = "${ERP_PASSWORD}"')
        if key != "sqlite":
            self.env["ERP_PASSWORD"] = self.secret("Database password (typing is hidden)")
        lines.append(f"mapping = {toml_str(self.mapping())}")
        self.ensure_driver(package, module)
        return key, lines

    def mapping(self):
        maps = sorted((self.base / "mappings").glob("*.toml"))
        names = []
        for m in maps:
            title = next((ln.split("=", 1)[1].strip().strip('"') for ln in m.read_text(encoding="utf-8").splitlines()
                          if ln.strip().startswith("name =")), m.stem)
            names.append(f"{m.name}  ({title})")
        i = self.choose("\nWhich mapping file connects this ERP? (a new ERP needs a new mapping - see docs)",
                        names + ["New mapping for a new ERP (start from the template)"], default=1)
        if i < len(maps):
            return f"mappings/{maps[i].name}"
        name = self.ask("Short name for the new mapping (letters, no spaces)", "my_mill").replace(" ", "_")
        target = self.base / "mappings" / f"{name}.toml"
        if not target.exists():
            shutil.copy(self.base / "mappings" / "demo_sqlite.toml", target)
            self.out(f"  Created {target.relative_to(self.base)} from the demo mapping - edit its SELECTs for this ERP "
                     f"(docs/CONNECTING_AN_ERP.md), then run  python -m smart_layer check")
        return f"mappings/{target.name}"

    def find_oracle_client(self):
        if os.name != "nt":
            return ""
        for pattern in ORACLE_CLIENT_GLOBS:
            hits = glob.glob(pattern, recursive=True)
            if hits:
                return str(Path(hits[0]).parent)
        return ""

    def ensure_driver(self, package, module):
        if not package:
            return
        try:
            importlib.import_module(module)
        except ImportError:
            if not self.install:
                self.out(f"  Note: install the database driver with:  python -m pip install {package}")
                return
            self.out(f"  Installing the database driver ({package}) ...")
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", package], check=False)

    def dashboard_section(self):
        lan = self.yes("\nOpen the dashboard to phones and PCs on the mill network?", True)
        port = int(self.ask("Dashboard port", "8080"))
        pw = self.secret("Dashboard password (leave empty to create one)", allow_empty=True) if lan else ""
        if lan and not pw:
            pw = secrets.token_urlsafe(9)
            self.out(f"  Dashboard password created: {pw}   <- write this down and give it to the owner")
        if pw:
            self.env["DASHBOARD_PASSWORD"] = pw
        return [f'host = "{"0.0.0.0" if lan else "127.0.0.1"}"', f"port = {port}"] + (
            ['password = "${DASHBOARD_PASSWORD}"'] if pw else [])

    def notify_section(self):
        if not self.yes("\nSend alerts to the owner's phone (free ntfy app)?", True):
            return ['channels = ["console"]']
        topic = "smartlayer-" + secrets.token_hex(12)
        self.env["NTFY_TOPIC"] = topic
        self.out(f"  On the owner's phone: install the 'ntfy' app, tap +, and subscribe to:  {topic}")
        self.out("  (keep this name private - anyone with it can read the alerts)")
        return ['channels = ["console", "ntfy"]', 'ntfy_topic = "${NTFY_TOPIC}"']

    def chat_section(self):
        key = self.secret("\nClaude API key for 'Ask the ERP' chat (from console.anthropic.com; Enter to skip)",
                          allow_empty=True)
        if key:
            self.env["ANTHROPIC_API_KEY"] = key
            if self.install:
                self.ensure_driver("anthropic", "anthropic")

    # -- run -------------------------------------------------------------------------------------
    def run(self):
        self.out("Project Smart Layer by Azam Puri - setup\n" + "=" * 40)
        company = self.ask("Mill name", "My Mill")
        currency = self.ask("Currency", "Rs")
        driver, erp = self.erp_section()
        serve = self.dashboard_section()
        notify = self.notify_section()
        self.chat_section()
        config = TEMPLATE.format(company=toml_str(company), currency=toml_str(currency), erp="\n".join(erp),
                                 serve="\n".join(serve), notify="\n".join(notify))
        cfg_path, env_path = self.base / "config.toml", self.base / ".env"
        for p in (cfg_path, env_path):
            if p.exists():
                shutil.copy(p, p.with_name(p.name + ".bak"))
        cfg_path.write_text(config, encoding="utf-8")
        old = {}
        if env_path.exists():
            from .config import read_text_any
            old = dict(ln.split("=", 1) for ln in read_text_any(env_path).splitlines() if "=" in ln and not ln.startswith("#"))
        env_path.write_text("\n".join(f"{k}={v}" for k, v in {**old, **self.env}.items()) + "\n", encoding="utf-8")
        self.out(f"\nSaved {cfg_path.name} and {env_path.name} (passwords only in .env).")
        return cfg_path


TEMPLATE = """# Project Smart Layer by Azam Puri - written by the setup wizard (python -m smart_layer setup).
# Passwords and keys are in .env next to this file. Edit values here any time; re-run the wizard to start over.

[company]
name = {company}
currency = {currency}

[erp]
{erp}

[store]
path = "data/smart_layer.db"

[sync]
history_days = 400

[serve]
{serve}

[notify]
{notify}

[reports]
tracked_issue_days = 60
reorder_cover_months = 2
output_dir = "output"

[audit]
large_payment = 500000
duplicate_payment_min = 5000
# Practices the owner confirms as normal are kept out of alerts, e.g.
# dismiss = [{{ match = "^big-", why = "advance payments to sizers are normal" }}]

# The mill's own contract-closing method - set these with the owner.
[closing]
divisor = 840                      # many mills use 800
bag_lb = 100
basis = "dispatched"               # dispatched (net of returns) | woven
rejection_charged_to = "mill"      # mill: party pays yarn for fresh meters only | party: all meters
wastage_allowance_pct = 0
yarn_groups = []                   # e.g. [["6", "18"]] when one yarn is booked under two codes
# weight_formula = "threads_per_inch * width_in * 1.0936 / divisor / count"
# [closing.yarns."<yarn code>"]
# warp_closing_count = 38
# weft_closing_count = 39
# rate_per_lb = 550

[pilferage]
lookback_days = 90
excess_kg = 400
sizing_loss_pct = 6

[ask]
model = "claude-opus-5-5"
effort = "high"
notes = \"\"\"
Describe the mill's voucher types, departments and anything special about its ERP here, for the chat assistant.
\"\"\"
"""


def run(base_dir="."):
    return Wizard(base_dir).run()
