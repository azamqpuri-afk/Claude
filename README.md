# Project Smart Layer by Azam Puri

A smart layer for textile mills that sits on top of **any ERP**: daily audits, stock checks,
reorder lists, production reports, phone alerts, and **"ask the ERP"** in English, Urdu or
Roman Urdu, powered by Claude.

Dost Textiles is the first mill connected; the product is built so that any other mill, on any
ERP, can be connected with one mapping file.

## How it works

```
 Customer's ERP (Oracle, SQL Server, MySQL, PostgreSQL, SQLite ...)
        │   mapping file - one SELECT per standard entity, in the ERP's own SQL
        ▼   (the only part written per ERP; read-only)
 sync ──► local store (SQLite) - the standard model: items, stock, receipts, issues,
        │                        parties, accounts, ledger, looms, loom production
        ▼
 reports · audits · alerts · ask the ERP      ◄── written once, work for every ERP
```

- **Read-only, always.** Mappings may only contain `SELECT`; the ERP session is opened read-only
  where the database allows it. Use a read-only database user as well.
- **The live ERP is never loaded by reports or by Claude.** They read the local copy. Claude's
  queries run on a connection that refuses anything except reading.
- **Backdated corrections are picked up.** Each sync re-pulls a rolling window (default 400 days).

## What it does

| Command | What you get |
|---|---|
| `audit` | Yesterday's entries checked across finance, store, weaving, yarn, beams, fabric and the loom monitor. Graded **ACTION** (do something today), **CONFIRMED** (definite data error), **WATCH** (unusual). Only new ACTION and CONFIRMED findings are pushed, each once. `--month 2026-09` for a full monthly audit. |
| `report production` / `value` | Meters, efficiency and rejection per loom; what the looms earned (rate per meter or per pick). |
| `report stock` / `zero` / `reorder` | Store stock status, items that just ran out (push), draft order list. |
| `report closing` | Contract closing: each party's yarn account, using the mill's own method. |
| `report pilferage` | Weft issued vs needed per party and yarn, suspicious entries, sizing loss. |
| `report beams` / `sizers` | Every beam on a loom with days to finish and its backup; empty beams with each sizer. |
| `report yarn-stock` / `fabric-stock` | Yarn in hand and at sizers; fabric in the mill by grade with ageing. |
| `report pnl` / `balances` / `parties` | Monthly P&L by head, balance summary, receivable / payable per party. |
| `report store-recon` | Store consumption in the ledger vs what the store issued, month by month. |
| `serve` / `dashboard` | Owner dashboard on phone and PC (password-protected): a glanceable **Overview**, **Weaving** (loom floor, ERP vs scoreboard), **Store Room**, **Yarn & Fabric**, a **Finance** ledger desk (party ledgers, running balance, daily voucher summary), **Payroll**, **Gate Passes** and **Reports** — with interactive charts bundled to run offline, and "Ask the ERP" chat. |
| `ask` | Questions in English, Urdu or Roman Urdu, answered from the data. |

Add `--csv` and/or `--pdf` to any report or the audit to save it in the output folder.

Audit checks include: unbalanced vouchers, trial balance, negative cash, backdated and future-dated vouchers,
double payments, large payments; zero-quantity slips, negative stock, purchase-rate jumps, cheaper elsewhere,
the same part issued twice, the same supplier bill twice; efficiency over 100%, production without a beam,
high rejection, idle looms, a loom far from its usual output; suspicious weft issues and sizing loss; looms
about to run out of beam with no backup, beams woven beyond their length, empty beams not back from sizers;
negative fabric stock; entries edited or deleted after saving; ERP production vs the loom monitor.

Thresholds **learn each mill's own habits**, and every method (contract closing, rejection, efficiency,
shrinkage, warning days) is the mill's own setting. See [docs/FEATURE_COVERAGE.md](docs/FEATURE_COVERAGE.md)
for how each of Dost Textiles' original scripts maps to the product (in the repository).

## Try it now on the demo mill

The demo is a fictional mill on a fictional ERP whose tables look nothing like the standard
model, with known mistakes planted for the audit to find.

```bash
pip install -r requirements.txt
python demo/build_demo_erp.py
python -m smart_layer -c config.demo.toml check
python -m smart_layer -c config.demo.toml sync
python -m smart_layer -c config.demo.toml audit
python -m smart_layer -c config.demo.toml report production
python -m smart_layer -c config.demo.toml report closing
python -m smart_layer -c config.demo.toml report pilferage
python -m smart_layer -c config.demo.toml report beams --pdf
python -m smart_layer -c config.demo.toml serve          # then open http://localhost:8080
python -m smart_layer -c config.demo.toml ask "which loom had the highest rejection last week?"   # needs ANTHROPIC_API_KEY
```

## Install at a mill

Follow **[docs/INSTALL_AT_A_MILL.md](docs/INSTALL_AT_A_MILL.md)** - the pilot playbook (what to ask beforehand, the
read-only database user, installation, checking the numbers with the owner, handover, support).

In short, on the mill's Windows server:

1. Copy the `SmartLayer` folder (from `dist/SmartLayer-<version>.zip`, built with `python tools/make_release.py`).
2. Double-click **`Install Smart Layer.bat`** and answer the questions. The installer finds or installs Python,
   writes `config.toml` and `.env` (passwords only in `.env`), tests every table, does the first sync,
   schedules the daily run, starts the dashboard with Windows and opens its port for phones on the mill network.
3. Licence: the installer shows the server's ID; you issue a licence for it and the mill pastes it
   (installer or dashboard → **Licence**). See [docs/LICENSING.md](docs/LICENSING.md) (not shipped).
4. `python -m smart_layer status` shows the health of the installation at any time.

A new ERP needs a mapping file first - see [docs/CONNECTING_AN_ERP.md](docs/CONNECTING_AN_ERP.md).
`python -m smart_layer grants` prints the SQL for the mill's IT person to create the read-only database user.

## Repository layout

```
smart_layer/            the product
  schema.py             the standard data model (single source of truth)
  erp.py                ERP connector + mapping loader (read-only guard)
  store.py              local store and sync
  reports/              stock, reorder, production
  audit/                finance, store, weaving, yarn checks
  textile/              contract closing (method.py = the mill's own rules) and pilferage
  web/                  owner dashboard (one page, all tabs), data builder, finance ledger desk and web server
  web/static/           Chart.js, bundled and served locally so charts work offline
  ask.py                "ask the ERP" with Claude
  setup_wizard.py       first-time setup questions
  licence.py            licence check (signed, tied to one server, expiry)
mappings/               one file per ERP
docs/                   pilot playbook, connecting a new ERP
demo/                   demo ERP generator (fictional mill)
Install Smart Layer.bat, install_windows.ps1, run_daily.ps1, run_dashboard.ps1, uninstall_windows.ps1
tools/make_release.py   builds the customer package (dist/)
tools/issue_license.py  makes licences - run on your own computer only (private key never in the repo)
tests/                  end-to-end tests
examples/               Dost Textiles' settings (not shipped to customers)
legacy/dost_textiles/   the original Dost Textiles scripts (not shipped to customers)
```

## Tests

```bash
python -m pytest -q
```
