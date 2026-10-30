# Where each Dost Textiles script went

Every script in `legacy/dost_textiles/` was reviewed. Features that any mill can use were rebuilt in the
product on the standard model (so they work with any ERP); the rest are listed with the reason.

## Rebuilt in the product

| Dost Textiles script(s) | Product feature | Command |
|---|---|---|
| 12, 13, 23, 27 daily production report / export, efficiency summary, month-end re-export | Daily production per loom (meters, efficiency, rejection), CSV/PDF; every sync re-pulls a rolling window so backdated corrections appear | `report production` |
| 13, 14, 15, 16 quality history with contract rates | Production value: earnings per loom and contract (rate per meter or per pick) | `report value` |
| 33, 34, 36, 39, 76 store stock check, zero-stock alert, vendor order list, PDF push | Whole store room (every item with stock or movement, searchable, click for issue history), new zero-stock push, draft reorder list, demand list (what to buy, on usage), item issue history | `report stock` · `report zero --push` · `report reorder` · `report demand` · Store tab |
| 40, 41, closing_entries, merged_ledger | Monthly P&L by account head, balance summary, party balances | `report pnl` · `report balances` · `report parties` |
| 45, 67 stores reconciliation, store journal by month | Ledger store consumption vs store issues per month | `report store-recon` |
| 48, 50, 55, 57, 59 contract closing (owner's method), grand closings | Contract closing with the mill's own method (divisor, counts, rejection rule, basis, allowances, per-party/contract overrides, own formula), yarn groups for one yarn under two codes, party totals | `report closing` |
| 52, 56 yarn & fabric data, party stock by date | Yarn in hand and at sizers per party/yarn; fabric stock per contract by grade with ageing | `report yarn-stock` · `report fabric-stock` |
| 54, 58 pilferage checks | Weft issued vs needed per party and yarn, suspicious entries, sizing loss per sizer | `report pilferage` (+ daily audit) |
| 60, 61, 62, 63, 64, 65, 75 beam register, sizer audit/ledger, backup alert, weekly beam report, correction list, sizer reconciliation | Beams on looms with days to finish (learned shrinkage), backup matching on contract/ends, phone alert, beam stock, empty beams per sizer and numbers not returned, overweave check; weekly beam report with days to finish by BOTH ERP and monitor rate (earlier wins) + finish date, per-loom-type warn window, PDF pushed Fridays | `report beams` · `report sizers` · `report weekly-beams` (+ daily audit) |
| 77 sensor twin check | Sensor duplicate-timings (sensor-swap) check from the monitor's raw stop log: looms that stop AND restart on the same second, grouped into episodes, graded by an optional floor layout; runs 24/7 on every live sync, pushes once per confirmed episode, stale-data alert | `[sources.*]` + `loom_stops` mapping, `[sensor]` (daily + live audit) |
| 70 ERP audit (daily / monthly) | Finance, store, weaving, yarn, beam, fabric, loom-monitor checks; ACTION / CONFIRMED / WATCH; learns the mill's habits; dismiss rules; push once | `audit` · `audit --month` |
| 26 production entry snapshot diff; 70 issue-value drift | Entries changed after saving (issue qty/value, production meters, deletions) | daily `audit` |
| 70 loom vs history | A loom's output far from its own usual | daily `audit` |
| 70 negative fabric stock | Dispatched more than produced in a grade | daily `audit` |
| 74 scoreboard data | Loom monitoring as a second source (any vendor: Access, SQL databases, CSV exports); ERP vs monitor per loom; monitor data missing | `[sources.*]` + `loom_monitor` mapping (+ daily audit) |
| 02, 24 backdated loans / POs; 19 entry timing | Backdated and future-dated vouchers against the mill's learned keying lag | daily `audit` |
| 40, 51, 53 business dashboard, dashboard pages, dashboard verification | Owner dashboard (phone/PC) with chat; every figure from the same verified store | `serve` · `dashboard` |
| 42, 44, 46, 47 detail / correction PDFs | PDF output of every report and the audit | `--pdf` on any report or audit |

## Not in the product, and why

| Script(s) | Reason |
|---|---|
| 01, 05-11, 17, 18, 20-22, 25 schema exploration and diagnostics | One-off investigation of the Dost ERP's tables - the mapping file replaces them |
| 03 admin batch loan amounts | One-off payroll correction for Dost |
| 49 closing by set lengths | Alternative closing idea; the product's closing covers the owner's chosen method. Can be added as a closing basis if a customer wants it |
| 66, 68, 69 audits of the accountant's closing workbooks | Read one accountant's own Excel layout - specific to that file; the product's closing replaces the workbook |
| 71 photo agent | Needs the Dost gate app and its photos; possible later as an add-on per mill |
| 72 approval folder check | Dost's Google Drive signing workflow |
| gate software checks inside 70 | Need the Dost gate app; another mill's gate system would need its own connector |
| ERP backup checks inside 70 | Server housekeeping specific to Dost's IT |
| old ERP (DOSTT) history | A second mapping for the old ERP can be added per mill when history before the current ERP is needed |
