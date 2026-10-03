"""
70_erp_audit.py - daily ERP audit + morning summary, and the monthly (10th) full audit. ERP READ ONLY.

Daily (every morning, for yesterday's entries):
  * every section is checked - finance, store, weaving, yarn, fabric, payroll - plus the gate software's
    Factory Entry Log (inward / outward / yarn stock / attendance / guests / meters) against the ERP;
  * the morning summary always goes out (ntfy push): scoreboard efficiency, what was entered in the ERP, gate activity;
  * findings are graded ACTION (do something today) / CONFIRMED (a definite error in the data) / WATCH (unusual, kept
    in the PDF only). Only ACTION and CONFIRMED items are pushed, and each one only once (state file); the PDF in
    Drive "DostTextile_Reports\\ERP AUDIT" lists everything, including WATCH items and items still open.
Monthly (10th): the same checks over the whole previous month, plus month-level checks (store journal vs slips,
payroll run, attendance gate vs ERP, backdating, gate registers vs ERP) -> PDF + push.

python 70_erp_audit.py                     daily, for yesterday
python 70_erp_audit.py --date 2026-09-29   daily, for that day
python 70_erp_audit.py --monthly [--month 2026-09]
add --no-push to build the PDF without sending
"""
import configparser
import importlib.util
import io
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
_d = importlib.util.spec_from_file_location("dash", HERE / "40_business_dashboard.py"); dash = importlib.util.module_from_spec(_d); _d.loader.exec_module(dash)
CFG = configparser.ConfigParser(); CFG.read(HERE / "report_config.ini")
REPORTS = Path(CFG.get("output", "folder"))
STATE = HERE / "erp_audit_state.json"
GATE_SHEET = "1RDUroVRxJyg1IEEePbCDgPfMrL-znVykPVfoee8-tsg"        # Gate Summary \ Factory Entry Log (gate entry software)
SA_EMAIL = "dost-textile-automation@helical-glass-505015-a7.iam.gserviceaccount.com"
SEV_ORDER = {"ACTION": 0, "CONFIRMED": 1, "WATCH": 2, "INFO": 3}
PROFILE = HERE / "erp_audit_profile.json"     # learned entry habits (rewritten every run)
RULES = HERE / "erp_audit_rules.json"         # owner-confirmed normal practices: {"dismiss": [{"match": regex, "why": text, "added": date}]}
PHOTO_FROM = None      # date from which every gate entry must carry a photo, e.g. date(2026, 10, 15) - set when the owner makes photos compulsory


