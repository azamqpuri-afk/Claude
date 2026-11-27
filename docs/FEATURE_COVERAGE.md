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

## New in 0.17.5

| # | What it does | Where |
|---|--------------|-------|
| 1 | The trial-balance finding names the out-of-balance voucher(s) (e.g. "SRN 771 Rs 24,407"), not just the total | Audit → Finance · `audit/finance.py` |
| 2 | Empty beams at a sizer are judged by **quantity** per sizer (sent vs empties + sized back), not by beam number — so a beam returned under a different number is no longer a false WATCH (INFO "all back by count"); only a real net shortfall alerts, oldest first | Audit → Beams · `audit/beams.py` |
| 3 | Idle-loom alert first checks for a **quality change** (a new beam mounted after the last entry) and gives it a grace period (INFO); otherwise it uses the scoreboard to word it as an entry problem ("ran … but nothing entered") vs a loom problem ("stopped") | Audit → Weaving · `audit/weaving.py` · `[audit] quality_change_days`/`entry_lag_days` |
| 4 | Over/under-production checks the loom's **quality** — a changed quality moves the picks/inch and so the meters, reported as INFO with the ppi change, not a false WATCH | Audit → Weaving · `audit/weaving.py` |
| 5 | Live feed shows the latest entries; when the newest isn't today it labels the panel with the last-entry date and time | Overview · `api/feed` |
| 6-9 | Weaving: ERP-vs-scoreboard shown as equal side-by-side boxes with the difference; both ERP and scoreboard lines always drawn; the monthly chart is **meters + A-grade %** (current month "to date"); the loom-by-loom table is in numeric loom order with ERP/scoreboard efficiency and meters for the last 30 days **and** this month, a shed TOTAL row, and a sticky loom column | Weaving tab · `web/data.py`, `web/dashboard.html` |
| 10 | **Contract report**: pick a contract (open or closed) and get the whole contract on one page and as PDF + Excel (one sheet per section) — header, yarn in (IGP), yarn returned/sold, sizing per sizer, sets, beams, production, a picks check (same quality at a different ppi), weft, fabric out and the balances. Also `report contract --contract <no>` on the CLI. "Contract closing" is removed from the Reports menu (the code is kept) | Reports → Contract report · `textile/contract_report.py` · `api/contracts-all`, `api/contract-report` · CLI `report contract` |

## New in 0.17.6 (retest fixes)

| # | What it does | Where |
|---|--------------|-------|
| a | Yarn received now tags warp/weft by the contract's warp yarn/count and defaults the rest to weft, so the weft balance is never left at zero | `textile/contract_report.py` `_side` |
| b | Sizing per sizer shows the **sized-beam yarn back** (from the sets), with gully and waste in their own columns and the loss % from the actual sized weight — no more false 0% | Contract report → Sizing |
| c | Beams are marked **running** ("running, X m to come") vs **finished**; short/extra % and shrinkage are finished-only (shrinkage labelled "to date" while beams still run); beams the ERP recorded only against a loom are pulled in too | Contract report → Beams |
| d | Production columns relabelled ("Total m") with a note saying how A / B / Rejection relate to the total, so the grades read correctly | Contract report → Production |
| e | Fabric dispatched shows a **Thaan** column (when the ERP records it); the picks check also flags **different picks/inch within the same contract** across the ERP's own entries | Contract report → Fabric out / Picks check |

## New in 0.17.7 (second retest)

