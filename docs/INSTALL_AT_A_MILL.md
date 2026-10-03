# Installing at a mill - pilot playbook

Project Smart Layer by Azam Puri. One page per stage; follow it in order.

---

## 1. Before the visit (by phone / WhatsApp)

Ask the mill owner or their IT person:

| Question | Why |
|---|---|
| Which ERP software, and which database (Oracle, SQL Server, MySQL...)? Which version? | Picks the mapping and the driver. Oracle 11g or older needs Oracle Client on the server. |
| Is there a Windows PC or server that is always on and can reach the database? | Smart Layer runs there (the database server itself is fine). |
| Can the ERP vendor / IT create a **read-only** database user? | Smart Layer never needs write access. |
| A list of the ERP's tables or views for: items, stock, GRN, issue slips, ledger, looms, production, contracts, yarn gate passes, fabric gate passes | Needed to write the mapping (one day of work for a new ERP; minutes if the ERP is already mapped). |
| How do they close contracts? (divisor 800/840, bag weight, who bears rejection, closing counts per yarn) | Goes into the closing settings - the product follows the mill's own method. |
| Who should get phone alerts? | Owner / GM install the free **ntfy** app. |

**If the ERP is new to you:** get read access first (or a backup copy), write `mappings/<erp>.toml`
(see [CONNECTING_AN_ERP.md](CONNECTING_AN_ERP.md)) and test it with `python -m smart_layer check` before the visit.

---

## 2. Read-only database user

Produce the exact SQL from the mapping and give it to the IT person / ERP vendor:

```
python -m smart_layer grants --user smartlayer_ro
```

They run it as a database administrator and choose the password. You will type that password in the setup
wizard; it is stored only in `.env` on their server.

---

## 3. Installation day (about 30 minutes)

1. Copy the **Smart Layer** folder to the server, e.g. `C:\SmartLayer` (from the release ZIP).
2. Double-click **`Install Smart Layer.bat`** and allow Administrator access.
3. Answer the questions:
    - mill name, currency
    - database type, address, read-only user and password
    - mapping file for their ERP
    - dashboard on the mill network: **yes**, and a dashboard password (write it down for the owner)
    - phone alerts: **yes** - note the ntfy topic it prints
    - Claude API key: Enter to skip if chat is not part of the package yet
4. The installer tests every table, does the first sync, schedules the daily run (07:30) and starts the
   dashboard. All 13 lines of the test should say **ok**.
5. Open the dashboard on the owner's phone: `http://<server address>:8080` (the installer prints it),
   any user name + the dashboard password. Add it to the phone's home screen.
6. On the owner's phone: ntfy app → **+** → subscribe to the topic the wizard printed.

---

## 4. Check the numbers with the owner (do not skip)

Pick yesterday (or the last day with production keyed) and compare with the ERP's own reports:

| Smart Layer | Compare with | Typical cause if different |
|---|---|---|
| `report production --date D`: total meters, looms | ERP *Daily Production* report | rows missing in the production view / wrong date column |
| Rejection % | ERP *Grade %* (100 − grade %) | which grades the mill counts as rejection (e.g. B + C + CP) - change `rejected_m` in the mapping |
| Efficiency % | ERP *Loom Wise Efficiency* total | give `efficiency_pct` and `meters_at_100` from the ERP's efficiency view |
| `report closing` for one finished contract | the mill's own closing sheet | closing counts per yarn, divisor, yarn booked under two codes (`yarn_groups`), basis |
| `report pilferage` | what the owner knows about the yarn store | per-party weft comparison is the default; one contract's construction may be wrong in the ERP |
| `report stock` for a few items | a physical count / ERP stock report | stock function or opening + receipts − issues in the mapping |

Fix the mapping or settings, run `python -m smart_layer sync` and compare again until the owner agrees.
Write down which figures were checked - it is the acceptance record for the pilot.

---

## 5. Handover

- Show the owner: dashboard tiles, *Needs attention*, contract closing, yarn flags, the date picker.
- Explain the grades: **ACTION** do something today · **CONFIRMED** a definite error in the data · **WATCH** have a look.
- Things the owner says are normal practice go into `[audit] dismiss` so they stop being pushed.
- Reports are saved every day in `C:\SmartLayer\output` (CSV opens in Excel); weekly stock, reorder,
  closing and pilferage on Fridays; full monthly audit on the 10th.

---

## 6. Support (remotely or on site)

| Situation | What to do |
|---|---|
| Is it working? | `python -m smart_layer status` - last sync, dashboard, scheduled tasks |
| Daily run problems | `output\daily_run_log.txt` |
| Dashboard does not open | `output\dashboard_log.txt`; Task Scheduler → *Smart Layer Dashboard* → Run |
| Change answers (password, alerts, database) | double-click the installer again, or `python -m smart_layer setup` |
| Remove | `uninstall_windows.ps1` as Administrator (keeps the folder and data) |

Use the `python.exe` path in `python_path.txt` if `python` is not recognised in PowerShell.
