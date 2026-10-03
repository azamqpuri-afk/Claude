"""
40_business_dashboard.py
Project : Dost Textiles - Business Dashboard (daily refresh)
Purpose : Month-by-month figures from Jan-2014 (old ERP, schema DOSTT) through
          today (new ERP, DOSTEX), merged by merged_ledger.py, written as one
          JSON file plus a self-contained HTML dashboard into the Drive-synced
          reports folder:
            1. Sales - every revenue head + meters invoiced + per-customer
            2. Receivables / payables - month-end balances + per party
            3. Cash in hand and bank - month-end per account, money in/out
            4. Loom efficiency and grade mix - monthly + per loom per month
            5. Expenses - monthly by expense head
            6. Profit summary - revenue, expenses, profit per month
          The page lets the viewer pick any month and see the trend around it.

Sparse series ("pairs") are [[month_index, value], ...] listing only the
months where the value changes (balances) or is non-zero (flows); the page
forward-fills balances and treats missing flow months as 0. A balance series
also carries "from"/"to" month indexes - an old-ERP account only exists up to
Sep-2023 and a new-ERP account only from Oct-2023.

Efficiency is weighted: SUM(TOTAL_MTR)/SUM(EFF_100)*100. All SELECT-only.
"""

import configparser
import json
import shutil
import sys
import traceback
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import oracledb

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import merged_ledger as ml  # noqa: E402

CONFIG_PATH = SCRIPT_DIR / "report_config.ini"
TEMPLATE_PATH = SCRIPT_DIR / "business_dashboard_template.html"
LOCAL_OUT_DIR = SCRIPT_DIR / "business_dashboard"
STATUS_LOG = SCRIPT_DIR / "business_dashboard_status_log.txt"
DATA_NAME = "business_dashboard_data.json"
HTML_NAME = "business_dashboard.html"
FIRST_MONTH = ml.FIRST_MONTH

ACCOUNT_NAMES_SQL = "SELECT ac, an FROM dostex.ach"
LAST_DATES_SQL = """
    SELECT (SELECT MAX(td) FROM dostex.wv_prod_v),
           (SELECT MAX(vd) FROM dostex.acl WHERE vt <> 'OPN')
    FROM dual
"""

SALES_ORDER = ["Conversion", "Fabric sale", "Rejection sale", "Yarn sale", "Bardana",
               "Scrap", "Waste & misc", "Fixed asset sale", "Other income"]


def revenue_category(ac, head, vt):
    if vt == "OGP_YARN" or ac == "350105002":
        return "Yarn sale"
    return {"350105": "Conversion", "350104": "Fabric sale", "350106": "Rejection sale",
            "350202": "Bardana", "350203": "Scrap", "350201": "Waste & misc",
            "350205": "Fixed asset sale"}.get(head, "Other income")


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_PATH)
    return cfg


def connect(cfg):
    lib = cfg.get("oracle", "client_lib_dir", fallback="").strip()
    if lib:
        oracledb.init_oracle_client(lib_dir=lib)
    else:
        oracledb.init_oracle_client()
    return oracledb.connect(user=cfg["oracle"]["user"],
                            password=cfg["oracle"]["password"],
                            dsn=cfg["oracle"]["dsn"])


def rows(cur, sql, **binds):
    cur.execute(sql, binds)
    return cur.fetchall()


def month_list(first, last):
    y, m = map(int, first.split("-"))
    ly, lm = map(int, last.split("-"))
    out = []
    while (y, m) <= (ly, lm):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def flow_pairs(series):
    return [[i, round(v)] for i, v in enumerate(series) if v and abs(v) >= 1]


def balance_pairs(series):
    """Change points of a balance series that is None outside its era."""
    idx = [i for i, v in enumerate(series) if v is not None]
    if not idx:
        return None
    pairs, prev = [], object()
    for i in idx:
        v = round(series[i])
        if v != prev:
            pairs.append([i, v])
            prev = v
    return {"from": idx[0], "to": idx[-1], "pairs": pairs}


