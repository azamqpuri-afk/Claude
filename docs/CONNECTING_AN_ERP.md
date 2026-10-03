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
