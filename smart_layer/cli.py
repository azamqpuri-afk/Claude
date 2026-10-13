"""
Command line for Project Smart Layer by Azam Puri.

  python -m smart_layer setup                    first-time setup: asks questions, writes config.toml and .env
  python -m smart_layer status                   health of this installation (last sync, dashboard, tasks)
  python -m smart_layer grants [--user NAME]     SQL to create the read-only database user the mapping needs
  python -m smart_layer machine-id               this server's ID (send it to get a licence)
  python -m smart_layer licence show|install|check [file or code] [--push]
  python -m smart_layer check                    test the ERP connection and mapping
  python -m smart_layer sync                     refresh the local store from the ERP
  python -m smart_layer report <name> [--date D]  (stock, zero, reorder, production, closing, pilferage, beams, sizers, ...) [--csv] [--push]
  python -m smart_layer audit [--date D | --month YYYY-MM] [--csv] [--push]
  python -m smart_layer ask ["question"]         ask the ERP (interactive without a question)
  python -m smart_layer serve                    owner dashboard + chat on http://<server>:8080
  python -m smart_layer dashboard [--date D]     save the dashboard as one HTML file (no chat)
  python -m smart_layer schema                   print the standard data model

Use -c to point at a config file (default config.toml).
"""

import argparse
import calendar
import sys
from datetime import date, timedelta

from . import PRODUCT, config, erp, notify, schema, store


def _open_erp(cfg):
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    con, style = erp.connect(cfg["erp"], cfg.base_dir)
    return erp.ERP(con, style, mapping, cfg["sources"], cfg.base_dir)


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
            src = e.mapping.source.get(ent.name, "erp")
            print(f"  {ent.name:<17} ok ({n:,} rows{' in the last 30 days' if ent.date_column else ''})"
                  + (f"  [source: {src}]" if src != "erp" else ""))
        except Exception as ex:                      # report every entity, not just the first failure
            ok = False
            print(f"  {ent.name:<17} FAILED: {ex}")
    return 0 if ok else 1


def cmd_sync(cfg, args):
    e = _open_erp(cfg)
    print(f"Syncing from {e.mapping.name} (last {cfg['sync']['history_days']} days; full history for yarn and fabric)")
    store.sync(_store(cfg), e, cfg["sync"]["history_days"], entities=args.entities,
               full_history=cfg["sync"].get("full_history", store.FULL_HISTORY))
    return 0


def cmd_report(cfg, args):
    from . import report_registry
    con = _store(cfg)
    day = date.fromisoformat(args.date) if args.date else None
    rep = report_registry.build(con, cfg, args.name, day)
    print(rep.text())
    tag = report_registry.report_day(args.name, day).isoformat()
    out_dir = cfg.path(cfg["reports"]["output_dir"])
    if args.csv:
        print("\nSaved", rep.write_csv(out_dir, f"{args.name}_{tag}.csv"))
    if args.pdf:
        print("Saved", rep.write_pdf(out_dir, f"{args.name}_{tag}.pdf", cfg["company"]["name"], PRODUCT))
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
    if args.pdf:
        print("Saved", rep.write_pdf(cfg.path(cfg["reports"]["output_dir"]), f"audit_{d0}_{d1}.pdf", cfg["company"]["name"], PRODUCT))
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


def cmd_serve(cfg, args):
    from .web.server import serve
    serve(cfg)
    return 0


