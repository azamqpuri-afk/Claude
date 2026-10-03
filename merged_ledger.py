"""
merged_ledger.py
One history across both ERPs, for the dashboard (40_) and the financial
statements (41_):
  - old ERP, schema DOSTT : Jan-2014 .. 30-Sep-2023 (ledger view DOSTT.ACL)
  - new ERP, schema DOSTEX: 01-Oct-2023 .. today     (ledger view DOSTEX.ACL)

The new ERP was started with the old ERP's closing trial balance as its
opening balances (DOSTEX ABALS, vt='OPN'); these were checked head by head
against DOSTT's balances at 30-Sep-2023 and agree, apart from a few
reclassifications at go-live that net to zero (see the reconciliation sheet in
the balance-sheet workbook). So:
  * movements (P&L, cash flow, sales) = old ledger up to Sep-2023 + new
    ledger from Oct-2023 excluding the OPN rows;
  * balances at a date <= 30-Sep-2023 come from the old ledger, balances
    after come from the new ledger including its OPN rows.
Old accounts are mapped onto new level-3 heads by old_erp_account_map.

All queries are SELECT-only. DOSTEX has read access to DOSTT's tables.
"""

from collections import defaultdict

from old_erp_account_map import new_head

FIRST_MONTH = "2014-01"
SWITCH_MONTH = "2023-10"      # first month of the new ERP
OLD_LAST_DATE = "2023-09-30"

