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

## New in 0.15.0 (owner's 24-item list)

These were added in one build on top of the model above. All read-only, all ERP-agnostic (mill-specifics stay in
the mapping/config, never the core).

| # | Feature | Where |
|---|---|---|
| 1 | Sales-this-month tile + drill to each invoice | Overview · `api/finance/sales` |
| 2 | Bank tile → balance per bank account | Overview · `api/finance/banks` |
| 3 | Dispatch truck animation driven from the Dispatched tile | Overview (live sync) |
| 4, 5 | Live-feed panel on Overview from the sync change-log (store issues grouped per loom), totals-only "Since" chips, animations only for big events | Overview · `api/feed` |
| 6 | Yarn-closing redesign: party → contract/quality dropdowns, 3 modes, IGP yarn + OGP fabric date-wise, warp/weft count, live-formula Excel + PDF | Yarn & Fabric → Closing · `api/closing/sheet` |
| 7 | Empty beams with sizes, opening CSV roll-forward, double-location flag | `report empty-beams` |
| 8 | Live scoreboard efficiency tile (reads the scoreboard's own current-shift figure) | Overview |
| 9 | Store additions: slip-no search, store expense (net of returns) + tile, issue-slips tab, store-returns tab (+`store_returns` entity) | Store Room tabs · `report store-expense/issue-slips/store-returns` |
| 10 | Loom-parts expense per loom, monthly matrix | `report loom-consumption` |
| 11 | Rates audit per item (purchase history + suppliers + similar items) | Store item drawer · `api/store/rates` |
| 12 | Sized-yarn closing count per set (6-step formula, 3 views, live-formula Excel) | `report sized-closing` |
| 13 | Manual rewind input per set (Smart Layer store, edit history, never ERP; generic manual inputs) | `manual_inputs` · `api/manual` |
| 14 | Cost per metre monthly, with / without sizing | `report cost-metre` |
| 15 | Contract progress (ordered/woven/dispatched/balance, m-day, est. finish) | `report contracts` |
| 16 | Yarn shortage warning (needed vs available by count) | `report yarn-shortage` (+ push) |
| 17 | Sizer ledger per sizer, live-formula Excel + PDF | `report sizer-ledger` |
| 18 | Unused-stock tab (in stock, not issued for N months) | Store Room → Unused stock · `report unused-stock` |
| 19 | Pushes from the sync: new production (once/day) and each new OGP fabric gate pass (replaces the hourly task) | `notify-changes` on every live sync |
| 20 | Morning summary + night-shift sensor result (~06:20) | `morning` / `night-sensor` |
| 21 | Scoreboard vs ERP weft comparison push | `weft-check` |
| 22 | Weft-only consumption by owner-given counts | `report weft-consumption` |
| 23 | Logo + 6 colour schemes + weave-forming opening animation + thread-to-signal header + colour picker | Overview 🎨 · `[dashboard] colour_scheme` |
| 24 | Fabric-in-stock history drill (produced / dispatched / returned / rejection / stock per contract) | Overview Dispatched tile · `api/fabric/history` |
| 31 | ERP edit history → Edits tab: list of every insert/edit/delete, filters (date/user/area/document), click a row for every field old→new, highlights (deletes, old entries, amount/account changes), "re-entered" pairing hint; phone push per delete and per old-entry edit; "edits by user" line in the morning summary. Line edits resolve their document/date/user from the header (`erp_doc_headers`); generic DB-owner logins fall back to the PC/host | Edits tab · `api/edits` · `erp_change_events`/`erp_change_values`/`erp_doc_headers` (optional) · `[edits]` |
| 29 | Scoreboard tab: live running-shift efficiency tile; a date picker showing day vs night efficiency per loom and mill totals; sensor duplicate-timings (swap) verdict live + per day/night; a 30-day compare with date check-boxes | Scoreboard tab · `api/scoreboard*` · `loom_monitor.shift` |
| 30a | Loom efficiency by date range / ticked dates: per loom ERP eff, scoreboard eff, metres, RPM, standard RPM, stops (sortable); click a loom for its day-by-day; CSV/PDF | Scoreboard tab → By date range · `api/looms/efficiency`, `api/looms/days` |
| 30b | Store purchasing: indent → PO → receipt; PO rate/qty vs the goods-receipt (differences flagged), supplier terms, paid/unpaid; pending indents / pending POs; per-item PO history | Purchasing tab · `api/purchasing` · `purchase_indents`/`purchase_orders` (optional) |
| 30c | Loom standard RPM per period shown against efficiency (the ERP's loom-detail table; a real target comes from config/ERP when present) | Scoreboard → By date range · `loom_standards` (optional) |
| 30d | Yarn purchase contracts (count, brand, qty, rate, delivery, status) | Purchasing → Yarn contracts · `yarn_contracts` (optional) |
| 30e | Item head / sub-head grouping in the store expense and unused-stock reports (a "by head" breakdown) | Store Room → Store expense / Unused stock · `items.head`/`sub_head` |
| 32 | `set-password` (hidden entry, DPAPI-sealed into .env) and `grants --missing` (prints only the GRANT lines for tables the read-only user cannot yet read); Morning scheduled task inherits the Daily principal and logs the real licence reason | CLI `set-password`, `grants --missing`; `installer/install_tasks.ps1`, `run_morning.cmd` |

## New in 0.16.1

| # | What it does | Where |
|---|--------------|-------|
| 3 | Physical fabric-count rejection: enter the goods actually counted in hand per contract+quality (by grade, with an "as on" date), so the closing uses the *counted* rejection instead of the ERP's C+CP grades — rolled forward by production and dispatch since the count date. A review table shows ERP stock vs ERP rejection vs the counted rejection and the difference; counts can be typed or imported as CSV | Weaving → Closing → **Fabric counts** · `api/closing/count-review`, `api/closing/counts-saved`, POST `api/closing/count`, `api/closing/count-import` · `[closing] rejection_grades`/`erp_start_date` |
| 4 | Closing balance in plain sentences ("X owes the mill N bags of 40 CARDED" / "The mill holds N bags of X's yarn") instead of a signed number, with a "Balance" side column in the report | Yarn & Fabric → Closing; contract-closing report |
| 5 | ERP system routines (e.g. nightly average-rate re-rating) collapse to one muted "ERP routine — N lines" row in the Edits tab and are never phone-pushed, so a person's real edits stand out | Edits tab · `[edits] system_modules`/`system_columns` |
| 6 | Real-PC attribution: when the ERP records the Forms server as the host, the true client PC is recovered from the WebLogic access log by time-matching the request, shown as "PC (by log match)" | Edits tab · `[edits] weblogic_access_log`/`ip_names`/`server_hosts` |

## New in 0.17.0

| # | What it does | Where |
|---|--------------|-------|
| Gully/Rewind | Tracks the gully (yarn left on the returned sizing cones) the ERP records but never follows: the mill rewinds and re-uses it — back to **sizing** for some yarns, as **weft** for others (a per-yarn route in config). Keeps a per-yarn book balance = opening + gully returned (net of the empty cones/tubes) − rewound re-used − waste, against a later physical count. The owner enters an **opening and an opening date** per yarn; only gully and re-use after that date are counted on top. Gully-cone count comes from the set record (bags × cones/bag + loose); the gate-app count is a cross-check. A per-set slip-vs-gate view, rewound/fresh line ticks keyed by date+doc+yarn (a to-sizing line matching a fresh receipt is auto-fresh; owner tick wins), an Overview "Gully book" tile, and PDF/Excel | Yarn & Fabric → **Gully / Rewind** · `api/gully`, `api/gully/sets`, `api/gully/lines`, POST `api/gully/set`,`/yarn`,`/line` · `[gully]` |
| Yarn weight check | Flags yarn-ledger lines with bags but a missing or implausible weight (checked on the ERP's **raw** entered weight `raw_kg`, before the mapping substitutes bags × a standard bag weight, so the bad rows are not hidden) — these throw the gully and yarn balances off | Yarn & Fabric → Gully / Rewind · `api/gully/quality` · `[gully] weight_ratio_low`/`weight_ratio_high` |

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