class Audit:
    def __init__(self, d0, d1, label):
        self.d0, self.d1, self.label = d0, d1, label          # window [d0, d1] inclusive (dates)
        self.cur = dash.connect(dash.load_config()).cursor()
        self.F = []                                            # (sev, section, key, title, detail)
        self.summary = {}
        self.verify = []                                       # physical checks asked of the owner
        self.rules = json.loads(RULES.read_text(encoding="utf-8")) if RULES.exists() else {"dismiss": []}
        self.P = self.learn()                                  # the factory's own entry habits -> thresholds

    def q(self, sql, **b):
        self.cur.execute(sql, **b)
        return self.cur.fetchall()

    def add(self, sev, section, key, title, detail=""):
        # owner-confirmed normal practice (erp_audit_rules.json "dismiss"): kept in the PDF as INFO, never pushed
        for r in self.rules.get("dismiss", []):
            if re.search(r.get("match", "^$"), f"{key} {title}", re.I):
                sev, detail = "INFO", f"{detail} [normal practice per owner: {r.get('why', '')}]"
                break
        self.F.append((sev, section, key, title, detail))

    # ------------------------------------------------------------------ LEARNING the factory's entry style
    def learn(self):
        """measures, from the last 120 days, how this factory actually enters data, and turns it into thresholds - so a flag means
        'unusual for THIS mill', not 'breaks a textbook rule'. Saved to erp_audit_profile.json each run (the PDF shows it)."""
        def pct(vals, p):
            v = sorted(vals)
            return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None
        P = {"made": f"{date.today():%Y-%m-%d}"}
        try:
            # 1. how many days after its date each voucher type is normally keyed (p95)
            lag = defaultdict(list)
            for vt, vd, fd in self.q("""SELECT vt, TRUNC(vd), MAX(feedd) FROM dostex.acl WHERE feedd >= SYSDATE - 120 AND feedd IS NOT NULL
                                        GROUP BY vt, vno, TRUNC(vd)"""):
                lag[vt].append(max(0, (fd.date() - vd.date()).days))
            P["voucher_lag_p95"] = {vt: pct(v, 0.95) for vt, v in lag.items() if len(v) >= 10}
            # 2. how often each loom's production is booked (p90 gap between entries, days)
            gaps = defaultdict(list)
            prev = {}
            for ln, td in self.q("""SELECT DISTINCT l.lname, TRUNC(p.td) FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode
                                    WHERE p.tt = 'PRODUCTION' AND p.td >= SYSDATE - 120 ORDER BY 1, 2"""):
                if ln in prev:
                    gaps[ln.strip()].append((td - prev[ln]).days)
                prev[ln] = td
            P["loom_gap_p90"] = {ln: pct(v, 0.9) for ln, v in gaps.items() if len(v) >= 5}
            # 3. days from the gate to the ERP entry (store GRNs and yarn IGPs keyed after arrival)
            gl = [max(0, (fd.date() - dd.date()).days) for dd, fd in self.q("""SELECT TRUNC(dd), feedd FROM dostex.dmn WHERE dt = 'SRN' AND feedd IS NOT NULL AND dd >= SYSDATE - 120
                                                                              UNION ALL SELECT TRUNC(td), feedd FROM dostex.wytm WHERE feedd IS NOT NULL AND td >= SYSDATE - 120""")]
            P["gate_to_erp_p90"] = pct(gl, 0.9)
            # 4. suppliers that reuse bill-book numbers (same number on different GRNs with different amounts)
            P["bill_reuse"] = sorted({an for (an,) in self.q("""SELECT DISTINCT a.an FROM dostex.dmn x JOIN dostex.dmn y ON y.ac = x.ac AND y.bno = x.bno AND y.trans_no > x.trans_no
                                     AND y.dt = 'SRN' AND ABS(y.dd - x.dd) > 3 LEFT JOIN dostex.ach a ON a.ac = x.ac WHERE x.dt = 'SRN' AND x.dd >= SYSDATE - 365""") if an})
            # 5. zero-quantity issue slips per month (a habit of writing demand slips)
            P["zero_qty_slips_per_month"] = round(float(self.q("""SELECT COUNT(*) / 4 FROM dostex.store_issue_v WHERE NVL(qty,0) <= 0 AND dd >= ADD_MONTHS(SYSDATE, -4)""")[0][0] or 0), 1)
            PROFILE.write_text(json.dumps(P, indent=1, default=str), encoding="utf-8")
        except Exception as e:                               # fall back to the last saved profile
            P = json.loads(PROFILE.read_text(encoding="utf-8")) if PROFILE.exists() else P
            P["error"] = str(e)[:150]
        return P

    @property
    def W(self):
        """SQL date window on a column: between d0 and d1 (inclusive)"""
        return f"BETWEEN DATE '{self.d0:%Y-%m-%d}' AND DATE '{self.d1:%Y-%m-%d}'"

    # ------------------------------------------------------------------ FINANCE
    def finance(self):
        W = self.W
        # ACL is a view whose FEEDD cannot be TRUNC'd (one branch is not a date) - compare it as a range instead
        FW = f"feedd >= DATE '{self.d0:%Y-%m-%d}' AND feedd < DATE '{self.d1 + timedelta(days=1):%Y-%m-%d}'"
        # vouchers that do not balance (dr <> cr) - entered or dated in the window
        for tbl, vt, vno, vd, gap in self.q(f"""SELECT tbl, vt, vno, TRUNC(vd), SUM(amt) FROM dostex.acl
                WHERE (TRUNC(vd) {W} OR ({FW})) GROUP BY tbl, vt, vno, TRUNC(vd) HAVING ABS(SUM(amt)) > 1"""):
            self.add("CONFIRMED", "Finance", f"unbal-{vt}-{vno}-{vd:%Y%m%d}", f"{vt} {vno} of {vd:%d-%b-%y} does not balance by Rs {abs(float(gap)):,.0f}",
                     "debits and credits differ - a line / account is missing (for a store receipt: the S.Tax or Freight code box is blank)")
        keyed = self.q(f"""SELECT vt, vno, TRUNC(vd), MAX(feedd), MAX(usern), SUM(CASE WHEN amt > 0 THEN amt ELSE 0 END) FROM dostex.acl
                           WHERE {FW} GROUP BY vt, vno, TRUNC(vd)""")
        for vt, vno, vd, fd, u, amt in keyed:
            fd = datetime.combine(fd.date(), datetime.min.time())
            if vd > fd + timedelta(days=1):          # dated in the future vs when keyed
                self.add("CONFIRMED", "Finance", f"future-{vt}-{vno}", f"{vt} {vno} dated {vd:%d-%b-%y} but keyed on {fd:%d-%b-%y} (future date)", f"user {u}")
            usual = (self.P.get("voucher_lag_p95") or {}).get(vt) or 0     # learned: how late this voucher type is normally keyed
            if (fd - vd).days <= max(7, usual + 3):   # back-dated: keyed much later than this mill normally keys it
                continue
            gap = (fd - vd).days
            self.add("ACTION" if gap > max(45, 3 * usual) else "WATCH", "Finance", f"backdate-{vt}-{vno}-{vd:%Y%m%d}",
                     f"{vt} {vno} keyed {gap} days after its date ({vd:%d-%b-%y} -> {fd:%d-%b-%y}), Rs {float(amt or 0):,.0f}", f"user {u}")
        # the same payment twice: same party, same amount, within 3 days, different vouchers
        for ac, an, amt, n, vnos in self.q(f"""SELECT l.ac, MAX(a.an), l.amt, COUNT(DISTINCT l.vt || l.vno), LISTAGG(l.vt || ' ' || l.vno || ' ' || TO_CHAR(l.vd,'DD-Mon'), ', ')
                WITHIN GROUP (ORDER BY l.vd) FROM dostex.acl l LEFT JOIN dostex.ach a ON a.ac = l.ac
                WHERE l.vt IN ('CP','BP') AND l.ac NOT LIKE '3008%' AND l.amt >= 5000
                AND TRUNC(l.vd) BETWEEN DATE '{self.d0:%Y-%m-%d}' - 3 AND DATE '{self.d1:%Y-%m-%d}'
                GROUP BY l.ac, l.amt HAVING COUNT(DISTINCT l.vt || l.vno) > 1"""):
            self.add("ACTION", "Finance", f"dup-{ac}-{amt}-{vnos}", f"Possible double payment: {an} Rs {float(amt):,.0f} paid {n} times", vnos)
        # the trial balance must be zero (debits = credits over the whole ledger)
        tb = float(self.q("SELECT SUM(amt) FROM dostex.acl")[0][0] or 0)
        self.summary["tb"] = tb
        if abs(tb) > 1:
            self.add("CONFIRMED", "Finance", f"tb-{round(tb)}", f"Trial balance is out by Rs {tb:,.0f}",
                     "total debits <> total credits - the unbalanced vouchers listed in the PDF cause it (store receipts with a blank S.Tax / Freight code, one-sided journals)")
        # cash in hand must never be negative
        cash = float(self.q(f"SELECT SUM(amt) FROM dostex.acl WHERE ac LIKE '300801%' AND TRUNC(vd) <= DATE '{self.d1:%Y-%m-%d}'")[0][0] or 0)
        self.summary["cash"] = cash
        if cash < -1:
            self.add("CONFIRMED", "Finance", "cash-negative", f"Cash in hand is negative in the ERP: Rs {cash:,.0f}", "a cash payment is keyed without its receipt, or a receipt is missing")
        # big payments - informational list for the summary
        big = self.q(f"""SELECT l.vt, l.vno, TRUNC(l.vd), a.an, l.amt FROM dostex.acl l LEFT JOIN dostex.ach a ON a.ac = l.ac
                WHERE l.vt IN ('CP','BP') AND l.ac NOT LIKE '3008%' AND l.amt >= 500000 AND (TRUNC(l.vd) {W})""")
        for vt, vno, vd, an, amt in big:
            self.add("WATCH", "Finance", f"big-{vt}-{vno}-{an}", f"Large payment {vt} {vno}: {an} Rs {float(amt):,.0f}", f"{vd:%d-%b-%y}")
        vv = self.q(f"""SELECT vt, COUNT(DISTINCT vno), SUM(NVL(dr,0)) FROM dostex.acl WHERE TRUNC(vd) {W} GROUP BY vt""")
        self.summary["vouchers"] = {vt: (int(n), float(s or 0)) for vt, n, s in vv}
        cp = self.q(f"SELECT SUM(amt) FROM dostex.acl WHERE ac LIKE '300801%' AND TRUNC(vd) {W}")[0][0]
        self.summary["cash_move"] = float(cp or 0)

    # ------------------------------------------------------------------ STORE
    def store(self):
        W = self.W
        zero = self.q(f"""SELECT v.dno, TRUNC(v.dd), v.cn, v.idesc, v.qty FROM dostex.store_issue_v v
                JOIN dostex.dmn m ON m.trans_no = v.trans_no WHERE (TRUNC(v.dd) {W} OR TRUNC(m.feedd) {W}) AND NVL(v.qty,0) <= 0 ORDER BY v.dno""")
        if self.d0 != self.d1 and zero:           # monthly: one line, with the list in the detail
            self.add("CONFIRMED", "Store", f"iss0-{self.d0:%Y%m}", f"{len(zero)} issue slip lines with quantity 0 in {self.d0:%b}",
                     "; ".join(f"#{d} {c}: {i}" for d, _, c, i, _ in zero) + " - demanded but not in stock? the item / loom may still be waiting")
        habit = (self.P.get("zero_qty_slips_per_month") or 0) >= 3        # learned: the store writes demand slips with quantity 0
        for dno, dd, cn, idesc, qty in (zero if self.d0 == self.d1 else []):
            self.add("WATCH" if habit else "CONFIRMED", "Store", f"iss0-{dno}-{idesc}", f"Issue slip {dno} ({dd:%d-%b}) to {cn}: {idesc} with quantity {qty or 0}",
                     "a slip with no quantity - item probably not in stock; the loom may still be waiting for it")
        iss = self.q(f"""SELECT DISTINCT v.ic, v.idesc, v.uc FROM dostex.store_issue_v v JOIN dostex.dmn m ON m.trans_no = v.trans_no
                         WHERE TRUNC(v.dd) {W} OR TRUNC(m.feedd) {W}""")
        for ic, idesc, uc in iss:
            st = self.cur.callfunc("dostex.get_item_stock", float, [ic, datetime.combine(self.d1, datetime.min.time()) + timedelta(days=1), uc])
            if st is not None and st < -0.001:
                self.add("CONFIRMED", "Store", f"neg-{ic}", f"{idesc}: stock is {st:g} (negative)", "issued more than received - a receipt is missing or the issue is wrong")
                self.verify.append(f"Store: count {idesc} (ERP shows {st:g})")
        for dno, dd, an, idesc, rate, ic in self.q(f"""SELECT r.dno, TRUNC(r.dd), r.an, r.idesc, r.rate, r.ic FROM dostex.store_receipt_v r
                JOIN dostex.dmn m ON m.trans_no = r.trans_no WHERE (TRUNC(r.dd) {W} OR TRUNC(m.feedd) {W}) AND NVL(r.rate,0) > 0"""):
            # market check (owner, 30-Sep-2026): the cheapest price paid to ANY OTHER supplier for the same item in the last 12 months
            alt = self.q("""SELECT * FROM (SELECT rate, TRUNC(dd), an, qty FROM dostex.store_receipt_v WHERE ic = :ic AND dno <> :dno AND NVL(rate,0) > 0
                            AND an <> :an AND dd BETWEEN ADD_MONTHS(:dd, -12) AND :dd ORDER BY rate) WHERE ROWNUM = 1""", ic=ic, dno=dno, an=an, dd=dd)
            if alt:
                ar, ad, aan, _ = alt[0]; qty_ = float(self.q("SELECT SUM(qty) FROM dostex.store_receipt_v WHERE dno = :d AND ic = :ic AND TRUNC(dd) = :dd",
                                                             d=dno, ic=ic, dd=dd)[0][0] or 0)
                extra = (float(rate) - float(ar)) * qty_
                if float(rate) > 1.25 * float(ar) and extra > 1000:
                    self.add("ACTION" if float(rate) > 1.5 * float(ar) and extra > 5000 else "WATCH", "Store", f"mkt-{dno}-{ic}",
                             f"GRN {dno} {idesc}: Rs {float(rate):,.0f} from {an} - {aan} sold it for Rs {float(ar):,.0f} on {ad:%d-%b-%y}",
                             f"Rs {extra:,.0f} more on {qty_:g} - ask for the market rate / a second quote")
            prev = self.q("""SELECT * FROM (SELECT rate, TRUNC(dd), an FROM dostex.store_receipt_v WHERE ic = :ic AND dno <> :dno AND NVL(rate,0) > 0
                             AND dd BETWEEN ADD_MONTHS(:dd, -18) AND :dd ORDER BY dd DESC) WHERE ROWNUM = 1""", ic=ic, dno=dno, dd=dd)
            if prev:
                pr, pd, pan = prev[0]; ratio = float(rate) / float(pr)
                if ratio > 1.5 or ratio < 0.67:
                    self.add("ACTION" if ratio > 2.5 else "WATCH", "Store", f"rate-{dno}-{ic}",
                             f"GRN {dno} {idesc}: rate Rs {float(rate):,.0f} vs last Rs {float(pr):,.0f} ({ratio:.1f}x)", f"{an} now; last {pan} {pd:%d-%b-%y}")
        # the same single part issued again to the same loom within 2 days (parts issued one at a time)
        for dno, dd, cn, idesc, amt, dno2, dd2 in self.q(f"""SELECT a.dno, TRUNC(a.dd), a.cn, a.idesc, a.amount, b.dno, TRUNC(b.dd) FROM dostex.store_issue_v a
                JOIN dostex.store_issue_v b ON b.ic = a.ic AND b.cn = a.cn AND b.dno <> a.dno AND TRUNC(b.dd) BETWEEN TRUNC(a.dd) - 2 AND TRUNC(a.dd)
                     AND (TRUNC(b.dd) < TRUNC(a.dd) OR b.dno < a.dno)
                WHERE TRUNC(a.dd) {W} AND a.cn LIKE 'LOOM%' AND NVL(a.qty,0) = 1 AND NVL(b.qty,0) = 1"""):
            self.add("ACTION" if float(amt or 0) >= 2000 else "WATCH", "Store", f"repeat-{dno}-{idesc}",
                     f"{idesc} issued again to {cn}: slip {dno2} ({dd2:%d-%b}) and slip {dno} ({dd:%d-%b})", f"Rs {float(amt or 0):,.0f} each - was the first one really fitted?")
            if float(amt or 0) >= 2000:
                self.verify.append(f"{cn}: check that {idesc} from slips {dno2} and {dno} are both fitted / the old part returned")
        # the same supplier bill on two GRNs - only when it looks like a real double entry (same amount, or within 3 days);
        # small suppliers reuse bill-book numbers, so the same number on different dates / goods is normal
        for an, bno, d1, d2, g1, g2, a1, a2 in self.q(f"""SELECT a.an, x.bno, TRUNC(x.dd), TRUNC(y.dd), x.dno, y.dno,
                (SELECT SUM(total_amt) FROM dostex.store_receipt_v r WHERE r.trans_no = x.trans_no), (SELECT SUM(total_amt) FROM dostex.store_receipt_v r WHERE r.trans_no = y.trans_no)
                FROM dostex.dmn x JOIN dostex.dmn y ON y.ac = x.ac AND y.bno = x.bno AND y.trans_no > x.trans_no AND y.dt = 'SRN'
                LEFT JOIN dostex.ach a ON a.ac = x.ac WHERE x.dt = 'SRN' AND x.bno IS NOT NULL AND UPPER(x.bno) NOT IN ('NILL','NIL','0','-')
                AND (TRUNC(y.dd) {W} OR TRUNC(y.feedd) {W}) AND x.dd >= DATE '{self.d0:%Y-%m-%d}' - 365"""):
            same_amt = a1 is not None and a2 is not None and abs(float(a1) - float(a2)) < 1
            # learned: suppliers known to reuse bill-book numbers are only flagged on the same amount
            if same_amt or (abs((d2 - d1).days) <= 3 and an not in (self.P.get("bill_reuse") or [])):
                self.add("ACTION", "Store", f"dupbill-{an}-{bno}-{g2}", f"{an} bill {bno} entered on GRN {g1} ({d1:%d-%b}, Rs {float(a1 or 0):,.0f}) and GRN {g2} ({d2:%d-%b}, Rs {float(a2 or 0):,.0f})",
                         "same bill twice - check it is not a double purchase / double payment")
        self.store_returns()
        self.store_value_drift()
        s = self.q(f"""SELECT 'ISS', COUNT(DISTINCT dno), SUM(amount) FROM dostex.store_issue_v WHERE TRUNC(dd) {W}
                       UNION ALL SELECT 'SRN', COUNT(DISTINCT dno), SUM(total_amt) FROM dostex.store_receipt_v WHERE TRUNC(dd) {W}""")
        self.summary["store"] = {k: (int(n or 0), float(a or 0)) for k, n, a in s}

    def store_returns(self):
        """Return slips (owner, 2-Oct-2026): a return valued at 0 (item had no average rate) or valued far above what the
        item ever cost (wrong unit / quantity - e.g. return 25 of 13-Nov-25: cable 80 units @ 5,598 = Rs 4.48 lakh)."""
        W = self.W
        for dno, dd, ic, idesc, uom, qty, avg, val in self.q(f"""SELECT m.dno, TRUNC(m.dd), d.ic, i.idesc, i.uom, d.qty, d.avg_rate, d.qty * NVL(d.avg_rate, 0)
                FROM dostex.dmn m JOIN dostex.ddt d ON d.trans_no = m.trans_no AND d.uc = m.uc JOIN dostex.i i ON i.ic = d.ic AND i.uc = d.uc
                WHERE m.dt = 'RET' AND (TRUNC(m.dd) {W} OR TRUNC(m.feedd) {W})"""):
            if not avg:
                self.add("CONFIRMED", "Store", f"ret0-{dno}-{ic}", f"Return slip {dno} ({dd:%d-%b}): {idesc} x{float(qty or 0):g} valued at Rs 0",
                         "the item had no average rate when returned - its purchase (GRN) was never entered or had no rate")
                continue
            buy = self.q("""SELECT MAX(rate) FROM dostex.store_receipt_v WHERE ic = :ic AND NVL(rate,0) > 0 AND dd >= ADD_MONTHS(:dd, -24)""", ic=ic, dd=dd)[0]
            hi = float(buy[0] or 0)
            if (hi and float(avg) > 3 * hi) or float(val or 0) >= 100000:
                self.add("ACTION", "Store", f"retbig-{dno}-{ic}", f"Return slip {dno} ({dd:%d-%b}): {idesc} {float(qty):g} {uom or ''} @ Rs {float(avg):,.0f} = Rs {float(val):,.0f}",
                         (f"highest purchase rate in 2 years Rs {hi:,.0f} - " if hi else "") + "check the unit and quantity on the slip")

    def store_value_drift(self):
        """Issue values changing after the fact (owner, 2-Oct-2026). Each day the value and quantity of every issue line of the
        last 180 days is kept; next day any change is explained: a GRN of that item dated on/before the issue but keyed since
        (the ERP re-rates issues at average rate - normal) or not (a manual edit). Quantity changes on old slips are edits."""
        if self.d0 != self.d1:
            return
        snap_f = HERE / "erp_audit_issue_snapshot.json"
        old = json.loads(snap_f.read_text()) if snap_f.exists() else {}
        rows = self.q("""SELECT d.trans_no || '-' || d.sr_no, m.dno, TO_CHAR(m.dd,'YYYY-MM-DD'), d.ic, i.idesc, d.qty, ROUND(d.qty * NVL(d.avg_rate,0), 2)
                FROM dostex.dmn m JOIN dostex.ddt d ON d.trans_no = m.trans_no AND d.uc = m.uc JOIN dostex.i i ON i.ic = d.ic AND i.uc = d.uc
                WHERE m.dt = 'ISS' AND m.dd >= TRUNC(SYSDATE) - 180""")
        new = {k: [dno, dd, ic, idesc, float(q or 0), float(v or 0)] for k, dno, dd, ic, idesc, q, v in rows}
        if old.get("_lines"):
            since = old.get("_taken")
            moved = defaultdict(lambda: [0.0, []])
            for k, (dno, dd, ic, idesc, q, v) in new.items():
                o = old["_lines"].get(k)
                if not o:
                    continue
                if abs(o[4] - q) > 1e-6:
                    self.add("CONFIRMED", "Store", f"isqty-{k}-{q:g}", f"Issue slip {dno} ({dd[8:]}-{dd[5:7]}): {idesc} quantity changed {o[4]:g} -> {q:g} after it was saved",
                             "an old issue slip was edited - check with the storekeeper why")
                elif abs(o[5] - v) >= 1:
                    moved[ic][0] += v - o[5]; moved[ic][1].append((dno, dd, idesc))
            for ic, (dv, lst) in moved.items():
                late = self.q("""SELECT r.dno, TRUNC(r.dd) FROM dostex.store_receipt_v r JOIN dostex.dmn m ON m.trans_no = r.trans_no
                                 WHERE r.ic = :ic AND m.feedd >= TO_DATE(:s,'YYYY-MM-DD HH24:MI') AND TRUNC(r.dd) <= TO_DATE(:d,'YYYY-MM-DD')""",
                              ic=ic, s=since, d=max(x[1] for x in lst))
                idesc = lst[0][2]
                if late:
                    if abs(dv) >= 5000:
                        self.add("WATCH", "Store", f"isrerate-{ic}-{self.d1:%Y%m%d}", f"{idesc}: {len(lst)} old issue lines re-valued {dv:+,.0f} Rs",
                                 f"GRN {late[0][0]} dated {late[0][1]:%d-%b} was keyed late - the ERP re-rated earlier issues at the new average rate (normal)")
                elif abs(dv) >= 1000:
                    self.add("ACTION", "Store", f"isedit-{ic}-{self.d1:%Y%m%d}", f"{idesc}: {len(lst)} old issue lines changed value {dv:+,.0f} Rs with no late GRN to explain it",
                             "slips " + ", ".join(sorted({str(x[0]) for x in lst})[:8]) + " - rate edited by hand? check with the store / accountant")
        snap_f.write_text(json.dumps({"_taken": datetime.now().strftime("%Y-%m-%d %H:%M"), "_lines": new}))

    # ------------------------------------------------------------------ WEAVING
    def weaving(self):
        W = self.W
        for td, ln, cn, eff, mtr in self.q(f"""SELECT TRUNC(e.td), l.lname, NULL, e.eff, e.total_mtr FROM dostex.wv_eff_v e LEFT JOIN dostex.looms l ON l.lcode = e.lcode
                WHERE e.tt = 'PRODUCTION' AND TRUNC(e.td) {W} AND e.eff > 100"""):
            # tiny part-shift entries (under 150 m) go over 100% on rounding of the shift fraction - only a big entry is a definite error
            self.add("CONFIRMED" if float(mtr) >= 150 else "WATCH", "Weaving", f"eff100-{td:%Y%m%d}-{ln}", f"{ln} {td:%d-%b}: efficiency {float(eff):.0f}% (over 100%) on {float(mtr):,.0f} m",
                     "meters, shifts or RPM keyed wrong")
        for td, ln, cn, mtr in self.q(f"""SELECT TRUNC(p.td), l.lname, p.cont_no, p.total_mtr FROM dostex.wv_prod_v p LEFT JOIN dostex.looms l ON l.lcode = p.lcode
                WHERE p.tt = 'PRODUCTION' AND TRUNC(p.td) {W} AND p.ref_tno IS NULL"""):
            self.add("CONFIRMED", "Weaving", f"nobeam-{td:%Y%m%d}-{ln}", f"{ln} {td:%d-%b}: {float(mtr):,.0f} m of contract {cn} entered without a beam", "")
        for ln, cn, rej, tot in self.q(f"""SELECT l.lname, p.cont_no, SUM(NVL(p.c,0)+NVL(p.cp,0)), SUM(p.total_mtr) FROM dostex.wv_prod_v p
                LEFT JOIN dostex.looms l ON l.lcode = p.lcode WHERE p.tt = 'PRODUCTION' AND TRUNC(p.td) {W} GROUP BY l.lname, p.cont_no
                HAVING SUM(p.total_mtr) > 200 AND SUM(NVL(p.c,0)+NVL(p.cp,0)) > 0.15 * SUM(p.total_mtr)"""):
            self.add("WATCH", "Weaving", f"rej-{self.d1:%Y%m%d}-{ln}", f"{ln} (contract {cn}): rejection {float(rej) / float(tot) * 100:.0f}% of {float(tot):,.0f} m", "")
        # beams woven beyond their set length
        # (in this view ACTUAL_LENGTH = set length of the beam, BEAM_PROD = woven from it, BLENGTH = what is left)
        for bc, st, ln, blen, woven in self.q("""SELECT b.beam_code, b.set_no, l.lname, b.actual_length, b.beam_prod FROM dostex.wv_beam_atloom_v b
                LEFT JOIN dostex.looms l ON l.lcode = b.lcode WHERE b.beam_prod > 1.02 * b.actual_length AND b.actual_length > 0"""):
            self.add("CONFIRMED", "Weaving", f"over-{bc}-{st}", f"Beam {bc} (set {st}) on {ln}: {float(woven):,.0f} m woven from a {float(blen):,.0f} m set",
                     "more fabric than the beam holds - production booked to the wrong beam, or the beam was changed without a take-off")
        # looms with a beam on but no production entered for days
        for ln, bc, cn, last in self.q("""SELECT l.lname, b.beam_code, b.cont_no, (SELECT MAX(p.td) FROM dostex.wv_prod_v p WHERE p.lcode = b.lcode AND p.tt = 'PRODUCTION')
                FROM dostex.wv_beam_atloom_v b LEFT JOIN dostex.looms l ON l.lcode = b.lcode"""):
            if last is None:
                continue
            idle = (self.d1 - last.date()).days
            usual = (self.P.get("loom_gap_p90") or {}).get((ln or "").strip()) or 3     # learned: how often this loom is normally booked
            if idle >= max(5, usual + 2):
                self.add("ACTION" if idle >= max(8, usual + 5) else "WATCH", "Weaving", f"idle-{ln}-{last:%Y%m%d}",
                         f"{ln} (beam {bc}, contract {cn}): no production entered for {idle} days (last {last:%d-%b})", "loom stopped, or production not keyed")
        prod = self.q(f"""SELECT COUNT(DISTINCT lcode), SUM(total_mtr), SUM(NVL(c,0)+NVL(cp,0)) FROM dostex.wv_prod_v WHERE tt = 'PRODUCTION' AND TRUNC(td) {W}""")[0]
        eff = self.q(f"SELECT SUM(eff * total_mtr) / NULLIF(SUM(total_mtr),0) FROM dostex.wv_eff_v WHERE tt = 'PRODUCTION' AND TRUNC(td) {W}")[0][0]
        self.summary["prod"] = dict(looms=int(prod[0] or 0), m=float(prod[1] or 0), rej=float(prod[2] or 0), eff=float(eff) if eff else None)
        # scoreboard (mill level) - the scoreboard PC uploads yesterday's summary at ~7 AM
        scb = {}
        d = self.d0
        while d <= self.d1:
            f = REPORTS / f"scoreboard_summary_{d:%Y-%m-%d}.csv"
            if f.exists():
                lines = f.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
                if len(lines) > 1:
                    v = lines[1].split(",")
                    try:
                        scb[d] = (float(v[1]), float(v[2]), float(v[3]))
                    except (ValueError, IndexError):
                        pass
            d += timedelta(days=1)
        self.summary["scb"] = scb
        if self.d0 == self.d1 and not scb:
            self.add("ACTION", "Weaving", f"scb-missing-{self.d1:%Y%m%d}", f"Scoreboard file for {self.d1:%d-%b} has not arrived",
                     "check the scoreboard PC (power / Drive sign-in / its upload job)")
        if scb and eff:
            se = sum(v[0] for v in scb.values()) / len(scb)
            if abs(se - float(eff)) > 8:
                self.add("WATCH", "Weaving", f"scb-eff-{self.d1:%Y%m%d}", f"Efficiency: scoreboard {se:.1f}% vs ERP entries {float(eff):.1f}%", "ERP books each loom every ~3 days - look at the 7-day trend")

    # ------------------------------------------------------------------ WEAVING - loom history, scoreboard, shrinkage
    def looms_vs_history(self):
        """per loom over a window (daily run: last 14 days; monthly: the month):
        (a) ERP meters against the scoreboard, RELATIVE to the whole mill - the scoreboard misses days and the ERP books rolls
            every ~3 days, so a loom is only out of line if its ERP / scoreboard ratio is far from the mill's ratio;
        (b) the scoreboard shows the loom stopped on days it counted, yet the ERP has production;
        (c) the loom's meters a day against its own previous 60 days"""
        a0 = self.d0 if self.d0 != self.d1 else self.d1 - timedelta(days=13)
        erp = {}
        for ln, m, n in self.q(f"""SELECT l.lname, SUM(p.total_mtr), COUNT(DISTINCT TRUNC(p.td)) FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode
                WHERE p.tt = 'PRODUCTION' AND TRUNC(p.td) BETWEEN DATE '{a0:%Y-%m-%d}' AND DATE '{self.d1:%Y-%m-%d}' GROUP BY l.lname"""):
            mt = re.search(r"\d+", ln or "")
            if mt:
                erp[int(mt.group())] = (ln.strip(), float(m or 0))
        scb, zero_days, files = defaultdict(float), defaultdict(int), 0
        d = a0
        while d <= self.d1:
            f = REPORTS / f"scb_efficiency_loomwise_{d:%Y-%m-%d}.csv"
            if f.exists():
                files += 1; day = defaultdict(float)
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
                    v = line.split(",")
                    if len(v) >= 3 and v[0].strip().isdigit():
                        day[int(v[0])] += num(v[2])
                for k in erp:
                    scb[k] += day.get(k, 0.0)
                    if day.get(k, 0.0) < 1:
                        zero_days[k] += 1
            d += timedelta(days=1)
        both = [k for k in erp if scb.get(k, 0) > 50]
        if files >= 5 and len(both) >= 10:
            rm = sum(erp[k][1] for k in both) / sum(scb[k] for k in both)
            lim = 0.15 if self.d0 != self.d1 else 0.25       # monthly (owner): flag every loom whose difference is high
            tbl = []
            for k in sorted(both):
                ln, e = erp[k]; s = scb[k]; dev = (e / s) / rm - 1; gap = e - s * rm
                tbl.append((ln, e, s, s * rm, dev))
                if abs(dev) > lim and abs(gap) > 500:
                    self.add("ACTION" if dev > 0.4 or self.d0 != self.d1 else "WATCH", "Weaving", f"scbrel-{ln}-{self.d1:%Y%W}",
                             f"{ln} {a0:%d-%b}-{self.d1:%d-%b}: ERP {e:,.0f} m, scoreboard {s:,.0f} m - {dev * 100:+.0f}% against the mill ({gap:+,.0f} m)",
                             "ERP higher than the loom's own count = production over-entered; lower = rolls not entered / missing")
                    if dev > 0.4:
                        self.verify.append(f"{ln}: check the rolls entered {a0:%d-%b}-{self.d1:%d-%b} (ERP {gap:+,.0f} m more than its scoreboard share)")
            self.summary["looms"] = (a0, files, rm, tbl)
            for k, (ln, e) in erp.items():
                if zero_days.get(k, 0) == files and e > 300:
                    self.add("WATCH", "Weaving", f"scbstop-{ln}-{self.d1:%Y%W}", f"{ln}: {e:,.0f} m entered but the scoreboard counted nothing on all {files} days it recorded",
                             "loom not wired to the scoreboard, or production entered for a loom that was stopped")
        # own history: meters a day in the window vs the 60 days before it
        for ln, m, days in self.q(f"""SELECT l.lname, SUM(p.total_mtr), DATE '{self.d1:%Y-%m-%d}' - DATE '{a0:%Y-%m-%d}' + 1 FROM dostex.wv_prod_v p
                JOIN dostex.looms l ON l.lcode = p.lcode WHERE p.tt = 'PRODUCTION' AND TRUNC(p.td) BETWEEN DATE '{a0:%Y-%m-%d}' AND DATE '{self.d1:%Y-%m-%d}' GROUP BY l.lname"""):
            h = self.q(f"""SELECT SUM(p.total_mtr) FROM dostex.wv_prod_v p JOIN dostex.looms l ON l.lcode = p.lcode WHERE p.tt = 'PRODUCTION' AND l.lname = :ln
                           AND TRUNC(p.td) BETWEEN DATE '{a0:%Y-%m-%d}' - 60 AND DATE '{a0:%Y-%m-%d}' - 1""", ln=ln)[0][0]
            if not h:
                continue
            now, usual = float(m) / float(days), float(h) / 60
            if usual > 50 and (now > 1.4 * usual or now < 0.6 * usual):
                self.add("WATCH", "Weaving", f"hist-{ln.strip()}-{self.d1:%Y%W}", f"{ln.strip()} {a0:%d-%b}-{self.d1:%d-%b}: {now:,.0f} m a day vs its usual {usual:,.0f} ({now / usual:.1f}x)",
                         "a big change in the loom's output - quality change, stoppage, or entries booked to the wrong loom")

    def beam_shrinkage(self):
        """beams taken off as COMPLETE in the window: shrinkage (set length - fabric woven) vs the last year's beams of the same quality"""
        rows_ = self.q("""SELECT d.ref_tno, d.ref_sr, TRUNC(m.td), b.bcode, b.setno, b.blength * NVL(mt.w,3), c.qcode, c.cont_no,
                (SELECT SUM(NVL(p.a,0)+NVL(p.a2,0)+NVL(p.b,0)+NVL(p.c,0)+NVL(p.cp,0)) FROM dostex.wprdtm pm JOIN dostex.wprdtd p ON p.trans_no = pm.trans_no
                 WHERE pm.tt = 'PRODUCTION' AND p.ref_tno = d.ref_tno AND p.ref_sr = d.ref_sr)
                FROM dostex.wprdtm m JOIN dostex.wprdtd d ON d.trans_no = m.trans_no
                JOIN dostex.wybeams b ON b.trans_no = d.ref_tno AND b.sr_no = d.ref_sr JOIN dostex.wytd yd ON yd.trans_no = b.trans_no AND yd.sr_no = b.ref_sr
                JOIN dostex.contm c ON c.cont_id = yd.cont_id
                LEFT JOIN (SELECT d3.ref_tno, d3.ref_sr, MAX(NVL(d3.bwidth,3)) w FROM dostex.wprdtm m3 JOIN dostex.wprdtd d3 ON d3.trans_no = m3.trans_no
                           WHERE m3.tt = 'BEAMS_TO_LOOM' GROUP BY d3.ref_tno, d3.ref_sr) mt ON mt.ref_tno = d.ref_tno AND mt.ref_sr = d.ref_sr
                WHERE m.tt = 'BEAMS_FROM_LOOM' AND d.bstatus = 'COMPLETE' AND m.td >= ADD_MONTHS(SYSDATE, -14)""")
        hist = defaultdict(list)
        for r in rows_:
            if r[5] and r[8]:
                hist[r[6]].append((r[2], 1 - float(r[8]) / float(r[5])))
        for tr, sr, td, bc, st, setlen, qc, cn, woven in rows_:
            if not (self.d0 <= td.date() <= self.d1) or not setlen or not woven or (bc or "").strip() in ("00006", "00012", "00019", "00035"):
                continue                                     # numbers painted on two beams mix their production
            s = 1 - float(woven) / float(setlen)
            h = [x for d_, x in hist[qc] if d_ < td and d_ >= td - timedelta(days=365) and -0.05 < x < 0.3]
            if len(h) < 6:
                continue
            mu = sum(h) / len(h); sd = (sum((x - mu) ** 2 for x in h) / len(h)) ** 0.5
            if s > mu + max(0.02, 2 * sd):
                self.add("ACTION" if s > mu + 0.05 else "WATCH", "Weaving", f"shrink-{bc}-{st}",
                         f"Beam {bc} (set {st}, contract {cn}) finished with {s * 100:.1f}% shrinkage vs {mu * 100:.1f}% usual for this quality",
                         f"{float(setlen):,.0f} m set gave {float(woven):,.0f} m - {float(setlen) * (s - mu):,.0f} m of fabric unaccounted: unentered rolls, cut beam or wrong set length")

    # ------------------------------------------------------------------ BEAMS (numbers sent to / back from sizers, on looms)
    def beams(self):
        """beam numbers: a sized beam must come back from the sizer it was sent to empty; a number can't be at two places at once;
        numbers outside the beam master / the reserved correction numbers; new numbers put into use; sizer beam balances"""
        TWO = {"00006", "00012", "00019", "00035"}          # painted on two physical beams (known)
        RESERVED = {"00540", "00541", "00542", "00543", "00544", "00545"}
        ev = defaultdict(list)                               # beam -> [(date, kind, party, ref)]
        for bc, td, tt, an, gp in self.q("""SELECT d.beam_code, TRUNC(m.td), m.tt, a.an, m.gp_no FROM dostex.wv_gp_beamsd d JOIN dostex.wv_gp_beamsm m ON m.trans_no = d.trans_no
                                            LEFT JOIN dostex.ach a ON a.ac = m.ac1 WHERE m.tt IN ('OGP_EMPTY_BEAMS','IGP_EMPTY_BEAMS')"""):
            ev[(bc or "").strip()].append((td.date(), "EMPTY_OUT" if tt == "OGP_EMPTY_BEAMS" else "EMPTY_BACK", (an or "").strip(), f"GP {gp}"))
        for bc, td, an, tno, st in self.q("""SELECT b.bcode, TRUNC(m.td), a.an, m.tno, b.setno FROM dostex.wybeams b JOIN dostex.wytm m ON m.trans_no = b.trans_no
                                             LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2) WHERE m.tt = 'IGP_SIZED_BEAMS'"""):
            ev[(bc or "").strip()].append((td.date(), "SIZED_IN", (an or "").strip(), f"IGP {tno} set {st}"))
        for bc, td, tt in self.q("""SELECT b.bcode, TRUNC(m.td), m.tt FROM dostex.wprdtm m JOIN dostex.wprdtd d ON d.trans_no = m.trans_no
                                    JOIN dostex.wybeams b ON b.trans_no = d.ref_tno AND b.sr_no = d.ref_sr WHERE m.tt IN ('BEAMS_TO_LOOM','BEAMS_FROM_LOOM')"""):
            ev[(bc or "").strip()].append((td.date(), "ON_LOOM" if tt == "BEAMS_TO_LOOM" else "OFF_LOOM", "", ""))
        master = {(b or "").strip() for (b,) in self.q("SELECT bcode FROM dostex.beams")}
        order = {"OFF_LOOM": 0, "EMPTY_OUT": 1, "EMPTY_BACK": 2, "SIZED_IN": 3, "ON_LOOM": 4}
        firsts = {}
        for bc, E in ev.items():
            E.sort(key=lambda x: (x[0], order[x[1]]))
            firsts[bc] = E[0][0]
            if bc in TWO:
                continue
            last_out = None; place = None                    # place: 'sizer:<name>' / 'loom' / 'mill'
            for i, (d, kind, party, ref) in enumerate(E):
                inwin = self.d0 <= d <= self.d1
                if kind == "EMPTY_OUT":
                    # a take-off keyed up to 3 days after the empty gate pass is late keying, not a number problem
                    late_off = any(k2 == "OFF_LOOM" and 0 <= (d2 - d).days <= 3 for d2, k2, *_ in E[i + 1:])
                    if inwin and place == "loom" and not late_off:
                        self.add("CONFIRMED", "Beams", f"out-onloom-{bc}-{d:%Y%m%d}", f"Beam {bc} sent empty to {party} ({ref}, {d:%d-%b}) while the ERP still has it on a loom",
                                 "the take-off was not entered, or the wrong beam number is on the gate pass")
                    if inwin and place and place.startswith("sizer:"):
                        self.add("WATCH", "Beams", f"out-twice-{bc}-{d:%Y%m%d}", f"Beam {bc} sent empty to {party} ({ref}, {d:%d-%b}) but it was already at {place[6:]}", "")
                    last_out = party; place = f"sizer:{party}"
                elif kind == "EMPTY_BACK":
                    place = "mill"
                elif kind == "SIZED_IN":
                    if inwin:
                        if last_out is None:
                            self.add("WATCH", "Beams", f"in-neversent-{bc}-{d:%Y%m%d}", f"Beam {bc} came back filled from {party} ({ref}, {d:%d-%b}) but was never sent to a sizer empty",
                                     "a different number came back, or the empty-beam gate pass was not entered")
                        elif place != f"sizer:{party}":
                            where = place[6:] if place and place.startswith("sizer:") else ("a loom" if place == "loom" else "the mill")
                            self.add("ACTION" if place == "loom" else "WATCH", "Beams", f"in-other-{bc}-{d:%Y%m%d}",
                                     f"Beam {bc} came back filled from {party} ({ref}, {d:%d-%b}) but the ERP had it at {where}",
                                     "the number sent and the number returned differ - check the beam's painted number")
                    place = "mill"
                elif kind == "ON_LOOM":
                    if inwin and place == "loom":
                        self.add("CONFIRMED", "Beams", f"twoloom-{bc}-{d:%Y%m%d}", f"Beam {bc} mounted on a loom on {d:%d-%b} while the ERP still has it on another loom", "")
                    place = "loom"
                elif kind == "OFF_LOOM":
                    if not (place or "").startswith("sizer:"):   # a late take-off must not bring back a beam already sent to the sizer
                        place = "mill"
            if any(self.d0 <= d <= self.d1 for d, *_ in E):
                if bc in RESERVED and any(k != "EMPTY_OUT" and k != "EMPTY_BACK" and self.d0 <= d <= self.d1 for d, k, *_ in E):
                    self.add("CONFIRMED", "Beams", f"reserved-{bc}", f"Beam number {bc} used - it is a reserved correction number, not a real beam", "")
                elif bc not in master and bc not in RESERVED:
                    self.add("CONFIRMED", "Beams", f"nomaster-{bc}", f"Beam number {bc} used but it is not in the beam master list", "typing error in the beam number?")
                if self.d0 <= firsts[bc] <= self.d1 and bc not in RESERVED:
                    self.add("WATCH", "Beams", f"new-{bc}", f"New beam number {bc} used for the first time ({E[0][1].replace('_', ' ').lower()} {E[0][2]}, {E[0][0]:%d-%b})",
                             "a new beam bought, or an old beam renumbered? - check physically")
                    self.verify.append(f"Beam {bc}: new number first used {E[0][0]:%d-%b} - is it a new beam or a renumbered old one?")

    # ------------------------------------------------------------------ YARN
    def yarn(self):
        W = self.W
        # typed weight does not fit the bags (a 100-lb bag is 45.36 kg)
        for tt, tno, td, cn, yd, bags, wt in self.q(f"""SELECT m.tt, m.tno, TRUNC(m.td), c.cont_no, y.yarn_desc, d.bags, d.yarn_wt FROM dostex.wytm m
                JOIN dostex.wytd d ON d.trans_no = m.trans_no JOIN dostex.yarn y ON y.ycode = d.ycode JOIN dostex.contm c ON c.cont_id = d.cont_id
                WHERE (TRUNC(m.td) {W} OR TRUNC(m.feedd) {W}) AND m.tt IN ('IGP_YARN','OGP_YARN','TO_SIZING') AND NVL(d.bags,0) > 0 AND NVL(d.cones,0) = 0
                AND NVL(d.yarn_wt,0) > 0 AND (d.yarn_wt / d.bags < 40 OR d.yarn_wt / d.bags > 50)"""):
            self.add("CONFIRMED", "Yarn", f"wt-{tt}-{tno}", f"{tt} {tno} ({td:%d-%b}, contract {cn}, {yd.strip()}): {float(wt):,.1f} kg for {float(bags):g} bags",
                     f"{float(wt) / float(bags):.1f} kg a bag - a bag is 45.36 kg; weight or bags typed wrong")
        # yarn in hand gone negative - per PARTY and yarn family (same blend, counts within 2: a party's 52/1 and 54/1, or 30/1 & 31/1,
        # are received on one contract / code and issued on another, so a single contract can dip below zero without any yarn missing)
        BQ = "NVL(d.bags,0) + NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0), CASE WHEN y.count > 40 THEN 40 ELSE 24 END)"
        bal = self.q(f"""SELECT a.an, y.yarn_desc, SUM(CASE WHEN m.tt IN ('IGP_YARN','FROM_SIZING','YARN_FROM_LOOMS') THEN 1
                WHEN m.tt IN ('OGP_YARN','TO_SIZING','YARN_TO_LOOMS') THEN -1 ELSE 0 END * ({BQ})), SUM(CASE WHEN TRUNC(m.td) {W} THEN 1 ELSE 0 END)
                FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no
                JOIN dostex.yarn y ON y.ycode = d.ycode JOIN dostex.contm c ON c.cont_id = d.cont_id LEFT JOIN dostex.ach a ON a.ac = c.party_code
                WHERE TRUNC(m.td) <= DATE '{self.d1:%Y-%m-%d}' AND c.party_code IN (SELECT c2.party_code FROM dostex.wytm m2 JOIN dostex.wytd d2 ON d2.trans_no = m2.trans_no
                JOIN dostex.contm c2 ON c2.cont_id = d2.cont_id WHERE TRUNC(m2.td) {W}) GROUP BY a.an, y.yarn_desc""")
        fam = defaultdict(float); fam_y = defaultdict(list); fam_mv = defaultdict(int)
        for an, yd, b, mv in bal:
            mt = re.match(r"\s*(\d+)", yd or ""); bl = yarn_blend(yd) or (yd or "").strip()
            c = int(mt.group(1)) if mt else 0
            key = next((k for k in fam if k[0] == an and k[2] == bl and abs(k[1] - c) <= 2), (an, c, bl))
            fam[key] += float(b or 0); fam_y[key].append(yd.strip()); fam_mv[key] += int(mv or 0)
        for (an, c, bl), b in fam.items():
            # only a yarn that moved in the window (old residues from past contracts are not today's problem)
            if b < -2 and fam_mv[(an, c, bl)]:
                self.add("CONFIRMED", "Yarn", f"negyarn-{an}-{c}{bl}", f"{an}: {' + '.join(sorted(set(fam_y[(an, c, bl)])))} yarn in hand is {b:,.1f} bags (negative)",
                         "more issued / sent than received from this party - a receipt (IGP) is missing")
        # a sizer returned more than it was sent (per sizer + contract + yarn)
        for an, cn, yd, bal in self.q(f"""SELECT a.an, c.cont_no, y.yarn_desc, SUM(CASE m.tt WHEN 'TO_SIZING' THEN 1 ELSE -1 END * ({BQ}))
                FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no JOIN dostex.yarn y ON y.ycode = d.ycode JOIN dostex.contm c ON c.cont_id = d.cont_id
                LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2) WHERE m.tt IN ('TO_SIZING','FROM_SIZING','IGP_SIZED_BEAMS') AND TRUNC(m.td) <= DATE '{self.d1:%Y-%m-%d}'
                AND d.cont_id IN (SELECT d2.cont_id FROM dostex.wytm m2 JOIN dostex.wytd d2 ON d2.trans_no = m2.trans_no WHERE TRUNC(m2.td) {W}
                                  AND m2.tt IN ('TO_SIZING','FROM_SIZING','IGP_SIZED_BEAMS'))
                GROUP BY a.an, c.cont_no, y.yarn_desc HAVING SUM(CASE m.tt WHEN 'TO_SIZING' THEN 1 ELSE -1 END * ({BQ})) < -1"""):
            self.add("CONFIRMED", "Yarn", f"sizerneg-{an}-{cn}-{yd}", f"{an}, contract {cn} {yd.strip()}: {abs(float(bal)):,.1f} bags more came back than were sent",
                     "a TO_SIZING gate pass is missing or booked to another contract")
        # weft issued to looms vs weft the fabric needs (7 days to the window end, per contract)
        s7 = f"BETWEEN DATE '{self.d1:%Y-%m-%d}' - 6 AND DATE '{self.d1:%Y-%m-%d}'"
        # compared per weft YARN (party's contracts share the same weft, and issues are often booked to a sibling contract);
        # 14 days, because a loom is only booked every ~3 days and weft is issued ahead
        s7 = f"BETWEEN DATE '{self.d1:%Y-%m-%d}' - 13 AND DATE '{self.d1:%Y-%m-%d}'"
        # per PARTY: a party's contracts share one weft and issues are often booked to a sibling contract / code (52/1 vs 54/1)
        need = defaultdict(float)
        for pn, m, pk, w, ct in self.q(f"""SELECT a.an, SUM(p.total_mtr), MAX(c.weft_thread), MAX(c.width), MAX(c.weft_marginal_count) FROM dostex.wv_prod_v p
                JOIN dostex.contm c ON c.cont_no = p.cont_no LEFT JOIN dostex.ach a ON a.ac = c.party_code WHERE p.tt = 'PRODUCTION' AND TRUNC(p.td) {s7}
                AND c.weft_thread > 0 AND c.weft_marginal_count > 0 GROUP BY a.an, c.cont_no"""):
            need[(pn or "?").strip()] += float(m or 0) * float(pk) * float(w) * 1.0936 / 800 / float(ct) / 100
        issued = defaultdict(float)
        for pn, b in self.q(f"""SELECT a.an, SUM(CASE m.tt WHEN 'YARN_TO_LOOMS' THEN 1 ELSE -1 END * ({BQ}))
                FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no JOIN dostex.yarn y ON y.ycode = d.ycode
                JOIN dostex.contm c ON c.cont_id = d.cont_id LEFT JOIN dostex.ach a ON a.ac = c.party_code
                WHERE m.tt IN ('YARN_TO_LOOMS','YARN_FROM_LOOMS') AND TRUNC(m.td) {s7} GROUP BY a.an"""):
            issued[(pn or "?").strip()] += float(b or 0)
        for pn in set(need) | set(issued):
            n, i = need.get(pn, 0), issued.get(pn, 0)
            if n > 5 and abs(i - n) > max(5, 0.35 * n):
                self.add("WATCH", "Yarn", f"weft14-{pn}-{self.d1:%Y%m%d}", f"{pn} weft, last 14 days: issued to looms {i:,.1f} bags vs needed for fabric entered {n:,.1f}",
                         "more issued = weft lying at looms or wasted; less = production over-entered or weft issued to another party's contract")
        mv = self.q(f"""SELECT m.tt, SUM({BQ}) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no JOIN dostex.yarn y ON y.ycode = d.ycode
                        WHERE TRUNC(m.td) {W} GROUP BY m.tt""")
        self.summary["yarn"] = {tt: float(b or 0) for tt, b in mv}

    # ------------------------------------------------------------------ FABRIC
    def fabric(self):
        W = self.W
        for cn, q_, a, b, c in self.q("""SELECT s.cont_no, MAX(s.quality), SUM(NVL(v.a,0)+NVL(v.a2,0)), SUM(NVL(v.b,0)), SUM(NVL(v.c,0)+NVL(v.cp,0))
                FROM dostex.wv_fab_stock_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id GROUP BY s.cont_no
                HAVING SUM(NVL(v.a,0)+NVL(v.a2,0)) < -1 OR SUM(NVL(v.b,0)) < -1 OR SUM(NVL(v.c,0)+NVL(v.cp,0)) < -1"""):
            if int(cn) < 30:          # old-ERP contracts: dispatched from opening stock carried in from the old ERP
                continue
            self.add("CONFIRMED", "Fabric", f"negfab-{cn}", f"Contract {cn}: fabric stock negative (A {float(a):,.0f} · B {float(b):,.0f} · C {float(c):,.0f} m)",
                     "dispatched more than produced in a grade - grade keyed wrong on the gate pass or production")
        # owner, 1-Oct-2026: the old no-rate OGPs (2023-Aug 2026) were the party's own fabric/rejection under the old contracts.
        # Now rejection belongs to the mill and conversion is billed on the OGP, so EVERY outward gate pass needs a rate (rejection included).
        # Rates are often added days later (Haroon trans 578: 2-Sep, rated by 1-Oct) -> new ones are WATCH, still unrated after 3 days = ACTION.
        for inv, dt, to, m in self.q(f"""SELECT inv_no, TRUNC(MAX(trans_date)), MAX(party), SUM(total_mtr) FROM dostex.fab_gp_v
                WHERE trans_type = 'GPOUT' AND NVL(rate,0) = 0 AND (TRUNC(trans_date) {W} OR trans_date >= DATE '2026-09-01') GROUP BY inv_no"""):
            late = dt <= datetime.combine(self.d1, datetime.min.time()) - timedelta(days=3)
            big = float(m or 0) >= 100            # 1-30 m pieces (samples, Azam Puri Fabrics) stay WATCH
            self.add("ACTION" if late and big else "WATCH", "Fabric", f"norate-{inv}",
                     f"OGP {inv} ({dt:%d-%b}) to {to}: {float(m):,.0f} m with no rate" + (f" - still not rated after {(self.d1 - dt.date()).days} days" if late else ""),
                     "not billed until a rate (and GST %) is entered on the gate pass")
        g = self.q(f"""SELECT COUNT(DISTINCT inv_no), SUM(total_mtr) FROM dostex.fab_gp_v WHERE trans_type = 'GPOUT' AND TRUNC(trans_date) {W}""")[0]
        st = self.q("SELECT SUM(NVL(a,0)+NVL(a2,0)+NVL(b,0)), SUM(NVL(c,0)+NVL(cp,0)) FROM dostex.wv_fab_stock_v")[0]
        self.summary["fabric"] = dict(ogp=int(g[0] or 0), m=float(g[1] or 0), stock=float(st[0] or 0), rej=float(st[1] or 0))

    # ------------------------------------------------------------------ PAYROLL
    def payroll(self):
        W = self.W
        for code, name, dt, hrs in self.q(f"""SELECT o.emp_code, e.emp_name, TRUNC(o.dated), o.over_time_hours FROM payroll.ot o LEFT JOIN payroll.emp e ON e.emp_code = o.emp_code
                WHERE (TRUNC(o.feedd) {W} OR TRUNC(o.dated) {W}) AND NVL(o.over_time_hours,0) > 0
                AND NOT EXISTS (SELECT 1 FROM payroll.in_out i WHERE i.emp_code = o.emp_code AND TRUNC(i.dated) = TRUNC(o.dated) AND i.attendance_type = 'P')"""):
            self.add("CONFIRMED", "Payroll", f"otabs-{code}-{dt:%Y%m%d}", f"Overtime {float(hrs):g} h for {name} ({code}) on {dt:%d-%b} - not marked present that day", "")
        for code, name, dt in self.q(f"""SELECT i.emp_code, e.emp_name, TRUNC(i.dated) FROM payroll.in_out i JOIN payroll.emp e ON e.emp_code = i.emp_code
                WHERE (TRUNC(i.feedd) {W} OR TRUNC(i.modd) {W}) AND e.quit = 'Y' AND i.attendance_type = 'P'"""):
            self.add("CONFIRMED", "Payroll", f"quitp-{code}-{dt:%Y%m%d}", f"{name} ({code}) has left but is marked present on {dt:%d-%b}", "")
        # attendance changed AFTER the salary for that period was generated (edit time later than the salary run), one line per employee
        for code, name, n, dmin, dmax, md in self.q(f"""SELECT i.emp_code, MAX(e.emp_name), COUNT(*), MIN(TRUNC(i.dated)), MAX(TRUNC(i.dated)), MAX(i.modd)
                FROM payroll.in_out i LEFT JOIN payroll.emp e ON e.emp_code = i.emp_code
                WHERE TRUNC(i.modd) {W} AND EXISTS (SELECT 1 FROM payroll.salm s WHERE s.emp_code = i.emp_code AND s.sal_gen_date IS NOT NULL
                AND i.modd > s.sal_gen_date AND TRUNC(i.dated) BETWEEN NVL(s.fdate, TRUNC(s.dated,'MM')) AND NVL(s.tdate, LAST_DAY(s.dated)))
                GROUP BY i.emp_code"""):
            self.add("ACTION", "Payroll", f"editfin-{code}-{md:%Y%m%d}", f"Attendance of {name} ({code}) changed on {md:%d-%b} for {n} day(s) {dmin:%d-%b}-{dmax:%d-%b}, after that salary was made",
                     "pay may no longer match attendance - check who changed it and why")
        n = self.q(f"""SELECT (SELECT COUNT(*) FROM payroll.in_out WHERE TRUNC(feedd) {W}), (SELECT COUNT(*) FROM payroll.ot WHERE TRUNC(feedd) {W}),
                       (SELECT COUNT(*) FROM payroll.emp_loans WHERE TRUNC(feedd) {W}) FROM dual""")[0]
        self.summary["payroll"] = dict(att=int(n[0] or 0), ot=int(n[1] or 0), loans=int(n[2] or 0))

    # ------------------------------------------------------------------ GATE (Factory Entry Log) vs ERP
    def gate(self):
        tabs = read_gate()
        if tabs is None:
            self.summary["gate"] = None
            self.add("ACTION", "Gate", "gate-access", "Gate sheet (Factory Entry Log) could not be read",
                     f"share it with {SA_EMAIL} as Viewer (Share button in the sheet) so the audit can compare it with the ERP")
            return
        inside = lambda r: r.get("Date") and self.d0 <= r["Date"] <= self.d1  # noqa: E731
        inw = [r for r in tabs.get("Inward Log", []) if inside(r)]
        out = [r for r in tabs.get("Outward Log", []) if inside(r)]
        guests = [r for r in tabs.get("Guest Log", []) if inside(r)]
        att = [r for r in tabs.get("Attendance Log", []) if inside(r)]
        ysl = [r for r in tabs.get("Yarn Stock Log", []) if inside(r)]
        met = [r for r in tabs.get("Meter Solar Log", []) if inside(r)]
        self.summary["gate"] = dict(inward=len({(r.get("Register #"), r.get("Party")) for r in inw}), outward=len({(r.get("Register #"), r.get("Party")) for r in out}),
                                    guests=len(guests), attendance=len(att), yarn_stock_lines=len(ysl),
                                    diesel=[r.get("Diesel Stock (mm)") for r in met][-1:] or None)
        # photos (gate pass / bill / attendance sheet) attached in the gate app's Media column - owner (30-Sep-2026) will make them
        # compulsory; from PHOTO_FROM on, an entry without a photo is flagged, and links the audit cannot open are reported
        ph = {}
        for tab in ("Inward Log", "Outward Log", "Attendance Log"):
            rows_ = [r for r in tabs.get(tab, []) if inside(r)]
            if tab == "Attendance Log":           # one sheet photo per attendance submission, not per worker
                rows_ = list({(r.get("Timestamp"), r.get("Shift")): r for r in rows_}.values())
            else:
                rows_ = list({regno(r.get("Register #")) or id(r): r for r in rows_}.values())
            with_ph = [r for r in rows_ if str(r.get("Media") or "").startswith("http")]
            ph[tab] = (len(with_ph), len(rows_))
            if PHOTO_FROM:
                for r in rows_:
                    if r["Date"] >= PHOTO_FROM and not str(r.get("Media") or "").startswith("http"):
                        what = regno(r.get("Register #")) or r.get("Shift") or ""
                        self.add("ACTION" if tab != "Attendance Log" else "WATCH", "Gate", f"nophoto-{tab}-{what}-{r['Date']:%Y%m%d}",
                                 f"{tab} {what} ({r['Date']:%d-%b}, {r.get('Party') or r.get('Name') or ''}) has no photo attached",
                                 "gate pass / bill / attendance sheet photo is required")
        self.summary["gate"]["photos"] = ph
        links = [str(r.get("Media")) for t in ("Inward Log", "Outward Log", "Attendance Log") for r in tabs.get(t, []) if inside(r) and str(r.get("Media") or "").startswith("http")]
        if links:
            bad = [u for u in links[:15] if not drive_file_ok(u)]
            if len(bad) == len(links[:15]):
                self.add("WATCH", "Gate", "photos-noaccess", "Gate photos could not be opened by the audit",
                         f"share the 'Gate Pass Photos' folder with {SA_EMAIL} (Viewer) so every photo link can be checked")
            else:
                for u in bad:
                    self.add("WATCH", "Gate", f"photo-broken-{u[-20:]}", "A gate photo link does not open", u)
        # entries the gate app sent back / still pending
        for r in tabs.get("Pending Queue", []):
            ts = r.get("Timestamp")
            dts = ts.date() if isinstance(ts, datetime) else None
            if dts and self.d0 <= dts <= self.d1 + timedelta(days=1) and str(r.get("Status") or "").strip().lower() not in ("approved", ""):
                self.add("WATCH", "Gate", f"queue-{r.get('ID')}", f"Gate app entry {r.get('Type')} by {r.get('Entered By')}: {r.get('Status')}", str(r.get("Reason") or ""))
        # the gate's own yarn stock count (latest day in the window) against the ERP yarn in hand, per count + blend
        ys = [r for r in tabs.get("Yarn Stock Log", []) if r.get("Date") and r["Date"] <= self.d1]
        if ys:
            last = max(r["Date"] for r in ys)
            gate_b = defaultdict(float)
            for r in ys:
                if r["Date"] != last:
                    continue
                mt = re.match(r"\s*(\d+)\s*(?:/\s*\d+)?\s*([A-Za-z]+)?", str(r.get("Yarn Type") or ""))
                bl = yarn_blend(mt.group(2)) if mt and mt.group(2) else None
                if not bl:
                    continue
                k = (int(mt.group(1)), bl); cpb = 40 if k[0] > 40 else 24
                gate_b[k] += num(r.get("Fresh Bags")) + num(r.get("Rewind Bags")) + (num(r.get("Fresh Cones")) + num(r.get("Rewind Cones"))) / cpb
            BQ = "NVL(d.bags,0) + NVL(d.cones,0)/NVL(NULLIF(d.cone_pbag,0), CASE WHEN y.count > 40 THEN 40 ELSE 24 END)"
            erp_b = defaultdict(float)
            for yd, b in self.q(f"""SELECT y.yarn_desc, SUM(CASE WHEN m.tt IN ('IGP_YARN','FROM_SIZING','YARN_FROM_LOOMS') THEN 1
                    WHEN m.tt IN ('OGP_YARN','TO_SIZING','YARN_TO_LOOMS') THEN -1 ELSE 0 END * ({BQ})) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no
                    JOIN dostex.yarn y ON y.ycode = d.ycode WHERE TRUNC(m.td) <= DATE '{last:%Y-%m-%d}' GROUP BY y.yarn_desc"""):
                mt = re.match(r"\s*(\d+)", yd or ""); bl = yarn_blend(yd)
                if mt and bl:
                    c = int(mt.group(1))
                    # the gate writes one count for a party's same yarn bought under two codes (52/1 & 54/1): nearest gate count, same blend, within 2
                    if (c, bl) not in gate_b:
                        near = [g for g in gate_b if g[1] == bl and abs(g[0] - c) <= 2]
                        if near:
                            c = min(near, key=lambda g: abs(g[0] - c))[0]
                    erp_b[(c, bl)] += float(b or 0)
            diffs = []
            for k, gb in gate_b.items():
                eb = erp_b.get(k, 0.0)
                if abs(gb - eb) > max(5, 0.15 * max(gb, eb)):
                    diffs.append((abs(gb - eb), k, gb, eb))
            for _, k, gb, eb in sorted(diffs, reverse=True)[:5]:
                self.add("WATCH", "Gate", f"ystock-{k[0]}{k[1]}-{last:%Y%m%d}", f"Yarn {k[0]} {k[1]}: gate count {gb:,.1f} bags on {last:%d-%b} vs ERP in hand {eb:,.1f}",
                         "physical count at the gate and the ERP book differ")
                self.verify.append(f"Yarn {k[0]} {k[1]}: count the bags (gate log {gb:,.1f}, ERP {eb:,.1f})")
        # every gate register number should have its ERP document (register # = ERP gate-entry number GE_NO);
        # if not found by number, an ERP document of the same party within 3 days means the number was typed differently
        g90 = int(self.P.get("gate_to_erp_p90") or 1)          # learned: days the ERP normally lags the gate
        grace = self.d1 - timedelta(days=max(1, g90)) if self.d0 == self.d1 else self.d1 - timedelta(days=max(3, g90))
        lo = self.d0 - timedelta(days=7 if self.d0 == self.d1 else 0)
        docs = self.q("""SELECT ge_no, TRUNC(dd), a.an, 'GRN ' || dno FROM dostex.dmn m LEFT JOIN dostex.ach a ON a.ac = m.ac WHERE m.dt = 'SRN' AND m.dd >= SYSDATE - 400
                         UNION ALL SELECT ge_no, TRUNC(td), a.an, tt || ' ' || tno FROM dostex.wytm m LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2) WHERE m.td >= SYSDATE - 400 AND m.tt IN ('IGP_YARN','IGP_SIZED_BEAMS','FROM_SIZING','OPN_SIZED_BEAMS')
                         UNION ALL SELECT ge_no, TRUNC(td), a.an, tt || ' ' || tno FROM dostex.wv_gp_beamsm m LEFT JOIN dostex.ach a ON a.ac = m.ac1 WHERE m.td >= SYSDATE - 400 AND m.tt = 'IGP_EMPTY_BEAMS'
                         UNION ALL SELECT DISTINCT ge_no, TRUNC(trans_date), party, trans_type || ' ' || NVL(TO_CHAR(inv_no), TO_CHAR(gp_no)) FROM dostex.fab_gp_v WHERE trans_date >= SYSDATE - 400 AND trans_type <> 'GPOUT'""")
        erp_in = set()
        for g, *_ in docs:
            erp_in |= regnos(g)

        docs_out = self.q("""SELECT DISTINCT ge_no, TRUNC(trans_date), party, 'OGP ' || inv_no FROM dostex.fab_gp_v WHERE trans_type = 'GPOUT' AND trans_date >= SYSDATE - 400
                             UNION ALL SELECT ge_no, TRUNC(td), a.an, 'empty beams GP ' || gp_no FROM dostex.wv_gp_beamsm m LEFT JOIN dostex.ach a ON a.ac = m.ac1
                                       WHERE m.tt = 'OGP_EMPTY_BEAMS' AND m.td >= SYSDATE - 400
                             UNION ALL SELECT ge_no, TRUNC(td), a.an, tt || ' ' || tno FROM dostex.wytm m LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2)
                                       WHERE m.tt IN ('OGP_YARN','TO_SIZING') AND m.td >= SYSDATE - 400""")
        gate_regs = {regno(r.get("Register #")) for r in tabs.get("Inward Log", [])}
        used = set()

        def kind_gate(detail):
            t = str(detail or "")
            if re.search(r"beam", t, re.I):
                return "BEAM"
            if re.search(r"(بیگز|کونز|\bbags?\b|\bcones?\b)", t, re.I):
                return "YARN"
            if re.search(r"(\d+\s*[*x]\s*\d+|\bmtr|\bmeter|rejection)", t, re.I):
                return "FABRIC"
            return "STORE"

        def kind_doc(ref):
            return ("BEAM" if "BEAM" in ref else "YARN" if ref.startswith(("IGP_YARN", "FROM_SIZING")) else "FABRIC" if ref.startswith("GPIN") else "STORE")

        def near_doc(d, party, kind):
            """an inward ERP document of the same kind and party within 3 days whose own gate-entry number matches no gate register - each used once"""
            tok = party_token(party)
            for i, (g, dd, an, ref) in enumerate(docs):
                if i in used or not dd or abs((dd.date() - d).days) > 3 or not tok or tok != party_token(an) or (regnos(g) & gate_regs) or kind_doc(ref) != kind:
                    continue
                used.add(i)
                return g, dd, ref
            return None
        seen = set()
        for r in tabs.get("Inward Log", []):
            reg = regno(r.get("Register #"))
            if not reg or not r.get("Date") or r["Date"] > grace or r["Date"] < lo or reg in seen:
                continue
            seen.add(reg)
            if reg in erp_in:
                continue
            kind = kind_gate(r.get("Detail"))
            nd = near_doc(r["Date"], r.get("Party"), kind)
            # logged in the gate's INWARD tab but it was really an outward movement: its register number is on an ERP outward document,
            # or it is empty beams and the ERP has empty beams going to that party that day
            od = None if nd else next((o for o in docs_out if reg in regnos(o[0]) or (kind == "BEAM" and "empty beams" in o[3] and o[1]
                                        and abs((o[1].date() - r["Date"]).days) <= 1 and party_token(o[2]) == party_token(r.get("Party")))), None)
            if od:
                self.add("WATCH", "Gate", f"in-isout-{reg}", f"Gate register {reg} ({r['Date']:%d-%b}, {r.get('Party')}) is in the gate's Inward Log, but the ERP has it as outward {od[3]}",
                         "entered in the wrong tab of the gate app")
                continue
            if nd:
                self.add("WATCH", "Gate", f"in-regdiff-{reg}", f"Gate inward register {reg} ({r['Date']:%d-%b}, {r.get('Party')}) is in the ERP as {nd[2]} with gate entry '{nd[0] or '(blank)'}'",
                         "gate-entry number typed differently in the ERP")
                continue
            age = (self.d1 - r["Date"]).days
            # yarn / fabric / beams must always be in the ERP; small store and repair items only warrant a look
            goods = re.search(r"(بیگز|کونز|\bbags?\b|\bbeam|\bmtr|\bmeter|\bctn\b|\bcones?\b|\d+\s*[*x]\s*\d+)", str(r.get("Detail") or ""), re.I)
            self.add("ACTION" if goods and age >= max(3, g90 + 1) else "WATCH", "Gate", f"in-noerp-{reg}", f"Gate inward register {reg} ({r['Date']:%d-%b}, {r.get('Party')}): {r.get('Detail')} - not in the ERP",
                     f"received at the gate {age} day(s) ago but no GRN / IGP / return with that gate entry or party within 3 days")
        # outward fabric: gate meters vs the ERP OGP (by register number, else same party + same meters within 3 days)
        erp_out = defaultdict(float); ogp = []
        for ge, dd, party, inv, m in self.q("""SELECT ge_no, TRUNC(MAX(trans_date)), MAX(party), inv_no, SUM(total_mtr) FROM dostex.fab_gp_v WHERE trans_type = 'GPOUT'
                                               AND trans_date >= SYSDATE - 400 GROUP BY ge_no, inv_no"""):
            for x in regnos(ge):
                erp_out[x] += float(m or 0)
            ogp.append((regno(ge), dd, party, inv, float(m or 0)))
        gm = defaultdict(float); gd = {}; gl = defaultdict(set)
        for r in tabs.get("Outward Log", []):
            reg = regno(r.get("Register #")); m = num(r.get("Meters"))
            if reg and m and r.get("Date"):
                line = (str(r.get("Detail") or "").strip(), round(m))
                if line in gl[reg]:              # same line keyed twice at the gate (e.g. 10-Sep and 11-Sep)
                    self.add("WATCH", "Gate", f"gatedup-{reg}", f"Gate outward register {reg} ({r.get('Party')}) entered twice in the gate log", f"{line[0]} {m:,.0f} m")
                    continue
                gl[reg].add(line); gm[reg] += m; gd[reg] = (r["Date"], r.get("Party"))
        for reg, m in gm.items():
            dte, party = gd[reg]
            if dte > grace or dte < lo:
                continue
            if reg in erp_out:
                if abs(erp_out[reg] - m) > max(5, 0.005 * m):
                    self.add("ACTION", "Gate", f"out-diff-{reg}", f"Register {reg} ({dte:%d-%b}, {party}): gate {m:,.0f} m vs ERP OGP {erp_out[reg]:,.0f} m", "meters differ")
                continue
            alt = [o for o in ogp if o[1] and abs((o[1].date() - dte).days) <= 3 and abs(o[4] - m) <= max(5, 0.005 * m)]
            if alt:
                self.add("CONFIRMED", "Gate", f"out-regdiff-{reg}", f"Fabric register {reg} ({dte:%d-%b}, {m:,.0f} m) is OGP {alt[0][3]} in the ERP, but with gate entry '{alt[0][0]}'",
                         "gate-entry number typed wrong on the ERP gate pass")
            else:
                self.add("ACTION", "Gate", f"out-noerp-{reg}", f"Fabric out at the gate, register {reg} ({dte:%d-%b}, {party}): {m:,.0f} m - no ERP gate pass",
                         "fabric left the mill without an OGP in the ERP")

    # ------------------------------------------------------------------ MONTHLY extras
    def monthly(self):
        # open unbalanced vouchers, all time
        op = self.q("SELECT tbl, vt, vno, TRUNC(vd), SUM(amt) FROM dostex.acl GROUP BY tbl, vt, vno, TRUNC(vd) HAVING ABS(SUM(amt)) > 1")
        if op:
            self.add("CONFIRMED", "Finance", "unbal-open", f"{len(op)} vouchers still out of balance (all time), Rs {sum(abs(float(x[4])) for x in op):,.0f}",
                     "; ".join(f"{vt} {vno} {vd:%d-%b-%y}" for _, vt, vno, vd, _ in op[:15]))
        # store journal vs store slips for the month
        _s = importlib.util.spec_from_file_location("st", HERE / "45_stores_reconciliation_pdf.py"); st = importlib.util.module_from_spec(_s); _s.loader.exec_module(st)
        months, iss, ret, jv = st.monthly(self.cur, dash.rows)
        m = f"{self.d0:%Y-%m}"
        j = jv.get(m, (None, "", 0.0)); net = iss[m] - ret[m]
        if j[0] is None:
            self.add("ACTION", "Finance", f"storejv-{m}", f"Store consumption journal for {self.d0:%b-%Y} not posted", f"store issued net Rs {net:,.0f}")
        elif abs(j[2] - net) > 1000:
            self.add("ACTION" if abs(j[2] - net) >= 50000 else "WATCH", "Finance", f"storejv-{m}",
                     f"Store journal {self.d0:%b-%Y} (JV {j[0]}) Rs {j[2]:,.0f} vs store slips net Rs {net:,.0f}: difference {j[2] - net:+,.0f}",
                     f"issues {iss[m]:,.0f} less returns {ret[m]:,.0f} - deduct the return slips and tie to the store issue report")
        # payroll: paid without attendance, duplicate CNIC among active employees, salary to employees who left
        W = self.W
        for code, name, days in self.q(f"""SELECT s.emp_code, e.emp_name, s.emp_worked_days FROM payroll.salm s LEFT JOIN payroll.emp e ON e.emp_code = s.emp_code
                WHERE TRUNC(s.dated) {W} AND NVL(s.earned_amount,0) > 0 AND NOT EXISTS (SELECT 1 FROM payroll.in_out i WHERE i.emp_code = s.emp_code
                AND TRUNC(i.dated) BETWEEN NVL(s.fdate, TRUNC(s.dated,'MM')) AND NVL(s.tdate, LAST_DAY(s.dated)))"""):
            self.add("ACTION", "Payroll", f"paynoatt-{code}-{m}", f"{name} ({code}) paid for {days} days with no attendance rows", "")
        # one CNIC paid on more than one code in the same month
        for nic, n, codes in self.q(f"""SELECT e.nic_no, COUNT(DISTINCT e.emp_code), LISTAGG(e.emp_code, ', ') WITHIN GROUP (ORDER BY e.emp_code) FROM payroll.emp e
                WHERE e.nic_no IS NOT NULL AND LENGTH(REPLACE(e.nic_no,'-','')) >= 13
                AND EXISTS (SELECT 1 FROM payroll.salm s WHERE s.emp_code = e.emp_code AND TRUNC(s.dated) {W} AND NVL(s.earned_amount,0) > 0)
                GROUP BY e.nic_no HAVING COUNT(DISTINCT e.emp_code) > 1"""):
            self.add("ACTION", "Payroll", f"dupnic-{nic}-{m}", f"CNIC {str(nic).replace('.', '-')} was paid on {n} employee codes in {self.d0:%b} ({codes})", "one person drawing more than one salary?")
        for code, name, amt in self.q(f"""SELECT s.emp_code, e.emp_name, s.earned_amount FROM payroll.salm s JOIN payroll.emp e ON e.emp_code = s.emp_code
                WHERE TRUNC(s.dated) {W} AND e.quit = 'Y' AND NVL(s.earned_amount,0) > 0"""):
            self.add("ACTION", "Payroll", f"payquit-{code}-{m}", f"{name} ({code}) has left but has salary Rs {float(amt):,.0f} in {self.d0:%b}", "")
        # a loom using far more spare parts than the others (last 90 days to the month end)
        lp = self.q(f"""SELECT cn, SUM(amount) FROM dostex.store_issue_v WHERE cn LIKE 'LOOM%' AND TRUNC(dd) BETWEEN DATE '{self.d1:%Y-%m-%d}' - 89 AND DATE '{self.d1:%Y-%m-%d}'
                        GROUP BY cn""")
        if len(lp) >= 10:
            vals = sorted(float(v or 0) for _, v in lp); med = vals[len(vals) // 2]
            for cn, v in lp:
                if float(v or 0) > 2.5 * med and float(v or 0) - med > 50000:
                    top = self.q(f"""SELECT * FROM (SELECT idesc, SUM(qty), SUM(amount) FROM dostex.store_issue_v WHERE cn = :c
                                     AND TRUNC(dd) BETWEEN DATE '{self.d1:%Y-%m-%d}' - 89 AND DATE '{self.d1:%Y-%m-%d}' GROUP BY idesc ORDER BY 3 DESC) WHERE ROWNUM <= 3""", c=cn)
                    self.add("ACTION", "Store", f"loomparts-{cn}-{m}", f"{cn}: spare parts Rs {float(v):,.0f} in 90 days vs Rs {med:,.0f} for a typical loom ({float(v) / med:.1f}x)",
                             "top: " + "; ".join(f"{i} x{float(qq):g} Rs {float(a):,.0f}" for i, qq, a in top))
                    self.verify.append(f"{cn}: ask the jobber why parts use is {float(v) / med:.1f}x other looms; check the replaced parts are kept")
        # rejection by loom for the month vs the mill
        rj = self.q(f"""SELECT l.lname, SUM(NVL(p.c,0)+NVL(p.cp,0)), SUM(p.total_mtr) FROM dostex.wv_prod_v p LEFT JOIN dostex.looms l ON l.lcode = p.lcode
                        WHERE p.tt = 'PRODUCTION' AND TRUNC(p.td) {W} GROUP BY l.lname""")
        tot_r = sum(float(r or 0) for _, r, _ in rj); tot_m = sum(float(t or 0) for _, _, t in rj)
        if tot_m:
            avg = tot_r / tot_m
            for ln, r, t in rj:
                if t and float(r or 0) > 500 and float(r) / float(t) > 2 * avg:
                    self.add("WATCH", "Weaving", f"rejloom-{ln}-{m}", f"{ln}: rejection {float(r) / float(t) * 100:.1f}% in {self.d0:%b} vs mill {avg * 100:.1f}%",
                             f"{float(r):,.0f} m of {float(t):,.0f} m - check the loom / weaver / beam quality")
        # physical count sample: the 5 store items with the most value issued this month
        for ic, idesc, uc, amt in self.q(f"""SELECT * FROM (SELECT v.ic, v.idesc, i.uc, SUM(v.amount) FROM dostex.store_issue_v v JOIN dostex.i i ON i.ic = v.ic
                WHERE TRUNC(v.dd) {W} GROUP BY v.ic, v.idesc, i.uc ORDER BY 4 DESC) WHERE ROWNUM <= 5"""):
            stck = self.cur.callfunc("dostex.get_item_stock", float, [ic, datetime.combine(self.d1, datetime.min.time()) + timedelta(days=1), uc])
            self.verify.append(f"Store count: {idesc} - ERP stock {stck if stck is not None else 0:g} (Rs {float(amt):,.0f} issued in {self.d0:%b})")
        # purchase orders keyed long after their date
        for po, pd, fd, u in self.q(f"SELECT po_no, TRUNC(po_date), TRUNC(feedd), usern FROM dostex.po_main WHERE TRUNC(feedd) {W} AND TRUNC(feedd) - TRUNC(po_date) > 30"):
            self.add("WATCH", "Store", f"pobackdate-{po}", f"PO {po} dated {pd:%d-%b-%y} keyed {fd:%d-%b-%y} ({(fd - pd).days} days later)", f"user {u}")
        # gate attendance vs ERP attendance (present days per employee code)
        tabs = read_gate()
        if tabs:
            emp = {str(r.get("Name") or "").strip().upper(): str(r.get("Code") or "").strip() for r in tabs.get("Employees", [])}
            gdays = defaultdict(set)
            for r in tabs.get("Attendance Log", []):
                if r.get("Date") and self.d0 <= r["Date"] <= self.d1 and r.get("In1"):
                    code = emp.get(str(r.get("Name") or "").strip().upper())
                    if code:
                        gdays[code].add(r["Date"])
            erp = {str(c): int(n) for c, n in self.q(f"SELECT emp_code, COUNT(DISTINCT TRUNC(dated)) FROM payroll.in_out WHERE TRUNC(dated) {W} AND attendance_type = 'P' GROUP BY emp_code")}
            for code, ds in gdays.items():
                e_ = erp.get(code, 0)
                if e_ - len(ds) >= 3:
                    self.add("WATCH", "Payroll", f"attgap-{code}-{m}", f"Employee code {code}: ERP present {e_} days, gate attendance log {len(ds)} days in {self.d0:%b}",
                             "ERP marks more days present than the gate recorded")

    # ------------------------------------------------------------------ BACKUP (owner, 1-Oct-2026: nightly 01:00 to D:, E: and the USB)
    def backup(self):
        """last night's ERP export: all 5 schemas in D:\\BACKUP and on the USB drive 'ERP BACKUP'; USB space"""
        import ctypes
        schemas = ("DOSTEX", "DOSTT", "DOSTT_GST", "DOST_PAYROLL", "PAYROLL")
        usb = None
        for L in "DEFGHIJKLMNOPQRSTUVWXYZ":
            buf = ctypes.create_unicode_buffer(64)
            if ctypes.windll.kernel32.GetVolumeInformationW(f"{L}:\\", buf, 64, None, None, None, None, 0) and buf.value.upper() == "ERP BACKUP":
                usb = Path(f"{L}:\\BACKUP"); break
        res = {}
        for where, folder in (("PC", Path(r"D:\BACKUP")), ("USB", usb)):
            if folder is None or not folder.exists():
                res[where] = None; continue
            newest = {}
            for s in schemas:
                fs = [f for f in folder.glob(f"{s}_*.dmp") if re.fullmatch(rf"{s}_\d\d-\d\d-\d{{4}}\.dmp", f.name, re.I)]
                if fs:
                    newest[s] = max(datetime.fromtimestamp(f.stat().st_mtime) for f in fs)
            res[where] = newest
        self.summary["backup"] = res
        today = datetime.combine(date.today(), datetime.min.time())
        for where, newest in res.items():
            if newest is None:
                self.add("ACTION", "Backup", f"backup-{where}-missing-{date.today():%Y%m%d}", f"ERP backup: {'USB drive ERP BACKUP not connected' if where == 'USB' else 'D:BACKUP folder missing'}", "")
                continue
            old = [s for s in schemas if s not in newest or newest[s] < today - timedelta(hours=7)]   # last night's 01:00 run
            # DOST_PAYROLL = the old payroll (2017-Sep-2023, no new entries); its Oracle password expired (ORA-28001, found 1-Oct-2026) and
            # only the ERP person can renew it - its last export (12-Jul-2026) still holds all its data, so it is a reminder, not an alarm
            if "DOST_PAYROLL" in old:
                old.remove("DOST_PAYROLL")
                self.add("WATCH", "Backup", f"backup-{where}-dostpayroll", f"ERP backup ({where}): old payroll DOST_PAYROLL not exported - its Oracle password has expired",
                         "ERP person to renew the password; last good export 12-Jul-2026 (old payroll, no new data)")
            if old:
                self.add("ACTION", "Backup", f"backup-{where}-{date.today():%Y%m%d}", f"ERP backup ({where}): last night's backup missing for {', '.join(old)}",
                         "newest: " + ", ".join(f"{s} {newest[s]:%d-%b}" if s in newest else f"{s} never" for s in old) + " - see D:\\BACKUP\\logs")
        if usb:
            free = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(str(usb.parent), None, None, ctypes.byref(free))
            gb = free.value / 1024 ** 3
            self.summary["backup_usb_free"] = gb
            if gb < 1.5:
                self.add("ACTION", "Backup", f"backup-usbfull-{date.today():%Y%m%d}", f"USB backup drive almost full: {gb:.1f} GB free",
                         "about 60 MB a night - move old backups off it or use a bigger drive")

    def sensor_twins(self):
        """Loom sensor moved onto another loom (owner, 3-Oct-2026): two looms stopping AND restarting within 2 s of each other,
        again and again - the scoreboard is counting one loom twice. Episodes from 77_sensor_twin_check.py."""
        _t = importlib.util.spec_from_file_location("t77", HERE / "77_sensor_twin_check.py"); t77 = importlib.util.module_from_spec(_t); _t.loader.exec_module(t77)
        res, _ = t77.scan(t77.con(), self.d0 - timedelta(days=1), self.d1 + timedelta(days=1))
        for x in res:
            if x["flag"] and self.d0 <= x["date"] <= self.d1:
                self.add("ACTION", "Weaving", f"twin-{x['date']}-{x['shift']}-{x['a']}-{x['b']}-{x['first']:%H%M}",
                         f"Looms {x['a']} & {x['b']}: stopped and restarted together {x['twins']} times, {x['first']:%d-%b %H:%M}-{x['last']:%H:%M} "
                         f"({'night' if x['shift'] == 2 else 'day'} shift)",
                         f"{x['share']:.0%} of their stops in that time were at the same second - one loom's sensor was probably on the other loom; "
                         "the scoreboard counted meters the stopped loom did not weave. Ask the shift jobber / check the camera.")
                self.verify.append(f"Looms {x['a']} & {x['b']}: who moved a sensor on the {'night' if x['shift'] == 2 else 'day'} shift of {x['date']:%d-%b} ({x['first']:%H:%M}-{x['last']:%H:%M})?")

    def run(self, monthly=False):
        for fn in (self.backup, self.finance, self.store, self.weaving, self.sensor_twins, self.looms_vs_history, self.beam_shrinkage, self.beams, self.yarn, self.fabric, self.payroll, self.gate):
            try:
                fn()
            except Exception as e:      # one section failing must not stop the others
                self.add("WATCH", fn.__name__.title(), f"err-{fn.__name__}", f"{fn.__name__} check could not run", str(e)[:200])
        if monthly:
            try:
                self.monthly()
            except Exception as e:
                self.add("WATCH", "Monthly", "err-monthly", "monthly checks could not run", str(e)[:200])
        self.F.sort(key=lambda f: (SEV_ORDER[f[0]], f[1]))


def drive_file_ok(url):
    """can the audit account open this Drive photo link?"""
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})|id=([A-Za-z0-9_-]{20,})", url)
    if not m:
        return False
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        c = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive.readonly"])
        build("drive", "v3", credentials=c).files().get(fileId=m.group(1) or m.group(2), fields="id").execute()
        return True
    except Exception:
        return False