def build(cur):
    months = month_list(FIRST_MONTH, date.today().strftime("%Y-%m"))
    mi = {m: i for i, m in enumerate(months)}
    L = ml.Ledger(cur, rows, months)
    names = L.names

    # 1. Sales by category + profit summary
    sales = defaultdict(lambda: [0.0] * len(months))
    revenue = [0.0] * len(months)
    costs = [0.0] * len(months)
    exp_heads = defaultdict(lambda: [0.0] * len(months))
    for era, m, ac, an, h, vt, tbl, amt in L.records:
        if m not in mi:
            continue
        i = mi[m]
        if h.startswith("35"):
            sales[revenue_category(ac, h, vt)][i] += -amt
            revenue[i] += -amt
        elif h[:2] in ("40", "45", "55"):
            costs[i] += amt
            exp_heads[h][i] += amt
    sales_series = {c: [round(v) for v in sales[c]] for c in SALES_ORDER if c in sales}

    meters = ml.merge_monthly(rows(cur, ml.OLD_METERS_SQL), rows(cur, ml.NEW_METERS_SQL), months)

    cust = defaultdict(lambda: [0.0] * len(months))
    for sql, old in ((ml.OLD_INVOICE_SQL, True), (ml.NEW_INVOICE_SQL, False)):
        for m, an, amt in rows(cur, sql):
            if m in mi and ml.is_old(m) == old:
                cust[(an or "").strip()][mi[m]] += ml.f(amt)
    customers = [{"name": n, "pairs": flow_pairs(s)} for n, s in cust.items() if sum(s) >= 50000]

    # 2 & 3. Balances
    hb = L.head_balances()

    def group_bal(prefix, sign=1):
        tot = [0.0] * len(months)
        for h, s in hb.items():
            if h.startswith(prefix):
                tot = [a + b for a, b in zip(tot, s)]
        return [round(sign * v) for v in tot]

    parties = []
    for prefix, kind in (("3003", "customer"), ("2001", "supplier"), ("2002", "accrued")):
        for leaf in L.leaf_balances((prefix,)):
            bp = balance_pairs(leaf["series"])
            if bp:
                parties.append({"name": leaf["name"], "kind": kind, "era": leaf["era"], **bp})
    cash_accounts = []
    for leaf in L.leaf_balances(("3008",), min_abs=1):
        bp = balance_pairs(leaf["series"])
        if bp:
            cash_accounts.append({"name": leaf["name"], "kind": "Cash" if leaf["head"] == "300801" else "Bank",
                                  "era": leaf["era"], **bp})
    flows = {}
    for m, i_, o_ in rows(cur, ml.OLD_CASH_FLOW_SQL):
        if ml.is_old(m):
            flows[m] = (ml.f(i_), ml.f(o_))
    for m, i_, o_ in rows(cur, ml.NEW_CASH_FLOW_SQL):
        if not ml.is_old(m):
            flows[m] = (ml.f(i_), ml.f(o_))

    # 4. Looms
    loom = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0]))  # name -> month -> mtr, m100, good, tot
    grades = {g: [0.0] * len(months) for g in ["A", "A2", "B", "C", "CP"]}
    for sql, old in ((ml.OLD_EFF_SQL, True), (ml.NEW_EFF_SQL, False)):
        for m, ln, mtr, m100 in rows(cur, sql):
            if m in mi and ml.is_old(m) == old:
                cell = loom[ln][mi[m]]
                cell[0] += ml.f(mtr); cell[1] += ml.f(m100)
    for sql, old in ((ml.OLD_GRADE_SQL, True), (ml.NEW_GRADE_SQL, False)):
        for m, ln, a, a2, b, c, cp in rows(cur, sql):
            if m in mi and ml.is_old(m) == old:
                i = mi[m]
                for g, v in zip(["A", "A2", "B", "C", "CP"], (a, a2, b, c, cp)):
                    grades[g][i] += ml.f(v)
                cell = loom[ln][i]
                cell[2] += ml.f(a) + ml.f(a2)
                cell[3] += ml.f(a) + ml.f(a2) + ml.f(b) + ml.f(c) + ml.f(cp)
    mtr = [0.0] * len(months); m100 = [0.0] * len(months); running = [0] * len(months)
    looms = []
    for ln, bym in loom.items():
        pairs = []
        for i, (a, b, g, t) in sorted(bym.items()):
            mtr[i] += a; m100[i] += b
            if b:
                running[i] += 1
            pairs.append([i, round(a), round(b), round(g), round(t)])
        looms.append({"loom": ln, "pairs": pairs})
    looms.sort(key=lambda x: (len(x["loom"]), x["loom"]))

    last_prod, last_ledger = rows(cur, LAST_DATES_SQL)[0]

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_production_date": last_prod.strftime("%Y-%m-%d") if last_prod else None,
        "last_ledger_date": last_ledger.strftime("%Y-%m-%d") if last_ledger else None,
        "months": months,
        "switch_index": mi.get(ml.SWITCH_MONTH),
        "pnl": {"revenue": [round(v) for v in revenue], "expenses": [round(v) for v in costs],
                "profit": [round(r - c) for r, c in zip(revenue, costs)]},
        "sales": {"series": sales_series, "meters_invoiced": meters, "customers": customers},
        "balances": {
            "receivables_net": group_bal("3003"),
            "payables_net": group_bal("2001", -1),
            "accrued_net": group_bal("2002", -1),
            "cash_bank_total": group_bal("3008"),
            "parties": parties,
        },
        "cash": {"accounts": cash_accounts,
                 "inflow": [round(flows.get(m, (0, 0))[0]) for m in months],
                 "outflow": [round(flows.get(m, (0, 0))[1]) for m in months]},
        "production": {
            "grades": {g: [round(v) for v in s] for g, s in grades.items()},
            "mtr": [round(v) for v in mtr], "mtr100": [round(v) for v in m100],
            "looms_running": running, "looms": looms,
        },
        "expenses": {"heads": [{"code": h, "name": names.get(h, h), "series": [round(v) for v in s]}
                               for h, s in sorted(exp_heads.items()) if any(abs(v) >= 1 for v in s)]},
    }


