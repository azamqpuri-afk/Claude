"""
Command line for Project Smart Layer by Azam Puri.

  python -m smart_layer check                    test the ERP connection and mapping
  python -m smart_layer sync                     refresh the local store from the ERP
  python -m smart_layer report stock|zero|reorder|production [--date D] [--csv] [--push]
  python -m smart_layer audit [--date D | --month YYYY-MM] [--csv] [--push]
  python -m smart_layer ask ["question"]         ask the ERP (interactive without a question)
  python -m smart_layer schema                   print the standard data model

Use -c to point at a config file (default config.toml).
"""

import argparse
import calendar
import sys
from datetime import date, timedelta

from . import PRODUCT, config, erp, notify, schema, store
from .reports import production, stock


def _open_erp(cfg):
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    con, style = erp.connect(cfg["erp"], cfg.base_dir)
    return erp.ERP(con, style, mapping)


def _store(cfg):
    return store.open_store(cfg.path(cfg["store"]["path"]))


def cmd_check(cfg, args):
    e = _open_erp(cfg)
    print(f"Mapping: {e.mapping.name}")
    since = date.today() - timedelta(days=30)
    ok = True
    for ent in schema.ENTITIES:
        if ent.name not in e.mapping.entities:
            print(f"  {ent.name:<17} not mapped")
            continue
        try:
            n = sum(1 for _ in e.fetch(ent.name, since))
            print(f"  {ent.name:<17} ok ({n:,} rows{' in the last 30 days' if ent.date_column else ''})")
        except Exception as ex:                      # report every entity, not just the first failure
            ok = False
            print(f"  {ent.name:<17} FAILED: {ex}")
    return 0 if ok else 1


def cmd_sync(cfg, args):
    e = _open_erp(cfg)
    print(f"Syncing from {e.mapping.name} (last {cfg['sync']['history_days']} days of dated records)")
    store.sync(_store(cfg), e, cfg["sync"]["history_days"], entities=args.entities)
    return 0


def cmd_report(cfg, args):
    con = _store(cfg)
    day = date.fromisoformat(args.date) if args.date else None
    builders = {"stock": stock.stock_status, "zero": stock.new_zero, "reorder": stock.reorder_list,
                "production": production.daily_production}
    rep = builders[args.name](con, cfg, day)
    print(rep.text())
    if args.csv:
        tag = (day or date.today()).isoformat()
        print("\nSaved", rep.write_csv(cfg.path(cfg["reports"]["output_dir"]), f"{args.name}_{tag}.csv"))
    if args.push and (rep.rows or args.name != "zero"):
        notify.send(cfg, rep.title, "\n".join(rep.summary) + ("\n\n" + rep.text(limit=15) if args.name == "zero" else ""))
    return 0


def cmd_audit(cfg, args):
    from . import audit
    if args.month:
        y, m = map(int, args.month.split("-"))
        d0, d1 = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    else:
        d0 = d1 = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    a = audit.run(_store(cfg), cfg, d0, d1)
    rep = a.report()
    print(rep.text())
    if args.csv:
        print("\nSaved", rep.write_csv(cfg.path(cfg["reports"]["output_dir"]), f"audit_{d0}_{d1}.csv"))
    if args.push:
        fresh = a.new_alerts()
        if fresh:
            body = "\n".join(f"[{f.severity}] {f.title}" for f in fresh[:25])
            if len(fresh) > 25:
                body += f"\n... and {len(fresh) - 25} more in the report"
            notify.send(cfg, f"ERP audit {d1:%d-%b}: {len(fresh)} new", body, priority="high")
    return 0


def cmd_ask(cfg, args):
    from .ask import Assistant
    bot = Assistant(cfg)
    show = (lambda sql: print(f"  [query] {' '.join(sql.split())[:160]}")) if args.show_sql else None
    questions = [" ".join(args.question)] if args.question else None
    if not questions:
        print(f"{PRODUCT} - ask about your mill (empty line to quit)")
    while True:
        q = questions.pop(0) if questions is not None else input("\nYou: ").strip()
        if not q:
            return 0
        answer, _ = bot.ask(q, on_sql=show)
        print(f"\n{answer}")
        if questions is not None and not questions:
            return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="smart_layer", description=PRODUCT)
    p.add_argument("-c", "--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("sync")
    s.add_argument("--entities", nargs="*")
    r = sub.add_parser("report")
    r.add_argument("name", choices=["stock", "zero", "reorder", "production"])
    r.add_argument("--date")
    r.add_argument("--csv", action="store_true")
    r.add_argument("--push", action="store_true")
    a = sub.add_parser("audit")
    a.add_argument("--date")
    a.add_argument("--month")
    a.add_argument("--csv", action="store_true")
    a.add_argument("--push", action="store_true")
    q = sub.add_parser("ask")
    q.add_argument("question", nargs="*")
    q.add_argument("--show-sql", action="store_true")
    sub.add_parser("schema")
    args = p.parse_args(argv)
    if args.cmd == "schema":
        print(schema.describe())
        return 0
    cfg = config.load(args.config)
    return {"check": cmd_check, "sync": cmd_sync, "report": cmd_report, "audit": cmd_audit, "ask": cmd_ask}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