def cmd_dashboard(cfg, args):
    from .web import static_page
    day = date.fromisoformat(args.date) if args.date else None
    out = cfg.path(args.out or (cfg.path(cfg["reports"]["output_dir"]) / "dashboard.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(static_page(_store(cfg), cfg, day), encoding="utf-8")
    print("Saved", out)
    return 0


def cmd_grants(cfg, args):
    """SQL for the mill's IT person: a read-only database user that can see only what the mapping reads."""
    import re
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    sql = "\n".join(mapping.entities.values())
    tables = sorted({m.lower() for m in re.findall(r"(?:FROM|JOIN)\s+([A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?)", sql, re.I)
                     if m.lower() not in ("dual",)})
    funcs = sorted({m.lower() for m in re.findall(r"\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\(", sql)
                    if m.split(".")[0].lower() not in {t.split(".")[-1] for t in tables} and "." in m})
    user, driver = args.user, cfg["erp"].get("driver", "oracle")
    print(f"-- Read-only user for {PRODUCT} ({mapping.name})")
    print("-- Run as a database administrator. Replace the password; put the same one in .env as ERP_PASSWORD.")
    if driver == "oracle":
        print(f'CREATE USER {user} IDENTIFIED BY "ChangeThisPassword";\nGRANT CREATE SESSION TO {user};')
        for t in tables:
            print(f"GRANT SELECT ON {t} TO {user};")
        for f in funcs:
            print(f"GRANT EXECUTE ON {f} TO {user};")
    elif driver == "mssql":
        print(f"CREATE LOGIN {user} WITH PASSWORD = 'ChangeThisPassword';\nCREATE USER {user} FOR LOGIN {user};")
        for t in tables:
            print(f"GRANT SELECT ON {t} TO {user};")
    elif driver == "mysql":
        print(f"CREATE USER '{user}'@'%' IDENTIFIED BY 'ChangeThisPassword';")
        for t in tables:
            print(f"GRANT SELECT ON {t if '.' in t else cfg['erp'].get('database', 'erp') + '.' + t} TO '{user}'@'%';")
    elif driver == "postgres":
        print(f"CREATE ROLE {user} LOGIN PASSWORD 'ChangeThisPassword';")
        for t in tables:
            print(f"GRANT SELECT ON {t} TO {user};")
    else:
        print("-- SQLite files have no users: give the service read access to the file only.")
    return 0


def cmd_licence(cfg, args):
    from . import licence
    if args.action == "install":
        if not args.value:
            print("Paste the licence code (one line), then press Enter:")
            args.value = input().strip()
        st = licence.install(cfg, args.value)
        print(("Installed. " if st.allowed else "Not installed: ") + st.message)
        return 0 if st.allowed else 1
    st = licence.status(cfg)
    if args.action == "check" and args.push and (st.warning or not st.allowed):
        notify.send(cfg, "Smart Layer licence", st.message, priority="high")
    print(f"{st.state}: {st.message}")
    print(f"This server's ID: {licence.machine_id()}")
    return 0 if st.allowed else 1


def cmd_status(cfg, args):
    import os
    import subprocess
    from . import licence
    print(f"{PRODUCT} - {cfg['company']['name']}")
    lic = licence.status(cfg)
    print(f"  Licence     : {lic.state} - {lic.message}")
    print(f"  Server ID   : {licence.machine_id()}")
    print(f"  ERP mapping : {cfg['erp'].get('mapping')}  ({cfg['erp'].get('driver')})")
    db = cfg.path(cfg["store"]["path"])
    if db.exists():
        con = store.open_store(db)
        rows = con.execute("SELECT entity, MAX(synced_at), rows FROM sync_log GROUP BY entity ORDER BY MIN(rowid)").fetchall()
        last = max((r[1] for r in rows), default=None)
        print(f"  Last sync   : {last or 'never'}  ({db.stat().st_size / 1e6:,.1f} MB)")
        for e, when, n in rows:
            print(f"      {e:<17} {n:>9,} rows")
    else:
        print("  Last sync   : never - run  python -m smart_layer sync")
    sv = cfg["serve"]
    print(f"  Dashboard   : http://{'<this server>' if sv.get('host') == '0.0.0.0' else sv.get('host')}:{sv.get('port')}"
          f"  password {'set' if sv.get('password') else 'NOT set'}")
    print(f"  Alerts      : {', '.join(cfg['notify'].get('channels', []))}")
    chat = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"  Chat        : {'ready' if chat else 'not set up (no ANTHROPIC_API_KEY in .env)'}")
    if os.name == "nt":
        for task in ("Smart Layer Daily", "Smart Layer Live", "Smart Layer Dashboard"):
            r = subprocess.run(["schtasks", "/query", "/tn", task, "/fo", "list"], capture_output=True, text=True)
            state = next((ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines() if ln.startswith("Status")), "not installed")
            print(f"  Task        : {task} - {state}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="smart_layer", description=PRODUCT)
    p.add_argument("-c", "--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("sync")
    s.add_argument("--entities", nargs="*")
    from . import report_registry
    r = sub.add_parser("report")
    r.add_argument("name", choices=report_registry.NAMES)
    r.add_argument("--date")
    r.add_argument("--csv", action="store_true")
    r.add_argument("--pdf", action="store_true")
    r.add_argument("--push", action="store_true")
    a = sub.add_parser("audit")
    a.add_argument("--date")
    a.add_argument("--month")
    a.add_argument("--csv", action="store_true")
    a.add_argument("--pdf", action="store_true")
    a.add_argument("--push", action="store_true")
    q = sub.add_parser("ask")
    q.add_argument("question", nargs="*")
    q.add_argument("--show-sql", action="store_true")
    sub.add_parser("serve")
    d = sub.add_parser("dashboard")
    d.add_argument("--date")
    d.add_argument("--out")
    sub.add_parser("schema")
    sub.add_parser("setup")
    sub.add_parser("machine-id")
    lc = sub.add_parser("licence")
    lc.add_argument("action", choices=["show", "install", "check"])
    lc.add_argument("value", nargs="?", help="licence file or code (install)")
    lc.add_argument("--push", action="store_true")
    sub.add_parser("status")
    g = sub.add_parser("grants")
    g.add_argument("--user", default="smartlayer_ro")
    args = p.parse_args(argv)
    if args.cmd == "schema":
        print(schema.describe())
        return 0
    if args.cmd == "machine-id":
        from .licence import machine_id
        print(machine_id())
        return 0
    if args.cmd == "setup":
        from pathlib import Path

        from .setup_wizard import run
        cfg_path = run(Path(args.config).resolve().parent)
        print("\nTesting the connection to the ERP ...")
        return cmd_check(config.load(cfg_path), args)
    cfg = config.load(args.config)
    if args.cmd in ("sync", "report", "audit", "ask"):   # the dashboard (serve) starts regardless, so the
        from .licence import require                      # licence can be installed from its own Licence screen
        require(cfg)
    return {"check": cmd_check, "sync": cmd_sync, "report": cmd_report, "audit": cmd_audit, "ask": cmd_ask, "serve": cmd_serve,
            "dashboard": cmd_dashboard,
            "status": cmd_status, "grants": cmd_grants, "licence": cmd_licence}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
