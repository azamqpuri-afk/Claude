"""
Scheduled weekly/monthly jobs with catch-up (item B4).

The mill PC is often switched off at night or over a weekend, so a weekly (Friday) or monthly (1st / 10th) job can
sail past without ever running - the owner silently loses that report. This module keeps the schedule in Python (not
in a PowerShell date test inside run_daily.cmd) so it is testable, and makes every job *catch up*: on each daily run
it works out the most recent date each job was due, and runs any job that was due on or before today but has not yet
run for that period. The last-run date per job lives in the store's generic `state` table (key "job:<slot>").

The daily task calls `SmartLayer.exe run-scheduled --push`; cli.py maps each due slot to the reports it runs.
"""
import datetime

# slot -> human label. The reports each slot runs are defined in cli.py (they need the report runner), keyed by slot.
SLOTS = {
    "weekly":      "weekly reports (due every Friday)",
    "monthly-1":   "month-start reports (due on the 1st)",
    "monthly-10":  "previous-month audit (due on the 10th)",
}


def _recent_weekday(today, weekday):
    """The most recent date <= today that falls on `weekday` (Mon=0 .. Sun=6). For Friday(4) today=Sun -> last Fri."""
    return today - datetime.timedelta(days=(today.weekday() - weekday) % 7)


def _recent_day_of_month(today, dom):
    """The most recent date <= today whose day-of-month is `dom`. Before the `dom`th, it is last month's `dom`th.
    `dom` is 1..28 here (both our monthly jobs use 1 and 10), so the day always exists."""
    if today.day >= dom:
        return today.replace(day=dom)
    first = today.replace(day=1)
    prev_month_end = first - datetime.timedelta(days=1)
    return prev_month_end.replace(day=dom)


def trigger_date(slot, today):
    """The most recent date on or before `today` when this slot was due."""
    if slot == "weekly":
        return _recent_weekday(today, 4)            # Friday
    if slot == "monthly-1":
        return _recent_day_of_month(today, 1)
    if slot == "monthly-10":
        return _recent_day_of_month(today, 10)
    raise KeyError(slot)


def _last_run(con, slot):
    from . import store
    v = store.get_state(con, f"job:{slot}")
    try:
        return datetime.date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def due(con, today):
    """Slots whose most-recent trigger date is on/before today and has not run for that period yet. A job that
    already ran today (or later in this period) is not due again; a missed Friday/1st/10th still shows up."""
    out = []
    for slot in SLOTS:
        trig = trigger_date(slot, today)
        last = _last_run(con, slot)
        if last is None or last < trig:
            out.append(slot)
    return out


def mark(con, slot, today):
    from . import store
    store.set_state(con, f"job:{slot}", today.isoformat())