LEDGER_SQL = """
    SELECT l.ac, a.an, TO_CHAR(l.vd,'YYYY-MM-DD'), l.vt, l.vno, l.naration, l.amt
    FROM   dostex.acl l JOIN dostex.ach a ON a.ac = l.ac
    WHERE  SUBSTR(l.ac,1,4) IN ('3003','2001','2002','3008') AND NVL(l.amt,0) <> 0
    ORDER BY l.ac, CASE WHEN l.vt = 'OPN' THEN 0 ELSE 1 END, l.vd, l.vt, l.vno
"""
LEDGER_GROUPS = {"3003": "conversion", "2001": "store", "2002": "accrued", "3008": "bank"}   # 3008 = cash (300801) and bank accounts
LEDGER_NAME = "business_dashboard_ledgers.json"


# daybook: every cash / bank / journal voucher of the last 90 days, and a count of the other (system) entries per day
DAYBOOK_SQL = """
    SELECT TO_CHAR(l.vd,'YYYY-MM-DD'), l.vt, l.vno, l.ac, a.an, l.naration, l.amt, l.usern, TO_CHAR(l.feedd,'YYYY-MM-DD HH24:MI')
    FROM   dostex.acl l LEFT JOIN dostex.ach a ON a.ac = l.ac
    WHERE  l.vd >= TRUNC(SYSDATE) - 90 AND l.vd <= TRUNC(SYSDATE) AND NVL(l.amt,0) <> 0 AND l.vt IN ('CP','CR','BP','BR','JV')
    ORDER BY l.vd, l.vt, l.vno, l.sr_no
"""
DAYBOOK_OTHER_SQL = """
    SELECT TO_CHAR(vd,'YYYY-MM-DD'), vt, COUNT(DISTINCT vno)
    FROM   dostex.acl WHERE vd >= TRUNC(SYSDATE) - 90 AND vd <= TRUNC(SYSDATE) AND NVL(amt,0) <> 0 AND vt NOT IN ('CP','CR','BP','BR','JV','OPN')
    GROUP BY TO_CHAR(vd,'YYYY-MM-DD'), vt
"""