OLD_MOV_SQL = """
    SELECT TO_CHAR(l.vd,'YYYY-MM') m, l.ac, a.an, l.vt, SUM(l.amt)
    FROM   dostt.acl l LEFT JOIN dostt.ach a ON a.ac = l.ac
    WHERE  l.ac IS NOT NULL
    GROUP BY TO_CHAR(l.vd,'YYYY-MM'), l.ac, a.an, l.vt
"""
NEW_MOV_SQL = """
    SELECT TO_CHAR(l.vd,'YYYY-MM') m, l.ac, a.an, l.vt, l.tbl, SUM(l.amt)
    FROM   dostex.acl l LEFT JOIN dostex.ach a ON a.ac = l.ac
    WHERE  l.vt <> 'OPN'
    GROUP BY TO_CHAR(l.vd,'YYYY-MM'), l.ac, a.an, l.vt, l.tbl
"""
NEW_OPN_SQL = """
    SELECT l.ac, a.an, SUM(l.amt)
    FROM   dostex.acl l LEFT JOIN dostex.ach a ON a.ac = l.ac
    WHERE  l.vt = 'OPN' GROUP BY l.ac, a.an
"""
# Cash/bank flows netted per voucher so cash<->bank transfers cancel.
OLD_CASH_FLOW_SQL = """
    SELECT m, SUM(CASE WHEN amt > 0 THEN amt ELSE 0 END), SUM(CASE WHEN amt < 0 THEN -amt ELSE 0 END)
    FROM  (SELECT TO_CHAR(vd,'YYYY-MM') m, mt, vt, vno, vd, SUM(amt) amt
           FROM dostt.acl WHERE ac LIKE '65%' GROUP BY TO_CHAR(vd,'YYYY-MM'), mt, vt, vno, vd)
    GROUP BY m
"""
NEW_CASH_FLOW_SQL = """
    SELECT m, SUM(CASE WHEN amt > 0 THEN amt ELSE 0 END), SUM(CASE WHEN amt < 0 THEN -amt ELSE 0 END)
    FROM  (SELECT TO_CHAR(vd,'YYYY-MM') m, tbl, vt, vno, vd, SUM(amt) amt
           FROM dostex.acl WHERE ac LIKE '3008%' AND vt <> 'OPN'
           GROUP BY TO_CHAR(vd,'YYYY-MM'), tbl, vt, vno, vd)
    GROUP BY m
"""
# Invoiced amount per customer (incl. GST, returns negative).
OLD_INVOICE_SQL = """
    SELECT TO_CHAR(l.vd,'YYYY-MM'), a.an, SUM(l.amt)
    FROM   dostt.acl l JOIN dostt.ach a ON a.ac = l.ac
    WHERE  l.ac LIKE '50%' AND l.vt IN ('OGP_CONVERSION','OGP_FABRIC','OGP_DYED_FABRIC','IGP_CONVERSION')
    GROUP BY TO_CHAR(l.vd,'YYYY-MM'), a.an
"""
NEW_INVOICE_SQL = """
    SELECT TO_CHAR(l.vd,'YYYY-MM'), a.an, SUM(l.amt)
    FROM   dostex.acl l JOIN dostex.ach a ON a.ac = l.ac
    WHERE  l.tbl = 'GAC' AND l.ac LIKE '3003%'
    GROUP BY TO_CHAR(l.vd,'YYYY-MM'), a.an
"""
OLD_METERS_SQL = """
    SELECT TO_CHAR(m.td,'YYYY-MM'),
           SUM(CASE WHEN SUBSTR(m.tt,1,3)='OGP' THEN 1 ELSE -1 END
               * (NVL(d.a,0)+NVL(d.b,0)+NVL(d.c,0)+NVL(d.cp,0)))
    FROM   dostt.wctm m JOIN dostt.wctd d ON d.tno = m.tno AND d.td = m.td AND d.tt = m.tt
    WHERE  m.tt IN ('OGP_CONVERSION','OGP_FABRIC','OGP_DYED_FABRIC','IGP_CONVERSION') AND NVL(d.rate,0) > 0
    GROUP BY TO_CHAR(m.td,'YYYY-MM')
"""
NEW_METERS_SQL = """
    SELECT TO_CHAR(NVL(inv_date,trans_date),'YYYY-MM'),
           SUM(CASE WHEN trans_type='GPOUT' THEN total_mtr ELSE -total_mtr END)
    FROM   dostex.fab_gp_v
    WHERE  trans_type IN ('GPOUT','GPIN') AND NVL(rate,0) <> 0
    GROUP BY TO_CHAR(NVL(inv_date,trans_date),'YYYY-MM')
"""
# Efficiency per loom per month (weighted: meters / meters-at-100%).
OLD_EFF_SQL = """
    SELECT TO_CHAR(e.td,'YYYY-MM'), l.lname, SUM(e.total_mtr), SUM(e.eff_100)
    FROM dostt.wv_eff_v e JOIN dostt.looms l ON l.lcode = e.lcode
    WHERE e.td <= DATE '2023-09-30'
    GROUP BY TO_CHAR(e.td,'YYYY-MM'), l.lname
"""
NEW_EFF_SQL = """
    SELECT TO_CHAR(e.td,'YYYY-MM'), l.lname, SUM(e.total_mtr), SUM(e.eff_100)
    FROM dostex.wv_eff_v e JOIN dostex.looms l ON l.lcode = e.lcode
    GROUP BY TO_CHAR(e.td,'YYYY-MM'), l.lname
"""
# Grade meters per loom per month. Old ERP has no A2 grade.
OLD_GRADE_SQL = """
    SELECT TO_CHAR(d.td,'YYYY-MM'), l.lname, SUM(NVL(d.a,0)), 0, SUM(NVL(d.b,0)), SUM(NVL(d.c,0)), SUM(NVL(d.cp,0))
    FROM dostt.wprdtd d JOIN dostt.looms l ON l.lcode = d.lcode
    WHERE d.tt = 'PRODUCTION' AND d.td <= DATE '2023-09-30'
    GROUP BY TO_CHAR(d.td,'YYYY-MM'), l.lname
"""
NEW_GRADE_SQL = """
    SELECT TO_CHAR(v.td,'YYYY-MM'), l.lname, SUM(NVL(v.a,0)), SUM(NVL(v.a2,0)), SUM(NVL(v.b,0)), SUM(NVL(v.c,0)), SUM(NVL(v.cp,0))
    FROM dostex.wv_prod_v v JOIN dostex.looms l ON l.lcode = v.lcode
    GROUP BY TO_CHAR(v.td,'YYYY-MM'), l.lname
"""
NEW_NAMES_SQL = "SELECT ac, an FROM dostex.ach"

# Owner-approved corrections, applied in the reports only (the ERP is never
# changed): new-ERP leaf account -> (leaf, head) it should have been posted to.
#  - 300201001 YARN, THREAD STOCK: its only entries (Rs 131 store sale
#    18-Feb-2025, Rs 27,500 yarn gate-out 21-May-2025) were sales posted to a
#    stock account; moved to yarn sale income (approved 2026-09-25).
CORRECTIONS = {"300201001": ("350105002", "350105")}


def f(v):
    return float(v or 0)


def is_old(m):
    return m < SWITCH_MONTH


