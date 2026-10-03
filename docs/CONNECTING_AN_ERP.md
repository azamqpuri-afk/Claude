# Connecting a new ERP

Connecting a mill means writing one **mapping file**: a SELECT per standard entity, in the
ERP's own SQL, with the columns renamed to the standard names. No Python is needed.

## 1. Find the ERP's tables

Ask the ERP vendor or the mill's IT person for read access, then find where each of these lives:

| Standard entity | What to look for in the ERP |
|---|---|
| `items` | Item / product master for the store |
| `stock_balances` | Current stock per item: a stock table, a stock function, or opening + receipts − issues |
| `store_receipts` | GRN / purchase receipt lines |
| `store_issues` | Store issue slip lines |
| `parties` | Customers, suppliers, sizing parties |
| `accounts` | Chart of accounts |
| `ledger_entries` | General ledger / voucher lines |
| `account_balances` | Balance per account (usually a `GROUP BY` over the ledger) |
| `looms` | Loom master |
| `loom_production` | Production per loom per shift |
| `contracts` | Contracts / sales orders with construction (ends, picks, width, yarns, counts, rates) |
| `yarn_movements` | Yarn gate passes: from / to parties, to / from sizers, sized beams in, issues to looms |
| `fabric_dispatches` | Fabric gate passes out to parties and returns |
| `beams` | Beams now: on looms (set length, woven), sized and waiting, empty, at sizers |
| `beam_movements` | Beam gate passes and beams on / off looms |
| `loom_monitor` | Optional: the loom monitoring system (scoreboard / sensors) - usually a separate source |

`python -m smart_layer schema` prints every standard column with its meaning. Columns marked
`NOT NULL` are required; the rest can be left out if the ERP doesn't have them.

## 2. Write the mapping

Copy `mappings/demo_sqlite.toml` and replace each SELECT. Rules:

- **Alias every column to its standard name** (`SELECT itm_id AS item_code ...`).
- **Dated entities filter on `:since`**, the start of the sync window
  (`WHERE txn_date >= :since`). Write it the same way for every database; it is converted for
  the driver.
- **Signs:** ledger `amount` is debit positive, credit negative, so each voucher sums to zero.
- **Categories** use the standard values, translated with `CASE`:
  - `account_group`: cash, bank, customer, supplier, revenue, expense, asset, liability, equity, other
  - `voucher_kind`: payment, receipt, journal, sale, purchase, opening, other
  - `party_type`: customer, supplier, sizer, employee, other
- **`voucher_id`** must be unique per voucher. If the ERP's numbers repeat across books or years,
  join them (`book || '/' || vno || '/' || year`).
- **Efficiency:** give `efficiency_pct` if the ERP calculates it, or `meters_at_100`
  (e.g. `rpm * shift_minutes / (ppi * 39.37)`) so it can be calculated.
- Only `SELECT` / `WITH`, one statement per entity; anything else is refused.

## Other sources (loom scoreboard, sensors, exported files)

Some data lives outside the ERP - typically the loom monitoring system, which differs from vendor to vendor.
Give it its own connection in `config.toml` and point the entity at it with `source`:

```toml
[sources.scoreboard]
driver = "access"                 # access | sqlite | mssql | mysql | postgres | oracle | csv
path = 'E:\scoreboard\OLMSDB.mdb'
copy_first = true                 # read a copy - never lock the file the scoreboard program writes to
```
```toml
[entities.loom_monitor]
source = "scoreboard"
sql = """SELECT ... AS read_date, ... AS loom_code, ... AS meters FROM <the vendor's table> WHERE ... >= :since"""
```

Each vendor's tables and formulas go in that SELECT (for example meters = picks x panels / (picks per inch x 39.37)).
A vendor that only exports files: `driver = "csv"`, `files = 'C:\exports\*.csv'`, `table = "readings"`, then
`SELECT ... FROM readings`. Looms are matched by loom code, or by the number in the loom name.

## 3. Test it

```bash
python -m smart_layer check     # runs every entity separately and names any failure
python -m smart_layer sync
python -m smart_layer audit --date <a recent day>
```

Then compare a few numbers with the ERP's own reports: one day's production, one item's stock,
one party's balance. Note anything specific to the mill (voucher type codes, department names)
in `[ask] notes` in the config so Claude understands it.

## Database drivers

| `erp.driver` | Database | Install |
|---|---|---|
| `oracle` | Oracle (set `thick = true` for 11g and older) | `pip install oracledb` |
| `mssql` | SQL Server (`dsn` = ODBC connection string) | `pip install pyodbc` |
| `postgres` | PostgreSQL | `pip install psycopg[binary]` |
| `mysql` | MySQL / MariaDB | `pip install pymysql` |
| `sqlite` | SQLite file | built in |
