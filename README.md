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
| `audit` | Yesterday's entries checked across finance, store and weaving. Findings graded **ACTION** (do something today), **CONFIRMED** (definite data error), **WATCH** (unusual). Only new ACTION and CONFIRMED findings are pushed, each one once. `--month 2026-09` for a full monthly audit. |
| `report stock` | Every item used in the last 60 days: out of stock / low (under one month of use) / in stock. |
| `report zero` | Items that ran out since the last check: for a daily phone push. |
| `report reorder` | Draft order list with last supplier, last rate and estimated value. Nothing is sent to suppliers. |
| `report production` | Meters, efficiency and rejection per loom for a day, and looms with nothing entered. |
| `report closing` | **Contract closing**: each party's yarn account per contract (received − returned − consumed = balance in bags and money). The whole method is the mill's own: divisor, bag size, closing counts, basis (dispatched or woven), who bears rejection, allowances, per-party and per-contract overrides, even the yarn-weight formula. |
| `report pilferage` | **Yarn pilferage check**: weft issued vs weft needed for the fabric woven, suspicious entries (no production nearby, very large, keyed late or after hours), sizing loss per sizer. Yarn findings also join the daily audit. |
| `serve` | **Owner dashboard** on the mill network (phone and PC): key figures, what needs attention, production and finance charts, contract closing, yarn flags, stock, looms, and an "Ask the ERP" chat. Password-protected. |
| `dashboard` | The same dashboard saved as one HTML file (for sharing or demos; chat needs `serve`). |
| `ask` | Questions in plain language, answered from the data, with the queries shown on request. |

Audit checks include: unbalanced vouchers, trial balance, negative cash, backdated and
future-dated vouchers, possible double payments, large payments, zero-quantity issue slips,
negative stock, purchase-rate jumps, cheaper prices from other suppliers, the same part issued
twice to one machine, the same supplier bill on two receipts, efficiency over 100%, production
without a beam, high rejection, and looms with no production for longer than usual.

Thresholds **learn each mill's own habits** (how late each voucher type is normally keyed, how
often each loom is booked, which suppliers reuse bill numbers), so a flag means "unusual for this
mill". Practices the owner confirms as normal go in `audit.dismiss` and stop being pushed.

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
python -m smart_layer -c config.demo.toml serve          # then open http://localhost:8080
python -m smart_layer -c config.demo.toml ask "which loom had the highest rejection last week?"   # needs ANTHROPIC_API_KEY
```

## Install at a mill

1. Copy `config.example.toml` to `config.toml` and fill in the ERP connection.
   Put passwords and keys in a `.env` file next to it (never committed).
2. Point `erp.mapping` at the mill's mapping file (see [docs/CONNECTING_AN_ERP.md](docs/CONNECTING_AN_ERP.md)).
3. `python -m smart_layer check` tests every entity and names any that fail.
4. `python -m smart_layer sync`, then run the reports.
5. On Windows, schedule `run_daily.ps1` once a day with Task Scheduler.

Phone alerts use [ntfy](https://ntfy.sh). Topics can be read by anyone who knows the name, so use a
long random topic name and preferably an access token.

## Repository layout

```
smart_layer/        the product
  schema.py         the standard data model (single source of truth)
  erp.py            ERP connector + mapping loader (read-only guard)
  store.py          local store and sync
  reports/          stock, reorder, production
  audit/            finance, store, weaving checks
  textile/          contract closing (method.py = the mill's own rules) and pilferage
  web/              owner dashboard page, data builder and web server
  ask.py            "ask the ERP" with Claude
mappings/           one file per ERP
  dost_textiles_oracle.toml
  demo_sqlite.toml
demo/               demo ERP generator
tests/              end-to-end tests on the demo ERP
legacy/dost_textiles/   the original Dost Textiles scripts, kept as they are
```

## Tests

```bash
python -m pytest -q
```