class Ledger:
    """Loads both ledgers once; offers merged movements and balances."""

    def __init__(self, cur, rows, months):
        self.months = months
        self.names = {ac: (an or "").strip(" .") for ac, an in rows(cur, NEW_NAMES_SQL)}
        # records: (era, month, leaf_ac, leaf_name, head6, vt, tbl, amt)
        self.records = []
        self.unmapped = defaultdict(float)
        for m, ac, an, vt, amt in rows(cur, OLD_MOV_SQL):
            h = new_head(ac)
            if not h:
                self.unmapped[ac] += f(amt)
                continue
            self.records.append(("old", m, ac, (an or "").strip(), h, vt, None, f(amt)))
        for m, ac, an, vt, tbl, amt in rows(cur, NEW_MOV_SQL):
            leaf, head = CORRECTIONS.get(ac, (ac, ac[:6]))
            self.records.append(("new", m, leaf, (an or "").strip(), head, vt, tbl, f(amt)))
        self.new_opening = [(ac, (an or "").strip(), ac[:6], f(v)) for ac, an, v in rows(cur, NEW_OPN_SQL)]

    # -- movements (no opening balances) --------------------------------
    def head_movements(self):
        out = defaultdict(lambda: defaultdict(float))
        for era, m, ac, an, h, vt, tbl, amt in self.records:
            out[h][m] += amt
        return out

    # -- month-end balances ---------------------------------------------
    def head_balances(self):
        old_mov = defaultdict(lambda: defaultdict(float))
        new_mov = defaultdict(lambda: defaultdict(float))
        for era, m, ac, an, h, vt, tbl, amt in self.records:
            (old_mov if era == "old" else new_mov)[h][m] += amt
        opening = defaultdict(float)
        for ac, an, h, v in self.new_opening:
            opening[h] += v
        heads = set(old_mov) | set(new_mov) | set(opening)
        out = {}
        for h in heads:
            run_old, run_new, series = 0.0, opening[h], []
            for m in self.months:
                if is_old(m):
                    run_old += old_mov[h].get(m, 0.0)
                    series.append(run_old)
                else:
                    run_new += new_mov[h].get(m, 0.0)
                    series.append(run_new)
            out[h] = series
        return out

    def leaf_balances(self, head_prefixes, min_abs=1000):
        """Month-end balance per leaf account whose head starts with one of
        head_prefixes. Old-ERP leaves have values only up to Sep-2023, new
        ones only from Oct-2023 (None elsewhere)."""
        mov = defaultdict(lambda: defaultdict(float))
        meta = {}
        for era, m, ac, an, h, vt, tbl, amt in self.records:
            if h.startswith(head_prefixes):
                mov[(era, ac)][m] += amt
                meta[(era, ac)] = (an, h)
        opening = {}
        for ac, an, h, v in self.new_opening:
            if h.startswith(head_prefixes):
                opening[("new", ac)] = v
                meta.setdefault(("new", ac), (an, h))
        out = []
        for key, (an, h) in meta.items():
            era = key[0]
            run = opening.get(key, 0.0)
            series = []
            for m in self.months:
                if is_old(m) != (era == "old"):
                    series.append(None)
                    continue
                run += mov[key].get(m, 0.0)
                series.append(round(run))
            if any(s is not None and abs(s) >= min_abs for s in series):
                out.append({"ac": key[1], "name": an or key[1], "head": h, "era": era, "series": series})
        return out

    def leaf_balances_at(self, date_str):
        """{(leaf_ac, head6): balance} at a date (YYYY-MM-DD), era chosen by date."""
        m_cut = date_str[:7]
        out = defaultdict(float)
        if date_str <= OLD_LAST_DATE:
            for era, m, ac, an, h, vt, tbl, amt in self.records:
                if era == "old" and m <= m_cut:
                    out[(ac, h)] += amt
        else:
            for ac, an, h, v in self.new_opening:
                out[(ac, h)] += v
            for era, m, ac, an, h, vt, tbl, amt in self.records:
                if era == "new" and m <= m_cut:
                    out[(ac, h)] += amt
        return out


def merge_monthly(rows_old, rows_new, months):
    """Two (month, value) row sets -> list aligned to months (old up to Sep-23)."""
    d = {}
    for m, v in rows_old:
        if is_old(m):
            d[m] = d.get(m, 0.0) + f(v)
    for m, v in rows_new:
        if not is_old(m):
            d[m] = d.get(m, 0.0) + f(v)
    return [round(d.get(m, 0.0)) for m in months]
