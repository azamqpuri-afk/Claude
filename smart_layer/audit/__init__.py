"""
ERP audit (generalised from Dost Textiles' 70_erp_audit.py).

Runs rules over a date window of the local store and grades each finding:
  ACTION     do something today
  CONFIRMED  a definite error in the data
  WATCH      unusual - listed in the report only
  INFO       dismissed by the owner as normal practice (config audit.dismiss)

Thresholds adapt to each mill: the audit first learns, from the last 120 days,
how this mill normally keys its data (how late each voucher type is entered,
how often each loom's production is booked, which suppliers reuse bill
numbers) so a flag means "unusual for THIS mill", not "breaks a textbook rule".

Only ACTION and CONFIRMED findings are pushed, each one once.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from ..reports import Report
from ..store import get_state, set_state

SEVERITIES = ("ACTION", "CONFIRMED", "WATCH", "INFO")


@dataclass
class Finding:
    severity: str
    section: str
    key: str          # stable id, so a finding is pushed only once
    title: str
    detail: str = ""


def pct(values, p):
    v = sorted(values)
    return v[min(len(v) - 1, int(p * (len(v) - 1)))] if v else None


class Audit:
    def __init__(self, con, cfg, d0, d1):
        self.con, self.cfg, self.d0, self.d1 = con, cfg, d0, d1
        self.t = cfg["audit"]
        self.cur = cfg["company"]["currency"]
        self.findings = []
        self.summary = []
        self.profile = self.learn()

    def q(self, sql, *params):
        return self.con.execute(sql, params).fetchall()

    @property
    def window(self):
        return self.d0.isoformat(), self.d1.isoformat()

    def add(self, severity, section, key, title, detail=""):
        for rule in self.t.get("dismiss", []):
            if re.search(rule.get("match", "^$"), f"{key} {title}", re.I):
                severity, detail = "INFO", f"{detail} [normal practice: {rule.get('why', '')}]".strip()
                break
        self.findings.append(Finding(severity, section, key, title, detail))

    def learn(self):
        start = (self.d1 - timedelta(days=120)).isoformat()
        P = {}
        lags = {}
        for vt, lag in self.q("""SELECT voucher_type, MAX(julianday(date(entered_at)) - julianday(voucher_date))
                                 FROM ledger_entries WHERE entered_at IS NOT NULL AND voucher_date >= ?
                                 GROUP BY voucher_id""", start):
            lags.setdefault(vt, []).append(max(0, lag or 0))
        P["voucher_lag_p95"] = {vt: pct(v, 0.95) for vt, v in lags.items() if len(v) >= 10}
        gaps, prev = {}, {}
        for loom, d in self.q("""SELECT DISTINCT loom_code, prod_date FROM loom_production
                                 WHERE prod_date >= ? ORDER BY 1, 2""", start):
            if loom in prev:
                gaps.setdefault(loom, []).append((date.fromisoformat(d) - date.fromisoformat(prev[loom])).days)
            prev[loom] = d
        P["loom_gap_p90"] = {k: pct(v, 0.9) for k, v in gaps.items() if len(v) >= 5}
        P["bill_reuse"] = sorted({s for (s,) in self.q("""
            SELECT DISTINCT x.supplier_code FROM store_receipts x JOIN store_receipts y
              ON y.supplier_code = x.supplier_code AND y.supplier_bill_no = x.supplier_bill_no AND y.doc_no > x.doc_no
             AND ABS(julianday(y.doc_date) - julianday(x.doc_date)) > 3
            WHERE x.supplier_bill_no IS NOT NULL AND x.doc_date >= ?""", (self.d1 - timedelta(days=365)).isoformat()) if s})
        n = self.q("SELECT COUNT(*) FROM store_issues WHERE COALESCE(qty, 0) <= 0 AND doc_date >= ?",
                   (self.d1 - timedelta(days=120)).isoformat())[0][0]
        P["zero_qty_issues_per_month"] = round(n / 4, 1)
        return P

    def report(self):
        order = {s: i for i, s in enumerate(SEVERITIES)}
        rows = sorted(self.findings, key=lambda f: (order[f.severity], f.section, f.title))
        n = {s: sum(1 for f in rows if f.severity == s) for s in SEVERITIES}
        period = f"{self.d0:%d-%b-%Y}" if self.d0 == self.d1 else f"{self.d0:%d-%b-%Y} to {self.d1:%d-%b-%Y}"
        return Report(f"ERP audit - {period}", ["Severity", "Section", "Finding", "Detail"],
                      [[f.severity, f.section, f.title, f.detail] for f in rows],
                      [f"{n['ACTION']} action, {n['CONFIRMED']} confirmed errors, {n['WATCH']} to watch, "
                       f"{n['INFO']} dismissed as normal practice.", *self.summary])

    def new_alerts(self, remember=True):
        """ACTION / CONFIRMED findings not pushed before."""
        seen = set(get_state(self.con, "audit_pushed", []))
        fresh = [f for f in self.findings if f.severity in ("ACTION", "CONFIRMED") and f.key not in seen]
        if remember:
            set_state(self.con, "audit_pushed", sorted(seen | {f.key for f in fresh})[-20000:])
        return fresh


def run(con, cfg, d0, d1=None):
    from . import beams, finance, store, weaving, yarn
    a = Audit(con, cfg, d0, d1 or d0)
    for rules in (finance, store, weaving, yarn, beams):
        rules.run(a)
    return a
