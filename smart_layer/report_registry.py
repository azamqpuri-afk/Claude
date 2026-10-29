"""
One list of every report, used by both the command line and the owner dashboard so the two never drift apart.

Each entry is  name -> (builder, label shown to the owner, day_report)
  builder(con, cfg, day) -> Report
  label      plain words for the dashboard's report menu
  day_report True when the report is for a single day (defaults to yesterday), False for a position "as of today"
"""

from datetime import date, timedelta

from .reports import finance, gatepass, payroll, production, reconcile, stock
from .textile import beams, closing, pilferage, stock_position

REPORTS = {
    "stock":        (stock.stock_status,            "Store: stock status (out / low / in stock)", False),
    "zero":         (stock.new_zero,                "Store: items newly at zero", False),
    "reorder":      (stock.reorder_list,            "Store: reorder list", False),
    "production":   (production.daily_production,   "Production on the day (per loom)", True),
    "value":        (production.production_value,   "Production value (earnings per loom / contract)", True),
    "closing":      (closing.contract_closing,      "Contract closing (party yarn balance)", False),
    "trial-balance": (finance.trial_balance,         "Finance: trial balance check (as of the To date)", False),
    "unbalanced":   (finance.unbalanced,             "Finance: unbalanced entries (debit ≠ credit)", False),
    "pilferage":    (pilferage.pilferage,           "Yarn: pilferage check", True),
    "beams":        (beams.beam_report,             "Beams on looms (days left, backups)", False),
    "sizers":       (beams.sizer_ledger,            "Empty beams with sizers (by quantity)", False),
    "pnl":          (finance.pnl,                   "Finance: profit and loss", False),
    "balances":     (finance.balances,              "Finance: balance summary", False),
    "parties":      (finance.parties,               "Finance: party balances", False),
    "store-recon":  (reconcile.store_consumption,   "Store: consumption reconciliation", False),
    "yarn-stock":   (stock_position.yarn_stock,     "Yarn stock position", False),
    "fabric-stock": (stock_position.fabric_stock,   "Fabric stock position", False),
    "gate-register": (gatepass.register,            "Gate passes: latest register", False),
    "gate-summary": (gatepass.summary,              "Gate passes: summary by type", True),
    "wage-bill":    (payroll.wage_bill,             "Payroll: wage bill by section", False),
    "payslips":     (payroll.payslips,              "Payroll: payslips for the period", False),
    "staff":        (payroll.staff,                 "Payroll: employee list", False),
    "emp-loans":    (payroll.loans,                 "Payroll: employee loans & advances", False),
}

NAMES = list(REPORTS)

# reports that take a from/to window (builder signature (con, cfg, d0, d1)); everything else takes a single as-of day.
RANGE = {"unbalanced"}


def build(con, cfg, name, day=None, d0=None, d1=None):
    """The Report for one name. A range report uses d0/d1 (To defaults to today, From to a year before). Every other
    report is a single-day / position report and uses the To date as its as-of (falling back to its own default)."""
    builder = REPORTS[name][0]
    if name in RANGE:
        return builder(con, cfg, d0, d1 or day)
    return builder(con, cfg, report_day(name, d1 or day))


def report_day(name, day=None):
    if day:
        return day
    return date.today() - timedelta(days=1) if REPORTS[name][2] else date.today()


def menu():
    """[(name, label)] for the dashboard's report list."""
    return [(name, label) for name, (_, label, _) in REPORTS.items()]