| # | What it does | Where |
|---|--------------|-------|
| 1 | Dost fabric gate-pass thaan column is `THAN` (not `THAAN`) — mapping fixed so the fabric dispatch sync no longer fails | `mappings/dost_textiles_oracle.toml` |
| 2 | A new sync test runs every demo-mapping entity through `fetch`, so a mapping that references a column the source does not have is caught in CI | `tests` |
| 3 | The quality-change date in the output-change audit is the **first production date of the new contract** on the loom (not the first date inside the recent window); when the change predates the window it compares only against earlier days of the **same quality**, and says "not enough same-quality history" rather than a false alert | Audit → Weaving · `audit/weaving.py` |
| 4 | Contract-report beams resolve to the **real painted beam number and set length** from beam movements (never the ERP's internal production id); running vs finished is kept | Contract report → Beams |
| 5 | Production grades tie to the total: when the ERP's rejection already includes B (Dost B + C + CP) the report shows A, B, C/CP and total Rejection with "Total = A + Rejection". `[weaving] rejection_includes_second` ("auto" by default) keeps it generic | Contract report → Production · `[weaving]` |
| 6 | Shrinkage is shown on **finished beams only**; while beams are still running it says "n/a — beams still running" | Contract report → Balances |

## New in 0.17.8 (third retest)

| # | What it does | Where |
|---|--------------|-------|
| 1 | Output-change audit: a quality change that leaves the **picks/inch the same** stays a WATCH ("quality changed but picks/inch the same, so it does not explain the Nx"); the soft "not enough same-quality history" INFO is only used when the ppi actually changed | Audit → Weaving · `audit/weaving.py` |
| 2 | Thaan shows "—" when the ERP recorded none (NULL), not a false 0, and the total is blank when every gate pass is NULL (mapping no longer `NVL`s thaan to 0) | Contract report → Fabric out · `mappings/dost_textiles_oracle.toml` |

## New in 0.17.9 (installer upgrade fix)

| # | What it does | Where |
|---|--------------|-------|
| 1 | Upgrades no longer fail with "DeleteFile failed; code 5. Access is denied". Before copying files the installer drops a stop-flag the dashboard keep-alive loop watches, ends **and disables** all four tasks (Dashboard, Live, Daily, Morning), kills any orphaned `cmd.exe`/`wscript.exe` loop shell by its command line (the case Task Scheduler's "End" misses), kills `SmartLayer.exe`, then waits until it is really gone | `installer/SmartLayer.iss` (`PrepareToInstall`) |
| 2 | `run_dashboard.cmd`'s loop exits as soon as the `upgrading.flag` appears, instead of relaunching `SmartLayer.exe` 5 s later and re-locking the files | `installer/run_dashboard.cmd` |
| 3 | After the copy the installer re-enables the tasks, removes the flag, and (on an already-configured install) restarts the Dashboard and Live tasks — the existing Interactive/`run_hidden.vbs` logon is preserved, never reset to password logon | `installer/SmartLayer.iss` (`CurStepChanged`) |
| 4 | `CloseApplications=force` + `RestartApplications=no` as an extra Restart-Manager safety net | `installer/SmartLayer.iss` |

## New in 0.18.0 (weaving/monitor audit accuracy, sensor, feed, notifications)

| # | What it does | Where |
|---|--------------|-------|
| 1 | **Over/under-production rebased for a quality change.** Expected = usual × old_ppi/new_ppi × new_pieces/old_pieces; "usual" from the loom's OLD-quality running days and "now" from its NEW-quality running days, dropping stoppage/beam-change days (monitor efficiency below the configurable floor). Fully explained → INFO (out of Needs Attention); still off after adjusting → stays, with the adjusted expected figure. ppi/pieces/quality change read from the ERP contract **or** the scoreboard | Audit → Weaving · `audit/weaving.py` · `[audit] stoppage_efficiency_floor_pct` · `loom_monitor.pieces`/`.style` |
| 2 | **ERP vs loom-monitor compared cut-to-cut**: the window ends at the loom's last ERP cut and starts just after the previous cut, so uncut cloth on the loom is not counted against the ERP | Audit → Looms · `audit/monitor.py` |
| 3 | **New alert — "quality change not entered in the ERP"**: the scoreboard shows a new style/ppi on a loom but after the grace period the ERP still has the old beam/contract and no new beam issue | Audit → Looms · `audit/monitor.py` · `[audit] quality_change_erp_grace_days` |
| 4 | **Sensor-swap twin stops now count stops with no restart time** (both blank → twin; one blank → twin if the next stops also match); episodes show in Needs Attention with the pair, first→last time, count and share, and **Open** shows both looms' stop timeline for the window (CCTV match) | Audit → Looms · `audit/sensor.py` · `api/sensor/episode` |
| 5 | **Live feed / tiles honour today**: the date box is used only when the owner explicitly picks a date, so the feed shows today (or the latest day with entries) instead of yesterday's as-of | Overview · `web/dashboard.html` |
| 6 | **Notifications marked read stay read.** Stable finding keys (type + subject, never dates/ratios); a read item returns only if it escalates; a vanished item is remembered `ack_keep_days` before being forgotten; "show all notifications" lists read items greyed with the read date, Open, and "mark unread"; old acks migrate to the new keys | Overview · `web/data.py`, `web/server.py`, `web/dashboard.html` · `[reports] ack_keep_days` |
| 7 | **Contract report redesigned, party-first.** Pick a **party** first (dropdown with counts); the contract and quality lists are then scoped to that party. **Closing by contract** (one contract) or **Closing by quality** (all of that party's contracts of one quality combined, with a per-contract summary table on top, per-contract sub-totals and a grand total). Sections: a Summary on top, yarn in/out with returns both ways (warp/weft separate), fabric dispatched to the party, a separate **"Fabric to other parties"** tab (any fabric OGP whose `party_code` differs from the contract's — rejection sale or sold elsewhere — kept out of the summary and balance, with a memo line), sets with shrinkage, and the picks check. Totals on every section; PDF + Excel. The other-party rule is by **account code**, not deliver-to text, and `[contract_report] other_party_codes`/`same_party_codes` can pin codes either way. CLI: `report contract --contract <no>` or `report contract --party <code> --quality <q>` | Reports → Contract closing · `textile/contract_report.py` · `api/contracts-all`, `api/contract-report` · `[contract_report]` |

## New in 0.19.0 (weft check, eff-100 timing, sized-beam drill, expense totals)

| # | What it does | Where |
|---|--------------|-------|
| J | **Scoreboard vs ERP weft check.** From the scoreboard picks, the reed width (contract width × panels) and the weft count, estimate the weft the looms wove and compare it to the weft the ERP issued (net to_looms − from_looms), per contract and per month, flagging gaps beyond a configurable tolerance. A line in the contract-report Summary and a "Weft: scoreboard vs ERP" panel in Weaving (click a contract for its monthly split). Picks come from `loom_monitor.picks` (RTLSUM PPROD), or are derived from metres × picks/inch when the monitor gives none. Each scoreboard reading is attributed to **the beam on that loom** (beam_movements to_loom → from_loom run), carrying forward the loom's last ERP contract over the gap days when ERP production is keyed in lumps — so no picks are dropped. The ERP weft is counted over the **same window** the scoreboard covers (from the first monitor date); contracts from before the scoreboard went live show "no scoreboard data", and the list shows contracts active in the last `weft_active_days` first. **Ply yarn** is handled — a 2-ply 36s weighs like an 18s single, so the effective weft count is count ÷ plies, read from the weft yarn name ("36/2") or the scoreboard style ("…DOUB…"). **Panels are capped** to a real reed width (`width_in × panels ≤ max_reed_width_in`), so a stale 3-panel count on a 118" loom reads as 1 panel, not 354" of weft. The waste %, tolerance %, the Ne→length constant, the active window, the reed-width cap and the ply markers all live in config so a denier/tex mill can change them | Weaving + Reports → Contract closing · `textile/weft_check.py`, `web/server.py`, `web/dashboard.html` · `api/weft` · `[weaving] weft_waste_pct`/`weft_tolerance_pct`/`ne_m_per_kg`/`weft_active_days`/`max_reed_width_in`/`weft_ply_markers` · `loom_monitor.picks` |
| K | **"Efficiency over 100%" only in the daily audit.** It never fires from the live/urgent run (a mid-entry shift can read over 100% until the other shift is keyed) and only on days on or before yesterday: WATCH the first daily sighting, CONFIRMED only if it is still over 100% a day later, re-read fresh each run so a corrected entry drops out | Audit → Weaving · `audit/weaving.py` |
| L | **Sized-beam stock drill-down.** Clicking the Weaving "Sized, waiting" tile lists each sized beam waiting for a loom — set, beam, contract, ends, quality, set length, sizer, received date, days waiting — with a beam-count and total-length row. The sizer and received date are taken from `sized_sets` via set no when the beam snapshot leaves them blank | Weaving · `web/data.py`, `web/server.py`, `web/dashboard.html` · `api/weaving/sized-beams` |
| N | **Live-feed production line per date.** The feed's old "Woven +X m" becomes a proper production line per production date gained in the sync — "Production 16-Nov entered: 5,689 m · eff 76.4% · A 5,150 / B 320 / C+CP 219 (rej 9.5%) · 10 looms" — recomputed from `loom_production` so a later sync of the same date updates that line instead of adding another. Efficiency is total metres ÷ metres-at-100 (as the reports use); the grade split comes from first/second/rejected metres with the mapping's labels (A / B / C+CP). Tapping it opens a per-loom table for that date (loom, contract, quality, metres, eff %, A / B / rej) | Overview live feed · `web/data.py`, `web/server.py`, `web/dashboard.html` · `api/feed`, `api/feed/production` |
| M | **Store expense tiles fixed + totals.** The tiles now read the Issued/Returned columns by name (a Head column had shifted the fixed indexes, so the figures were wrong), and the report carries a TOTAL row on screen and in the PDF/Excel | Store → Expense · `web/dashboard.html`, `reports/store_extra.py` |

## New in 0.19.5 (item O - store item location)

| # | What it does | Where |
|---|--------------|-------|
| O+ | **Location-wise filter in the Store tab.** A location dropdown (every rack/shelf in the store, natural-sorted so A-2 is before A-10, plus "(no location)") next to the search box filters the item list to one rack; it combines with the free-text search, and the count line updates to "N item(s) shown in location X" | Store · `web/dashboard.html` |
| O | **Store item location (rack/shelf) everywhere.** A new `items.location` column (Dost `DOSTEX.I.LOC`) shows the bin/rack code (e.g. A-01, O-112) next to the item name in the Store stock tab and its PDF, the unused-stock report (Location column; default sort became most-dead-first in 0.20.0, item U), the store-demand and draft-order reports, the issue-slip lines and the item ledger drawer — and the store search box matches on location too. NULL when the ERP has none, shown as "—" | Store · `schema.py` (`items.location`), `reports/stock.py`, `reports/store_extra.py`, `web/data.py`, `web/dashboard.html` · mapping `items.location` |

## New in 0.20.0 (reliability, data safety, mapping packaging)

| # | What it does | Where |
|---|--------------|-------|
| B1 | **Push outbox - no lost alerts.** Alerts are queued durably and marked sent only after delivery is confirmed, with per-channel isolation and backoff retry; the dashboard shows pushes not delivered in the last 24h | `outbox.py`, `audit.mark_pushed`, `cli.py`, Overview warning |
| Q | **UTF-8 console.** A redirected cp1252 console no longer crashes the first push channel (non-ASCII in a title/body); channels are isolated | `notify.force_utf8_stdout`, `notify.send` |
| B5 | **Nightly backup + power-loss safety.** A dated, consistent copy of the store after each daily sync (SQLite `VACUUM INTO`, kept to N); `synchronous=FULL` so a recent commit is never rolled back on power loss | `store.backup`, `cli._nightly_backup` · `[reports] backup` |
| B4 | **Weekly/monthly jobs with catch-up.** Friday / 1st / 10th jobs run even if the PC was off on the due day; the schedule lives in the app, not a PowerShell date test | `jobs.py`, `run-scheduled` |
| B2 | **Daily-run fixes.** Dropped the non-existent `closing` report; Morning task added to the uninstaller and `status` | `run_daily.cmd`, `SmartLayer.iss`, `cli.cmd_status` |
| B6 | **CI gate.** pytest runs before the Nuitka compile; the built exe is smoke-tested with stdout redirected under code page 1252 | `.github/workflows/build-windows.yml` |
| S | **Versioned mill mappings.** Each mapping declares the app version it was built for; `install-mapping` backs up then replaces the live mapping; CI seals each mill mapping as its own versioned artifact; the dashboard/status/audit warn when the mapping is older than the app | `erp.Mapping`, `cli.install-mapping`, `tools/pack_mill_mappings.py` |
| R/V/B7 | **Over/under-production accuracy.** ERP metres are per-loom-length (panels don't scale them); a single catch-up entry is spread cut-to-cut, not read as one day | `audit/weaving.py` |
| U | **Unused-stock most-dead-first.** Default sort is never-issued first, then unused longest; Location stays a column | `reports/store_extra.unused_stock` |
| T | **Loans settled by journal voucher.** A loan cleared in the accounts by a JV (not salary deduction) shows "settled in accounts (JV)" and drops out of "to recover" | `reports/payroll.loans` · `[payroll] jv_kinds` |
| P | **Idempotent installer tasks.** On upgrade a mill's customized trigger is kept; a signed-in-user task tries an S4U logon (runs when the user is not logged on) and falls back to Interactive | `installer/install_tasks.ps1` |
| B3 | **Reboot-blindness heartbeat.** A large gap since the last live run pushes once "server was off from X to Y - data gap"; every live run pings an external dead-man's-switch so an outside service alerts if the whole PC/app dies | `pushes.heartbeat` · `[monitor] restart_gap_minutes` / `heartbeat_url` |

## New in 0.21.0 (audit accuracy, demand, grade chart, skins)

| # | What it does | Where |
|---|--------------|-------|
| AA | **Qty-0 / rate-0 purchase lines.** A qty-0 receipt line (often a duplicate or the wrong item) is flagged as an entry problem, never as a rate jump, and never used as an item's last rate; a rate-0 line with real qty asks "free or missing from the bill?" | `audit/store.py` |
| AB | **Edits compared per line.** The issue-slip snapshot is keyed per line (slip + item + destination + the ERP's line serial via the new optional `store_issues.line_no`), so adding a second line of the same item is no longer read as the first line's qty/value changing; a line added to an older slip is flagged as a possible backdated insert | `audit/edits.py`, `schema.py` |
| AC | **Store demand: urgency + Excel.** Default order is out-of-stock first then below-usage, both highest-use first; a "Months cover" column; an Excel download beside the PDF | `reports/stock.py`, dashboard |
| AD | **Monthly production by grade.** One stacked bar per month — A-grade / B-grade / Rejection (C+CP) — with total m above the bar, A% inside the band and rejection in red; replaces the old meters-and-A% chart | `web/data.py`, `web/dashboard.html` |
| AE | **Look & feel skins.** A top-bar switch: Classic / iOS (frosted glass) / Android (Material You) — token swaps only, same data and tabs, light and dark for each; remembered per device and per login | `web/dashboard.html`, `web/server.py`, `web/data.py` |
| AF | **Audit false-alarm fixes.** Beams count sized stock of the same quality as a backup; sizing loss is per contract (netted across sizers), moving contracts only; legacy negative fabric stops nagging (no movement > 90 days); rejection = C + CP, B shown separately everywhere | `textile/beams.py`, `textile/pilferage.py`, `audit/yarn.py`, `audit/weaving.py`, Dost mapping |

## New in 0.22.0 (skins to iOS/One UI, grade chart declutter, mark-read fix)

| # | What it does | Where |
|---|--------------|-------|
| AE | **Skins reworked to real OS looks.** Classic (unchanged) / **iOS — Liquid Glass** (translucent glass cards with a specular edge over fixed colour blobs, pill segmented tabs, large title, SF font) / **Samsung — One UI** (renamed from "Android"; flat tonal borderless cards, roomy header, One UI accent, flat bottom-nav tabs, SamsungOne font). Each skin now swaps the **full colour palette** in light **and** dark, mapped to CSS tokens, so every tab, tile, card, list, button, tooltip and Chart.js colour follows it; charts re-render on a skin change. A saved "android" choice migrates to "samsung" | `web/dashboard.html`, `web/server.py`, `web/data.py` |
| AG | **Monthly-grade chart decluttered.** Above each bar: the total in short form ("241k") then the rejection % only, in red; "A 95%" set vertically inside a tall A segment; no per-segment meters; top padding and a y-max rounded up to the next 50k so labels never clip; un-rotated month labels; on a narrow screen the bars scroll sideways (min-width 640) instead of squeezing; the legend sits at the bottom. Tooltip still shows full A/B/Rejection meters, % and total | `web/dashboard.html` |
| AH | **"Mark read" now sticks.** Acknowledged findings moved from a single shared JSON blob to **one row per finding** (new `acks` table, old blob migrated once), so a mark-read click and the daily housekeeping on the threaded server can no longer clobber each other (the lost-update bug). The row is hidden optimistically on click, a failed POST (403 licence / 500 / offline) shows a toast and restores the row, and every `POST /api/ack` is logged with its id and status | `store.py`, `web/server.py`, `web/data.py`, `web/dashboard.html` |
| AE+ | **Skin colour everywhere + grouped lists.** The iOS/Samsung skins now colour the live UI, not just the palette: Samsung tabs are a filled accent pill on a tonal track; each tile's emoji sits in a tinted chip (money=accent, stock/yarn=orange, mill=green); switches take the accent; the Samsung Overview gets a tall One UI header. **Needs-attention** is one grouped card — each row a severity circle (iOS: rounded square) + bold title + muted detail line + a compact round ✓ button (and a chevron on iOS), with iOS inset separators; the read list matches at reduced opacity with ↩. Classic is untouched | `web/dashboard.html` |
| AE-theme | **Auto (sunrise/sunset) theme.** The 🌓 control is now Auto / Light / Dark (Auto is the default for new users), saved per device and per login (`/api/ui/theme`). Auto computes today's sunrise/sunset with the NOAA formula (no library) from `[dashboard] latitude`/`longitude` — dark between sunset and sunrise, re-checked every 5 min and on tab focus, charts rebuild on a flip, tooltip shows the next switch; missing coordinates fall back to the device setting. Works in all three skins | `web/dashboard.html`, `web/server.py`, `web/data.py`, config |
| AK | **Colour-scheme picker removed.** The old 🎨 "Colour scheme" (6 cloth palettes) is gone; the only visual choice is Look & feel (Classic / iOS / Samsung). Any saved `sl-scheme` is ignored | `web/dashboard.html` |
| AL | **Dispatched = this month; Dispatch history tile.** The Dispatched tile and its drill show only THIS MONTH's dispatches (date, gate pass, party, quality, meters + month total) via a new `api/fabric/dispatches`; a separate "Dispatch history" tile opens the full produced/dispatched/in-stock history | `web/data.py`, `web/server.py`, `web/dashboard.html` |
| AM | **Cost/metre totals + taxes (everywhere).** The cost drill shows the month's TOTAL rupees beside each Rs/m figure, then a Taxes block — **sales tax** (a pass-through: a cost only to the extent input tax paid exceeds output tax collected — the un-recovered excess; otherwise shown as "payable to FBR, not a cost"; `[cost] sales_tax_cost = "net"` restores signed output − input), **further tax on sales** and **withholding tax** — a Total taxes line and a Grand total incl. sizing and taxes. The taxes now flow through the whole feature: the **tile meta** shows "… incl. sizing & taxes", the **monthly history** gains "Taxes/m" and "Cost/m incl. taxes" columns (each month's own figures), and the **P&L** gains a matching "Taxes" group so it agrees with the cost. Tax accounts are excluded from the generic expense buckets so `further` isn't double-counted. Amounts come from GL accounts named generically in `[cost]` (output/input/further/withholding, each with a side + voucher-type filter); empty → "set the accounts" | `reports/finance.py`, `web/dashboard.html`, config |
| AN | **Hide amounts (per-tile + master eye).** Every money tile has its own 👁 that masks only that tile; a labelled master eye in the header ("👁 Hide amounts" / "🙈 Show amounts") hides all money at once (Overview + Finance tiles → `Rs ••••••`, Finance tables blur) or shows all (clearing the per-tile set). Visible by default, remembered per device (`sl-hideamt` + `sl-hide-tiles`); a hidden tile's drill is blocked with a toast. (Supersedes AJ) | `web/dashboard.html` |
| AI | **Auto (sunrise/sunset) theme.** 🌓 Auto / Light / Dark; Auto is the default and computes sunrise/sunset (NOAA, no library) from `[dashboard] latitude`/`longitude`, falling back to the device setting; saved per device and login | `web/dashboard.html`, `web/server.py`, `web/data.py`, config |
| AO | **Opening animation.** The weave now resolves into a clearly visible S logo + "Smart Layer" wordmark, held ~0.6s before fading (~2.6s total, once per session) | `web/dashboard.html` |
| AP | **Replay on open.** After the intro the dashboard replays today's latest cash movement (notes on the Cash tile) and latest dispatch/receipt (the truck) once per session, skipping each if there was no such event today; respects reduced-motion | `web/data.py`, `web/dashboard.html` |

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