def regno(v):
    """a gate register / ERP gate-entry number as text: 5190.0 -> '5190'"""
    s = str(v or "").strip()
    try:
        return str(int(float(s)))
    except ValueError:
        return s


def regnos(v):
    """ERP gate-entry field, which may hold a range ('4890-4894') or a list ('5230, 5231')"""
    out = set()
    for part in re.split(r"[,/ ]+", str(v or "").strip()):
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m and len(m.group(2)) < len(m.group(1)):
            m = re.fullmatch(r"(\d+)-(\d+)", m.group(1) + "-" + m.group(1)[:len(m.group(1)) - len(m.group(2))] + m.group(2))
        if m and 0 <= int(m.group(2)) - int(m.group(1)) <= 50:
            out |= {str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1)}
        elif part:
            out.add(regno(part))
    return out


def yarn_blend(s):
    """blend word from a yarn description / gate yarn type"""
    u = f" {str(s or '').upper()} "
    for key, bl in ((" SLUB", "SLUB"), (" VA", "VA"), ("VA ", "VA"), (" PC", "PC"), (" PV", "PV"), (" STP", "STP"), (" STAPLE", "STP"), (" VIS", "VIS"), (" CTN", "CTN"), (" COTTON", "CTN")):
        if key in u:
            return bl
    w = str(s or "").strip().upper()
    return {"CTN": "CTN", "VA": "VA", "PC": "PC", "PV": "PV", "SLUB": "SLUB", "STPL": "STP", "STP": "STP", "VIS": "VIS", "COTTON": "CTN"}.get(w)