def build_daybook(cur):
    """Voucher = [date, type, number, keyed by, keyed at, amount (total debits), [[account, name, amount, narration], ...]]"""
    vch = {}
    for d, vt, vno, ac, an, nar, amt, user, feed in rows(cur, DAYBOOK_SQL):
        v = vch.setdefault((d, vt, vno), [d, vt, vno, (user or "").strip(), feed, 0.0, []])
        a = round(float(amt), 2)
        if a > 0:
            v[5] = round(v[5] + a, 2)
        v[6].append([ac, (an or ac).strip(), a, " ".join((nar or "").split())[:80]])
    other = defaultdict(dict)
    for d, vt, n in rows(cur, DAYBOOK_OTHER_SQL):
        other[d][vt] = int(n)
    return {"vouchers": list(vch.values()), "other": other}


def build_ledgers(cur):
    """Party, cash-in-hand and bank ledgers (new ERP, Oct-2023 onward, with the opening balance carried from the old ERP).
    Line = [date, voucher type, voucher no, narration, amount (+ debit / - credit)]."""
    accs = {}
    for ac, an, d, vt, vno, nar, amt in rows(cur, LEDGER_SQL):
        grp = "cash" if ac.startswith("300801") else LEDGER_GROUPS[ac[:4]]
        a = accs.setdefault(ac, {"ac": ac, "name": (an or ac).strip(), "group": grp, "lines": []})
        a["lines"].append([d, vt, vno, " ".join((nar or "").split())[:90], round(float(amt), 2)])
    return {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "accounts": sorted(accs.values(), key=lambda a: (a["group"], a["name"])), "daybook": build_daybook(cur)}


ERP_DAY_SQL = """
    SELECT TO_CHAR(e.td,'YYYY-MM-DD'), SUM(e.total_mtr), SUM(e.eff_100), COUNT(DISTINCT e.lcode)
    FROM dostex.wv_eff_v e WHERE e.td >= TRUNC(SYSDATE) - 120 GROUP BY TO_CHAR(e.td,'YYYY-MM-DD')
"""
SCB_DB_DAILY = "scb_daily_from_db.csv"   # written by 74_scoreboard_data.py from the scoreboard database (DATE, SCB_EFF, SCB_PRODUCTION, SCB_RPM)


def scb_db_daily(reports_dir):
    """Scoreboard daily efficiency / meters / RPM taken from the scoreboard database copy - fills days with no summary file."""
    out = {}
    p = Path(reports_dir) / SCB_DB_DAILY
    if p.exists():
        for v in read_csv_rows(p):
            try:
                out[v["DATE"]] = (float(v["SCB_EFF"]), float(v["SCB_PRODUCTION"]), float(v["SCB_RPM"]))
            except (KeyError, ValueError):
                continue
    return out


LOOM_LAST_SQL = """
    SELECT l.lname, TO_CHAR(e.td,'YYYY-MM-DD'), MAX(e.quality), SUM(e.total_mtr), SUM(e.eff_100), SUM(e.tot_shifts), MAX(e.rpm)
    FROM dostex.wv_eff_v e JOIN dostex.looms l ON l.lcode = e.lcode
    WHERE (e.lcode, e.td) IN (SELECT lcode, MAX(td) FROM dostex.wv_eff_v WHERE td >= TRUNC(SYSDATE) - 400 GROUP BY lcode)
    GROUP BY l.lname, e.td
"""


ROLLS_SQL = """
    SELECT l.lname,
           COUNT(DISTINCT CASE WHEN p.td >= TRUNC(SYSDATE,'MM') THEN p.trans_no END),
           COUNT(DISTINCT CASE WHEN p.td >= TRUNC(SYSDATE) - 30 THEN p.trans_no END),
           TO_CHAR(MAX(p.td),'YYYY-MM-DD')
    FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode
    WHERE p.td >= TRUNC(SYSDATE) - 400
    GROUP BY l.lname
"""


