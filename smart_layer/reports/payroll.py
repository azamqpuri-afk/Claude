"""
Payroll reports, written only against the canonical employees / payroll / employee_loans data, so they work for
any mill once its payroll is mapped. Net pay is already computed in the mapping (earned + overtime - deductions).
"""

from datetime import date, timedelta

from . import Report


def _latest_month(con, as_of=None):
    months = [r[0] for r in con.execute(
        "SELECT DISTINCT pay_month FROM payroll WHERE pay_month IS NOT NULL ORDER BY pay_month")]
    if not months:
        return None
    if as_of:
        want = f"{as_of.year:04d}-{as_of.month:02d}"
        if want in months:
            return want
    return months[-1]


def wage_bill(con, cfg, as_of=None):
    """Wage bill for the latest pay month (or the month of as_of), split by section, with a duplicate-run warning."""
    cols = ["Section", "Employees", "Earned", "Overtime", "Deductions", "Net pay"]
    month = _latest_month(con, as_of)
    if not month:
        return Report("Wage bill", cols, [], ["No payroll data has been synced yet."])
    rows = con.execute("""SELECT COALESCE(section, '-'), COUNT(DISTINCT emp_code),
                                 SUM(earned), SUM(overtime), SUM(deductions), SUM(net_pay)
                          FROM payroll WHERE pay_month = ? GROUP BY section ORDER BY 1""", (month,)).fetchall()
    out = [[s, n, round(e or 0), round(o or 0), round(d or 0), round(nt or 0)] for s, n, e, o, d, nt in rows]
    tot = con.execute("""SELECT COUNT(DISTINCT emp_code), SUM(earned), SUM(overtime), SUM(deductions), SUM(net_pay)
                         FROM payroll WHERE pay_month = ?""", (month,)).fetchone()
    periods = con.execute("SELECT COUNT(DISTINCT period_start || '|' || period_end) FROM payroll WHERE pay_month = ?",
                          (month,)).fetchone()[0]
    span = con.execute("SELECT MIN(period_start), MAX(period_end) FROM payroll WHERE pay_month = ?", (month,)).fetchone()
    dates = f" ({span[0]} to {span[1]})" if span and span[0] else ""
    summary = [f"Wage bill for {month}{dates}: {tot[0] or 0} employees, net pay {round(tot[4] or 0):,}."]
    if periods > 2:
        summary.append(f"WARNING: {periods} pay periods in {month} - a month usually has 2 (1-15 and 16-end). "
                       "Check for a duplicate payroll run before trusting the totals.")
    return Report(f"Wage bill - {month}", cols, out, summary)


def payslips(con, cfg, as_of=None):
    """Each employee's pay for the latest period (or the period that contains as_of)."""
    cols = ["Code", "Name", "Section", "Monthly salary", "Days", "Earned", "Overtime", "Deductions", "Net pay"]
    periods = con.execute("SELECT DISTINCT period_start, period_end FROM payroll ORDER BY period_end").fetchall()
    if not periods:
        return Report("Payslips", cols, [], ["No payroll data has been synced yet."])
    ps, pe = periods[-1]
    if as_of:
        inside = [(a, b) for a, b in periods if a <= as_of.isoformat() <= b]
        if inside:
            ps, pe = inside[-1]
    rows = con.execute("""SELECT p.emp_code, e.name, p.section, e.monthly_salary,
                                 p.worked_days, p.earned, p.overtime, p.deductions, p.net_pay
                          FROM payroll p LEFT JOIN employees e ON e.emp_code = p.emp_code
                          WHERE p.period_start = ? AND p.period_end = ? ORDER BY p.emp_code""", (ps, pe)).fetchall()
    out = [[c, n, s, round(sal or 0), round(d or 0, 1), round(ea or 0), round(o or 0), round(de or 0), round(nt or 0)]
           for c, n, s, sal, d, ea, o, de, nt in rows]
    net = sum(r[8] for r in out)
    return Report(f"Payslips {ps} to {pe}", cols, out,
                  [f"Pay period {ps} to {pe}: {len(out)} employees, total net pay {net:,}."])


def loans(con, cfg, as_of=None):
    """Open employee loans and advances. The ERP's 'completed' flag is unreliable, so a loan is open when
    amount - repaid (repaid = salary deductions booked against it) is still positive."""
    cols = ["Code", "Name", "Type", "Given", "Amount", "Repaid", "Balance"]
    rows = con.execute("""SELECT l.emp_code, e.name, l.loan_type, l.loan_date, l.amount, l.repaid
                          FROM employee_loans l LEFT JOIN employees e ON e.emp_code = l.emp_code
                          ORDER BY l.loan_date DESC""").fetchall()
    out = []
    for c, n, t, d, a, r in rows:
        bal = round((a or 0) - (r or 0))
        if bal <= 1:                                   # fully (or over-) recovered - not open
            continue
        out.append([c, n, t, d, round(a or 0), round(r or 0), bal])
    total = sum(x[6] for x in out)
    return Report("Employee loans and advances (open)", cols, out,
                  [f"{len(out)} open loans / advances, balance {total:,} still to recover." if out
                   else "No open loans or advances."])


def staff(con, cfg, as_of=None):
    """Employees actually working now: not quit, and either present recently or paid in the last ~2 months
    (the latter also catches a new joiner who has not had a salary run yet). The ERP's status flag alone marks
    almost everyone active, so it is not used."""
    as_of = as_of or date.today()
    present_since = (as_of - timedelta(days=45)).isoformat()
    paid_since = (as_of - timedelta(days=62)).isoformat()
    cols = ["Code", "Name", "Department", "Designation", "Section", "Monthly salary"]
    rows = con.execute("""SELECT e.emp_code, e.name, COALESCE(e.department, '-'), COALESCE(e.designation, '-'),
                                 COALESCE(e.section, '-'), e.monthly_salary
                          FROM employees e
                          WHERE COALESCE(UPPER(e.quit), 'N') NOT IN ('Y', '1', 'TRUE', 'LEFT', 'QUIT', 'RESIGNED', 'TERMINATED')
                            AND (EXISTS (SELECT 1 FROM attendance a WHERE a.emp_code = e.emp_code AND a.last_present >= ?)
                                 OR EXISTS (SELECT 1 FROM payroll p WHERE p.emp_code = e.emp_code AND p.period_end >= ?))
                          ORDER BY e.department, e.name""", (present_since, paid_since)).fetchall()
    out = [[c, n, dept, desg, sec, round(sal or 0)] for c, n, dept, desg, sec, sal in rows]
    return Report("Employees (active)", cols, out, [f"{len(out)} employees working now."])