def party_token(s):
    """first meaningful word of a party name, for loose matching (HAROON FABRIC FSD ~ HAROON FABRICS PVT LTD)"""
    for w in re.findall(r"[A-Za-z]+", str(s or "").upper()):
        if len(w) >= 3 and w not in ("THE", "MIAN", "SHEIKH"):
            return w[:5]
    return None


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def read_gate():
    """the gate software's Google Sheet, every tab as a list of dicts (header row 1); Date column parsed. None if no access."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        from openpyxl import load_workbook
        c = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive.readonly"])
        raw = build("drive", "v3", credentials=c).files().export(fileId=GATE_SHEET, mimeType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet").execute()
    except Exception:
        return None
    wb = load_workbook(io.BytesIO(raw), data_only=True)
    out = {}
    for ws in wb:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        head = [str(h or "").strip() for h in rows[0]]
        L = []
        for r in rows[1:]:
            d = dict(zip(head, r))
            dv = d.get("Date")
            if isinstance(dv, datetime):
                d["Date"] = dv.date()
            elif isinstance(dv, str):
                try:
                    d["Date"] = datetime.strptime(dv.strip()[:10], "%Y-%m-%d").date()
                except ValueError:
                    d["Date"] = None
            L.append(d)
        out[ws.title] = L
    return out


# ---------------------------------------------------------------------- output
def scb_day(d):
    """scoreboard mill summary for one day: (eff, meters, rpm) or None"""
    f = REPORTS / f"scoreboard_summary_{d:%Y-%m-%d}.csv"
    if f.exists():
        lines = f.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        try:
            v = lines[1].split(","); return float(v[1]), float(v[2]), float(v[3])
        except (IndexError, ValueError):
            return None
    return None


def erp_day(cur, d):
    """ERP production for one production date: (looms, meters, rejection m, weighted eff) or None if nothing entered yet"""
    r = cur.execute(f"""SELECT COUNT(DISTINCT lcode), SUM(total_mtr), SUM(NVL(c,0)+NVL(cp,0)) FROM dostex.wv_prod_v
                        WHERE tt = 'PRODUCTION' AND TRUNC(td) = DATE '{d:%Y-%m-%d}'""").fetchone()
    if not r[1]:
        return None
    eff = cur.execute(f"SELECT SUM(eff * total_mtr) / NULLIF(SUM(total_mtr),0) FROM dostex.wv_eff_v WHERE tt = 'PRODUCTION' AND TRUNC(td) = DATE '{d:%Y-%m-%d}'").fetchone()[0]
    return int(r[0]), float(r[1]), float(r[2] or 0), float(eff) if eff else None


def prod_line(d, e, s):
    """one day, ERP next to the scoreboard (owner: compare the SAME day)"""
    t = f"Production {d:%d-%b}: ERP {e[1]:,.0f} m on {e[0]} looms" + (f", eff {e[3]:.1f}%" if e[3] else "") + (f", rejection {e[2] / e[1] * 100:.1f}%" if e[1] else "")
    if s:
        t += f" | scoreboard {s[1]:,.0f} m, eff {s[0]:.1f}%, RPM {s[2]:.0f}"
    return t


def summary_lines(a):
    s = a.summary; L = []
    if a.d0 == a.d1:
        # owner (1-Oct-2026): the ERP production of the SAME previous day as the scoreboard. Yesterday's rolls are usually keyed the next
        # day or later, so if they are not in yet the latest day that is entered is shown against the scoreboard of that same day,
        # and a follow-up push (--erp-followup, hourly) sends yesterday's figures as soon as they are keyed.
        sc = scb_day(a.d1); e = erp_day(a.cur, a.d1)
        if e:
            L.append(prod_line(a.d1, e, sc))
        else:
            L.append(f"Scoreboard {a.d1:%d-%b}: " + (f"eff {sc[0]:.1f}% · {sc[1]:,.0f} m · RPM {sc[2]:.0f}" if sc else "file not received")
                     + f" | ERP production for {a.d1:%d-%b} not entered yet - will be sent when keyed")
            last = a.cur.execute(f"SELECT MAX(TRUNC(td)) FROM dostex.wv_prod_v WHERE tt = 'PRODUCTION' AND td < DATE '{a.d1:%Y-%m-%d}'").fetchone()[0]
            if last:
                L.append("Last day entered - " + prod_line(last.date(), erp_day(a.cur, last.date()), scb_day(last.date())))
    else:
        scb = s.get("scb") or {}
        if scb:
            L.append(f"Scoreboard: eff {sum(v[0] for v in scb.values()) / len(scb):.1f}% · {sum(v[1] for v in scb.values()):,.0f} m")
        p = s.get("prod") or {}
        if p:
            L.append(f"ERP production: {p['m']:,.0f} m" + (f" · eff {p['eff']:.1f}%" if p.get("eff") else ""))
    f = s.get("fabric") or {}
    if f:
        L.append(f"Fabric out: {f['ogp']} OGP, {f['m']:,.0f} m · stock {f['stock']:,.0f} m fresh + {f['rej']:,.0f} m rejection")
    y = s.get("yarn") or {}
    if y:
        L.append("Yarn: " + ", ".join(f"{k.replace('_', ' ').lower()} {v:,.1f}" for k, v in y.items() if v) + " bags")
    st = s.get("store") or {}
    if st:
        L.append(f"Store: {st.get('ISS', (0, 0))[0]} issue slips Rs {st.get('ISS', (0, 0))[1]:,.0f} · {st.get('SRN', (0, 0))[0]} GRNs Rs {st.get('SRN', (0, 0))[1]:,.0f}")
    b = s.get("backup")
    if b is not None:
        def last(n):         # the oldest of the 4 live schemas (old payroll DOST_PAYROLL is reported separately)
            live = {k: v for k, v in (n or {}).items() if k != "DOST_PAYROLL"}
            return min(live.values()) if len(live) == 4 else None
        pc, us = last(b.get("PC") or {}), last(b.get("USB") or {})
        L.append("ERP backup: PC " + (f"{pc:%d-%b %H:%M}" if pc else "INCOMPLETE") + " · USB " + (f"{us:%d-%b %H:%M}" if us else ("not connected" if b.get("USB") is None else "INCOMPLETE"))
                 + (f" ({s.get('backup_usb_free', 0):.1f} GB free)" if s.get("backup_usb_free") is not None else ""))
    v = s.get("vouchers") or {}
    if v:
        L.append("Accounts: " + ", ".join(f"{k} {n}" for k, (n, _) in sorted(v.items())) + f" · cash in hand Rs {s.get('cash', 0):,.0f}")
    pr = s.get("payroll") or {}
    if pr and any(pr.values()):
        L.append(f"Payroll entries: attendance {pr['att']}, overtime {pr['ot']}, loans {pr['loans']}")
    g = s.get("gate")
    if g is None:
        L.append("Gate log: not readable (share needed)")
    elif g:
        L.append(f"Gate: {g['inward']} inward, {g['outward']} outward, {g['guests']} guests, {g['attendance']} attendance lines" + (f", diesel {g['diesel'][0]} mm" if g.get("diesel") else ""))
        ph = g.get("photos") or {}
        if ph:
            L.append("Photos attached: " + ", ".join(f"{k.replace(' Log', '').lower()} {a}/{b}" for k, (a, b) in ph.items() if b))
    return L


def build_pdf(a, path, title):
    ss = getSampleStyleSheet(); sm = ParagraphStyle("sm", parent=ss["BodyText"], fontSize=8, leading=10)
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A4), leftMargin=1.2 * cm, rightMargin=1.2 * cm, topMargin=1.1 * cm, bottomMargin=1.1 * cm)
    el = [Paragraph(title, ss["Title"]), Paragraph("ERP read only. ACTION = do something today · CONFIRMED = definite error in the data · WATCH = unusual, have a look.", sm), Spacer(1, 6)]
    el += [Paragraph("<b>Summary</b>", ss["Heading2"])] + [Paragraph("• " + t, ss["BodyText"]) for t in summary_lines(a)] + [Spacer(1, 8)]
    lt = a.summary.get("looms")
    if lt and a.d0 != a.d1:
        a0, files, rm, tbl = lt
        el.append(Paragraph(f"<b>ERP production vs scoreboard, loom by loom ({a0:%d-%b} to {a.d1:%d-%b})</b>", ss["Heading2"]))
        el.append(Paragraph(f"The scoreboard recorded {files} of {(a.d1 - a0).days + 1} days, and the ERP books rolls every few days, so each loom is compared with "
                            f"the mill's own ratio (ERP = {rm:.2f} x scoreboard for all looms together). 'Expected' = the loom's scoreboard meters x {rm:.2f}. "
                            "Red = more than 15% away.", sm))
        rows = [["Loom", "ERP m", "Scoreboard m", "Expected ERP m", "Difference", "vs mill"]]
        sty = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3B57")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8),
               ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9AA5B1")), ("ALIGN", (1, 0), (-1, -1), "RIGHT")]
        for i, (ln, e, s, x, dev) in enumerate(tbl, 1):
            rows.append([ln, f"{e:,.0f}", f"{s:,.0f}", f"{x:,.0f}", f"{e - x:+,.0f}", f"{dev * 100:+.0f}%"])
            if abs(dev) > 0.15:
                sty.append(("BACKGROUND", (5, i), (5, i), colors.HexColor("#F8C9C9")))
        t = Table(rows, colWidths=[3 * cm, 3 * cm, 3 * cm, 3.4 * cm, 3 * cm, 2.4 * cm], repeatRows=1); t.setStyle(TableStyle(sty))
        el += [t, Spacer(1, 8)]
    P = a.P or {}
    if P:
        lg = P.get("voucher_lag_p95") or {}; lp = P.get("loom_gap_p90") or {}
        el += [Paragraph("<b>What the audit has learned about how the factory enters data (last 120 days)</b>", ss["Heading2"]),
               Paragraph("• Vouchers are normally keyed within (days, 95% of them): " + ", ".join(f"{k} {v}" for k, v in sorted(lg.items())), sm),
               Paragraph(f"• Each loom's production is normally booked every {min(lp.values()) if lp else '?'}-{max(lp.values()) if lp else '?'} days "
                         "(a loom is called idle only past its own usual gap)", sm),
               Paragraph(f"• The ERP entry normally follows the gate within {P.get('gate_to_erp_p90', '?')} day(s)", sm),
               Paragraph("• Suppliers who reuse bill-book numbers (only same-amount repeats are flagged): " + (", ".join(P.get("bill_reuse") or []) or "none"), sm),
               Paragraph(f"• Issue slips with quantity 0: about {P.get('zero_qty_slips_per_month', 0)} a month (demand slips)", sm),
               Paragraph(f"• Patterns you have confirmed as normal: {len(a.rules.get('dismiss', []))}", sm), Spacer(1, 8)]
    if a.verify:
        el += [Paragraph("<b>Please check physically</b>", ss["Heading2"])] + [Paragraph("• " + t, ss["BodyText"]) for t in dict.fromkeys(a.verify)] + [Spacer(1, 8)]
    el.append(Paragraph(f"<b>Findings ({len(a.F)})</b>", ss["Heading2"]))
    if a.F:
        rows = [["Grade", "Section", "Finding", "Detail"]] + [[f[0], f[1], Paragraph(f[3], sm), Paragraph(f[4] or "", sm)] for f in a.F]
        t = Table(rows, colWidths=[2.2 * cm, 2.2 * cm, 12 * cm, 10.5 * cm], repeatRows=1)
        sty = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3B57")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
               ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9AA5B1"))]
        for i, f in enumerate(a.F, 1):
            sty.append(("BACKGROUND", (0, i), (0, i), colors.HexColor({"ACTION": "#F8C9C9", "CONFIRMED": "#FFE3B3", "WATCH": "#E8EDF2", "INFO": "#FFFFFF"}[f[0]])))
        t.setStyle(TableStyle(sty)); el.append(t)
    else:
        el.append(Paragraph("No findings.", ss["BodyText"]))
    doc.build(el)


def drive_link(name):
    link = "https://drive.google.com/drive/folders/18CYkYptYXvrrq_szDHt4lbtsfyK4j3W5"
    try:
        import time
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        drv = build("drive", "v3", credentials=service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"),
                                                                                                   scopes=["https://www.googleapis.com/auth/drive.readonly"]))
        for _ in range(12):
            r = drv.files().list(q=f"name = '{name}' and trashed = false", fields="files(id)").execute()
            if r.get("files"):
                return f"https://drive.google.com/file/d/{r['files'][0]['id']}/view"
            time.sleep(20)
    except Exception:
        pass
    return link


def push(title, body, click, priority="default"):
    req = urllib.request.Request(url=f"https://ntfy.sh/{CFG.get('ntfy', 'topic')}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": priority, "Click": click, "Tags": "clipboard"})
    urllib.request.urlopen(req, timeout=30).read()


def erp_followup():
    """hourly (10:00-21:00): once yesterday's (or the day before's) production is keyed in the ERP, push it next to the scoreboard of
    the same day - once per day (state file)"""
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    cur = dash.connect(dash.load_config()).cursor()
    for back in (2, 1):
        d = date.today() - timedelta(days=back)
        key = f"prodsent-{d:%Y-%m-%d}"
        if key in state:
            continue
        e = erp_day(cur, d)
        # only when the day looks complete: most looms booked (rolls of a day are keyed together)
        if e and e[0] >= 5:
            push(f"ERP production - {d:%a %d-%b}", prod_line(d, e, scb_day(d)), "https://drive.google.com/drive/folders/18CYkYptYXvrrq_szDHt4lbtsfyK4j3W5")
            state[key] = f"{datetime.now():%Y-%m-%d %H:%M}"
            print("pushed", key)
    STATE.write_text(json.dumps(state, indent=0))


def main():
    args = sys.argv[1:]
    if "--erp-followup" in args:
        return erp_followup()
    monthly = "--monthly" in args
    if monthly:
        if "--month" in args:
            y, mo = map(int, args[args.index("--month") + 1].split("-"))
        else:
            t = date.today().replace(day=1) - timedelta(days=1); y, mo = t.year, t.month
        d0 = date(y, mo, 1); d1 = (date(y + mo // 12, mo % 12 + 1, 1) - timedelta(days=1))
        label = f"{d0:%B %Y}"
    else:
        d0 = d1 = datetime.strptime(args[args.index("--date") + 1], "%Y-%m-%d").date() if "--date" in args else date.today() - timedelta(days=1)
        label = f"{d0:%a %d-%b-%Y}"
    a = Audit(d0, d1, label)
    a.run(monthly)
    folder = REPORTS / "ERP AUDIT"; folder.mkdir(parents=True, exist_ok=True)
    name = f"{d0:%Y-%m} monthly audit.pdf" if monthly else f"{d0:%Y-%m-%d} daily audit.pdf"
    title = f"Dost Textiles - {'monthly ERP audit' if monthly else 'daily ERP check'} - {label}"
    build_pdf(a, folder / name, title)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    urgent = [f for f in a.F if f[0] in ("ACTION", "CONFIRMED")]
    new = [f for f in urgent if f[2] not in state]
    verify = list(dict.fromkeys(a.verify))
    new_verify = [v for v in verify if "verify:" + v not in state]
    for f in urgent:
        state.setdefault(f[2], f"{date.today():%Y-%m-%d}")
    for v in verify:
        state.setdefault("verify:" + v, f"{date.today():%Y-%m-%d}")
    print(f"{label}: {len(a.F)} findings, {len(urgent)} action/confirmed ({len(new)} new), {len(verify)} physical checks -> {folder / name}")
    for f in a.F:
        print("  ", f[0], "|", f[1], "|", f[3], "|", f[4])
    for v in verify:
        print("   VERIFY |", v)
    if "--no-push" in args:
        return
    STATE.write_text(json.dumps(state, indent=0))
    # monthly: owner (30-Sep-2026) - if everything is fine send nothing; only what is worth checking
    show = urgent if monthly else new
    ver = verify if monthly else new_verify
    if monthly and not show and not ver:        # owner: a short all-clear is fine
        push(f"Monthly ERP audit - {label}", "All checks done - everything is fine, nothing for you to check.", drive_link(name))
        print("pushed all-clear")
        return
    lines = [] if monthly else summary_lines(a)
    if show:
        lines.append("")
        lines.append(f"NEEDS ATTENTION ({len(show)}):")
        lines += [f"{'!' if f[0] == 'ACTION' else '*'} {f[1]}: {f[3]}" for f in show[:12]]
        if len(show) > 12:
            lines.append(f"... {len(show) - 12} more in the PDF")
    if ver:
        lines.append("")
        lines.append("PLEASE CHECK PHYSICALLY:")
        lines += [f"- {v}" for v in ver[:8]]
    if not monthly and not show and not ver:
        lines.append("")
        lines.append("No new problems found." + (f" ({len(urgent)} earlier items still open - see PDF)" if urgent else ""))
    push(f"{'Monthly ERP audit' if monthly else 'Morning summary'} - {label}", "\n".join(lines).strip(), drive_link(name),
         "high" if any(f[0] == "ACTION" for f in show) else "default")
    print("pushed")


if __name__ == "__main__":
    main()