ERP_LOOM30_SQL = """
    SELECT l.lname, SUM(e.total_mtr), SUM(e.eff_100)
    FROM dostex.wv_eff_v e JOIN dostex.looms l ON l.lcode = e.lcode
    WHERE e.td >= TRUNC(SYSDATE) - 30 GROUP BY l.lname
"""
# fabric meters (loom meters x pieces woven side by side) - the figure that matches dispatch and the scoreboard
ERP_FAB30_SQL = """
    SELECT l.lname, SUM(p.total_mtr)
    FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode
    WHERE p.td >= TRUNC(SYSDATE) - 30 GROUP BY l.lname
"""
# RPM typed into the ERP production entry - shown only as a comparison line next to the scoreboard's live RPM
ERP_RPM_SQL = """
    SELECT l.lname, TO_CHAR(e.td,'YYYY-MM-DD'), ROUND(AVG(e.rpm))
    FROM dostex.wv_eff_v e JOIN dostex.looms l ON l.lcode = e.lcode
    WHERE e.td >= :d0 AND e.rpm > 0 GROUP BY l.lname, TO_CHAR(e.td,'YYYY-MM-DD')
"""


def read_csv_rows(p):
    lines = [x for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
    head = [h.strip().upper() for h in lines[0].split(",")]
    return [dict(zip(head, [x.strip() for x in ln.split(",")])) for ln in lines[1:]]


def file_date(p):
    try:
        return date.fromisoformat(p.stem.split(" ")[0].rsplit("_", 1)[1])
    except ValueError:
        return None


def build_rpm(cur, reports_dir):
    """Shed RPM from the scoreboard summary files (all history) and each loom's RPM: scoreboard per-loom RPM
    when its per-loom file carries an RPM column, otherwise the RPM recorded in the ERP production entry."""
    shed = {}
    for p in Path(reports_dir).glob("scoreboard_summary_*.csv"):
        try:
            for v in read_csv_rows(p):
                rpm_ = float(v.get("SCB_RPM") or 0)
                if 50 < rpm_ < 300 and float(v.get("SCB_EFF") or 0) > 0:   # skip days the scoreboard was not recording
                    shed[v["DATE"]] = rpm_
        except Exception:
            continue
    for d, (eff_, _m, rpm_) in scb_db_daily(reports_dir).items():      # fill from the scoreboard database
        if d not in shed and eff_ > 0 and 50 < rpm_ < 300:
            shed[d] = rpm_
    # per-loom RPM captured live by the scoreboard: an RPM column in scb_efficiency_loomwise_<d>.csv, or a separate
    # scb_rpm_loomwise_<d>.csv (LOOM_NO, RPM). The ERP RPM is typed in by hand, so it is never used here.
    looms = {}
    for pattern in ("scb_efficiency_loomwise_*.csv", "scb_rpm_loomwise_*.csv"):
        for p in Path(reports_dir).glob(pattern):
            d = file_date(p)
            if not d:
                continue
            try:
                rows_ = read_csv_rows(p)
            except Exception:
                continue
            for v in rows_:
                rpm_ = v.get("RPM") or v.get("AVG_RPM")
                try:
                    rpm_ = float(rpm_) if rpm_ else None
                except ValueError:
                    rpm_ = None
                if rpm_ and 50 < rpm_ < 400:
                    looms.setdefault(f"LOOM # {int(float(v['LOOM_NO']))}", {})[d.isoformat()] = rpm_
    first = min(shed) if shed else date.today().replace(day=1).isoformat()
    erp = {}
    for ln, d, rpm in rows(cur, ERP_RPM_SQL, d0=datetime.fromisoformat(first)):
        erp.setdefault(ln, {})[d] = float(rpm)
    by_day = {}
    for s in erp.values():
        for d, v in s.items():
            by_day.setdefault(d, []).append(v)
    key = lambda x: (len(x[0]), x[0])  # noqa: E731
    return {"shed": sorted([[d, v] for d, v in shed.items()]), "loom_source": "scoreboard" if looms else "none",
            "erp_shed": sorted([[d, round(sum(v) / len(v))] for d, v in by_day.items()]),
            "erp_looms": {ln: sorted([[d, v] for d, v in s.items()]) for ln, s in sorted(erp.items(), key=key)},
            "looms": {ln: sorted([[d, v] for d, v in s.items()]) for ln, s in sorted(looms.items(), key=lambda x: (len(x[0]), x[0]))}}


def build_30day(cur, reports_dir):
    """Last 30 days, ERP vs the scoreboard PC's per-loom files (scb_efficiency_loomwise_<date>.csv:
    LOOM_NO, EFF, TOTAL_METERS). Scoreboard efficiency over the period = meters / (meters / eff), per loom and mill."""
    start = date.fromordinal(date.today().toordinal() - 30)
    looms = {}
    new = lambda: {"erp_mtr": 0.0, "erp_m100": 0.0, "erp_fab": 0.0, "scb_mtr": 0.0, "scb_pot": 0.0, "scb_days": set()}  # noqa: E731
    for ln, m, h in rows(cur, ERP_LOOM30_SQL):
        x = looms.setdefault(ln, new()); x["erp_mtr"], x["erp_m100"] = float(m or 0), float(h or 0)
    for ln, m in rows(cur, ERP_FAB30_SQL):
        looms.setdefault(ln, new())["erp_fab"] = float(m or 0)
    days = []
    for p in sorted(Path(reports_dir).glob("scb_efficiency_loomwise_*.csv")):
        try:
            d = date.fromisoformat(p.stem.rsplit("_", 1)[1])
        except ValueError:
            continue
        if d < start:
            continue
        try:
            lines = [x for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
        except Exception:
            continue
        head = [h.strip().upper() for h in lines[0].split(",")]
        added = False
        for ln in lines[1:]:
            v = dict(zip(head, [x.strip() for x in ln.split(",")]))
            try:
                n, eff, m = int(float(v["LOOM_NO"])), float(v["EFF"]), float(v["TOTAL_METERS"])
            except (KeyError, ValueError):
                continue
            if m <= 0 or eff <= 0:     # loom not recorded that day
                continue
            added = True
            x = looms.setdefault(f"LOOM # {n}", new())
            x["scb_mtr"] += m
            x["scb_pot"] += m / (eff / 100) if eff > 0 else 0
            x["scb_days"].add(d)
        if added:
            days.append(d.isoformat())
    # [loom, erp loom m, erp m@100%, scb m, scb m@100%, scb days, erp fabric m]
    rows_out = [[ln, round(x["erp_mtr"]), round(x["erp_m100"]), round(x["scb_mtr"]), round(x["scb_pot"]), len(x["scb_days"]), round(x["erp_fab"])]
                for ln, x in looms.items()]
    rows_out.sort(key=lambda r: (len(r[0]), r[0]))
    return {"from": start.isoformat(), "to": date.today().isoformat(), "scb_days": sorted(days), "looms": rows_out}


def build_floor(cur, reports_dir):
    """Daily ERP production vs the scoreboard PC's daily summary, and each loom's last ERP entry."""
    days = {}
    for d, m, m100, n in rows(cur, ERP_DAY_SQL):
        days[d] = {"erp_mtr": round(float(m or 0)), "erp_m100": round(float(m100 or 0)), "erp_looms": int(n)}
    for p in sorted(Path(reports_dir).glob("scoreboard_summary_*.csv")):
        try:
            lines = [x for x in p.read_text(encoding="utf-8-sig").splitlines() if x.strip()]
            head = [h.strip() for h in lines[0].split(",")]
            for ln in lines[1:]:
                v = dict(zip(head, [x.strip() for x in ln.split(",")]))
                eff_, rpm_ = float(v.get("SCB_EFF") or 0), float(v.get("SCB_RPM") or 0)
                if eff_ <= 0:          # scoreboard PC not recording that day - treat as no data
                    continue
                days.setdefault(v["DATE"], {}).update(
                    scb_eff=eff_, scb_mtr=float(v.get("SCB_PRODUCTION") or 0), scb_rpm=rpm_ if 50 < rpm_ < 300 else None)
        except Exception:
            continue
    for d, (eff_, mtr_, rpm_) in scb_db_daily(reports_dir).items():      # days the scoreboard PC sent no summary file
        if eff_ > 0 and days.get(d, {}).get("scb_eff") is None:
            days.setdefault(d, {}).update(scb_eff=eff_, scb_mtr=mtr_, scb_rpm=rpm_ if 50 < rpm_ < 300 else None)
    first = (date.today().toordinal() - 120)      # 120 days: the Mill Floor chart shows 60 days with a 30-day moving average
    out = [[d, x.get("erp_mtr"), x.get("erp_m100"), x.get("erp_looms"), x.get("scb_eff"), x.get("scb_mtr"), x.get("scb_rpm")]
           for d, x in sorted(days.items()) if date.fromisoformat(d).toordinal() >= first]
    looms = [[ln, d, (q or "").strip(), round(float(m or 0)), round(float(h or 0)), float(sh or 0), rpm]
             for ln, d, q, m, h, sh, rpm in rows(cur, LOOM_LAST_SQL)]
    looms.sort(key=lambda x: (len(x[0]), x[0]))
    # rolls checked = production entries (each entry is a roll taken off the loom and graded)
    rolls = {ln: [int(a or 0), int(b or 0), d] for ln, a, b, d in rows(cur, ROLLS_SQL)}
    for x in looms:
        x.extend(rolls.get(x[0], [0, 0, None]))
    return {"days": out, "looms_last": looms}


def log(msg):
    with open(STATUS_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def main():
    cfg = load_config()
    out_dir = Path(cfg["output"]["folder"])
    try:
        con = connect(cfg)
        data = build(con.cursor())
        data["floor"] = build_floor(con.cursor(), out_dir)
        data["floor"]["last30"] = build_30day(con.cursor(), out_dir)
        data["floor"]["rpm"] = build_rpm(con.cursor(), out_dir)
        ledgers = build_ledgers(con.cursor())
        con.close()

        LOCAL_OUT_DIR.mkdir(exist_ok=True)
        payload = json.dumps(data, separators=(",", ":"))
        lpayload = json.dumps(ledgers, separators=(",", ":"))
        (LOCAL_OUT_DIR / DATA_NAME).write_text(payload, encoding="utf-8")
        (LOCAL_OUT_DIR / LEDGER_NAME).write_text(lpayload, encoding="utf-8")
        # [dashboard] drive_file_id / ledger_file_id = Drive IDs of the synced JSON
        # files; the published artifact reads them through the viewer's Drive connector.
        file_id = cfg.get("dashboard", "drive_file_id", fallback="__DRIVE_FILE_ID__")
        ledger_id = cfg.get("dashboard", "ledger_file_id", fallback="__LEDGER_FILE_ID__")
        html = (TEMPLATE_PATH.read_text(encoding="utf-8")
                .replace("/*__DASHBOARD_DATA__*/null", payload.replace("</", "<\\/"))
                .replace("/*__LEDGER_DATA__*/null", lpayload.replace("</", "<\\/"))
                .replace("__DRIVE_FILE_ID__", file_id)
                .replace("__LEDGER_FILE_ID__", ledger_id))
        (LOCAL_OUT_DIR / HTML_NAME).write_text(html, encoding="utf-8")

        # Overwrite in place so Drive keeps the same file IDs.
        for name in (DATA_NAME, LEDGER_NAME, HTML_NAME):
            shutil.copyfile(LOCAL_OUT_DIR / name, out_dir / name)

        log(f"OK months={len(data['months'])} bytes={len(payload)} last_prod={data['last_production_date']} "
            f"last_ledger={data['last_ledger_date']}")
        return 0
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
