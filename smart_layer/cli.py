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
    if getattr(args, "deep", False):                 # also validate the VALUES in the local store, not just the SQL
        from . import selfcheck
        con = _store(cfg)
        print("\n" + selfcheck.summary(selfcheck.run(con, cfg)))
    return 0 if ok else 1


def cmd_autoprofile(cfg, args):
    """Read the ERP's own catalogue and identify it - the one-time install analysis (pure Python, offline)."""
    from . import autoprofile, erp as _erp
    con, _ = _erp.connect(cfg["erp"], cfg.base_dir)
    p = autoprofile.profile(con, cfg["erp"].get("driver", "sqlite"))
    print(f"ERP driver     : {p['driver']}")
    print(f"Tables / views : {p['tables']}")
    print(f"Columns        : {p['columns']}")
    if p["recognised"]:
        print(f"\nRecognised ERP : {p['label']}")
        print(f"  confidence   : {p['confidence']:.0%}  (signature tables found: {', '.join(p['signature_found'])})")
        print(f"  use mapping  : mappings/{p['mapping']}")
        print("\nThis ERP is known - its mapping applies, so reports work without hand-tuning.")
    else:
        print("\nUnknown ERP - no profile matches. Drafting best-guess column matches per entity;")
        print("fields with no confident match need a one-time confirm:\n")
        for ent in schema.ENTITIES:
            sug = autoprofile.suggest_columns(p["schema"], ent, top=1)
            weak = [c for c, cands in sug.items() if not cands or cands[0][2] < 0.5]
            best = {c: cands[0] for c, cands in sug.items() if cands and cands[0][2] >= 0.5}
            print(f"  {ent.name:<17} {len(best)}/{len(ent.columns)} matched"
                  + (f"  · confirm: {', '.join(weak)}" if weak else "  · all matched"))
    if getattr(args, "write_draft", None):
        from pathlib import Path
        text, drafted, skipped = autoprofile.draft_mapping(p["schema"])
        Path(args.write_draft).write_text(text, encoding="utf-8")
        print(f"\nDraft mapping written to {args.write_draft}")
        print(f"  {len(drafted)} entities drafted, {len(skipped)} with no candidate table.")
        print("  Edit the TODOs, add joins/filters and confirm business rules, then point config.toml at it.")
    return 0


def cmd_sync(cfg, args):
    e = _open_erp(cfg)
    if args.live:                                    # the 5-minute refresh: only the fast, window-safe entities
        days = int(cfg["sync"].get("live_days", 3))  # (LIVE_ENTITIES) - ledgers, attendance and month-end history
        ent = [x for x in args.entities if x in store.LIVE_ENTITIES] if args.entities else list(store.LIVE_ENTITIES)
        if args.entities and not ent:                # asked for entities, none are live-safe -> do nothing (never "all")
            print("Nothing to sync: none of the named data sets are refreshed by the live sync.")
            return 0
        print(f"Live sync from {e.mapping.name} (last {days} days: {', '.join(ent)})")  # rest are left to the daily sync
        return _sync_exit(store.sync(_store(cfg), e, days, entities=ent, full_history=(), fabric_history=False))
    print(f"Syncing from {e.mapping.name} (last {cfg['sync']['history_days']} days; full history for yarn and fabric)")
    res = store.sync(_store(cfg), e, cfg["sync"]["history_days"], entities=args.entities,
                     full_history=cfg["sync"].get("full_history", store.FULL_HISTORY))
    return _sync_exit(res)


def _sync_exit(res):
    """0 = everything synced, 2 = SOME data sets failed (the day's work still runs on the data we have), 1 = EVERY
    data set failed (treat as a hard failure - usually the ERP is unreachable or the login is wrong)."""
    failed = res.get("_failed") or []
    done = [k for k, v in res.items() if k != "_failed" and v is not None]
    if not failed:
        return 0
    return 1 if not done else 2


def cmd_report(cfg, args):
    from . import report_registry
    from .reports import stock
    con = _store(cfg)
    day = date.fromisoformat(args.date) if args.date else None
    if args.name == "zero":                              # only the daily push moves the "newly at zero" baseline
        rep = stock.new_zero(con, cfg, day, remember=bool(args.push))
    else:
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
    persist = bool(getattr(args, "push", False) and not getattr(args, "urgent", False))  # only the daily summary
    a = audit.run(_store(cfg), cfg, d0, d1, persist=persist)
    rep = a.report()
    print(rep.text())
    if args.csv:
        print("\nSaved", rep.write_csv(cfg.path(cfg["reports"]["output_dir"]), f"audit_{d0}_{d1}.csv"))
    if args.pdf:
        print("Saved", rep.write_pdf(cfg.path(cfg["reports"]["output_dir"]), f"audit_{d0}_{d1}.pdf", cfg["company"]["name"], PRODUCT))
    if args.push and args.urgent:                    # the 5-minute task: each urgent finding, once, with a tap-to-open link
        for f in a.new_alerts(urgent_only=True):
            notify.send(cfg, f"{f.section}: {f.title[:80]}", (f.title + ("\n" + f.detail if f.detail else "")),
                        priority="urgent", click=_dashboard_link(cfg, f))
    elif args.push:                                  # the daily summary: everything else new, batched
        fresh = a.new_alerts()
        if fresh:
            body = "\n".join(f"[{f.severity}] {f.title}" for f in fresh[:25])
            if len(fresh) > 25:
                body += f"\n... and {len(fresh) - 25} more in the report"
            notify.send(cfg, f"ERP audit {d1:%d-%b}: {len(fresh)} new", body, priority="high",
                        click=(cfg["notify"].get("dashboard_url") or cfg["serve"].get("url") or None))
    return 0


def _dashboard_link(cfg, f):
    """Dashboard URL that opens the record a finding is about (needs [notify] dashboard_url or [serve] url)."""
    import re
    base = (cfg["notify"].get("dashboard_url") or cfg["serve"].get("url") or "").rstrip("/")
    if not base:
        return None
    m = re.search(r"\b([A-Z][A-Z0-9]*-[A-Z0-9]+|LOOM[-\s]?\w+)\b", f.title)
    return base + ("/#q=" + m.group(1) if m else "/")


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


def _start_windows_tasks(cfg_path):
    """After setup (config.toml written), start the compiled product's scheduled tasks. Only for the compiled
    install, where install_tasks.ps1 sits next to config.toml; a no-op otherwise (e.g. run-from-source)."""
    import os
    if os.name != "nt":
        return
    import subprocess
    app_dir = cfg_path.parent
    script = app_dir / "install_tasks.ps1"
    if not script.exists():
        return
    try:
        subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-AppDir", str(app_dir), "-Start"], timeout=120)
        print("Started the dashboard and the live sync (scheduled tasks).")
    except Exception as e:
        print(f"(Could not start the scheduled tasks automatically: {e})")


def main(argv=None):
    p = argparse.ArgumentParser(prog="smart_layer", description=PRODUCT)
    p.add_argument("-c", "--config", default="config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check")
    ck.add_argument("--deep", action="store_true", help="also check the synced values look sane (balances, ranges, blanks)")
    s = sub.add_parser("sync")
    s.add_argument("--entities", nargs="*")
    s.add_argument("--live", action="store_true", help="quick refresh: all modules, last few days only (for the 5-minute task)")
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
    a.add_argument("--urgent", action="store_true", help="push only the urgent send-once alerts (for the 5-minute task)")
    q = sub.add_parser("ask")
    q.add_argument("question", nargs="*")
    q.add_argument("--show-sql", action="store_true")
    sub.add_parser("serve")
    d = sub.add_parser("dashboard")
    d.add_argument("--date")
    d.add_argument("--out")
    sub.add_parser("schema")
    apr = sub.add_parser("autoprofile")
    apr.add_argument("--write-draft", metavar="PATH", help="write a draft mapping .toml (for an ERP with no known profile)")
    sub.add_parser("setup")
    sub.add_parser("machine-id")
    lt = sub.add_parser("logtrim")
    lt.add_argument("path")
    lt.add_argument("--max-mb", type=float, default=5)
    lt.add_argument("--keep-mb", type=float, default=1)
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
    if args.cmd == "logtrim":                            # no config needed: logs exist before setup runs
        from .logs import trim_log
        trim_log(args.path, int(args.max_mb * 1024 * 1024), int(args.keep_mb * 1024 * 1024))
        return 0
    if args.cmd == "setup":
        from pathlib import Path

        from .setup_wizard import run
        cfg_path = run(Path(args.config).resolve().parent)
        cfg = config.load(cfg_path)
        print("\nAnalysing the ERP (reading its tables once) ...")
        try:
            from . import autoprofile, erp as _erp
            con, _ = _erp.connect(cfg["erp"], cfg.base_dir)
            pr = autoprofile.profile(con, cfg["erp"].get("driver", "sqlite"))
            if pr["recognised"]:
                print(f"  Recognised: {pr['label']} ({pr['confidence']:.0%}). Using mappings/{pr['mapping']} - no tuning needed.")
            else:
                print(f"  New ERP ({pr['tables']} tables). To start a mapping:  SmartLayer.exe autoprofile --write-draft mymill.toml")
        except Exception as ex:
            print(f"  (Auto-profile skipped: {ex})")
        print("\nTesting the connection to the ERP ...")
        rc = cmd_check(cfg, args)
        if rc == 0:                                      # ERP reachable: do the first sync now so the dashboard has data
            print("\nDoing the first sync from the ERP (this can take a few minutes) ...")
            try:
                from .licence import require
                require(cfg)
                cmd_sync(cfg, argparse.Namespace(live=False, entities=None))
                print("\nSetup complete. Open the dashboard:  http://localhost:8080")
            except SystemExit as e:                      # usually: licence not installed yet
                print(f"\nFirst sync not done yet: {e}")
                print("Open the dashboard (http://localhost:8080), install the licence, then run:  SmartLayer.exe sync")
            except Exception as e:
                print(f"\nFirst sync could not finish: {e}\nYou can run it later with:  SmartLayer.exe sync")
        _start_windows_tasks(cfg_path)                   # config.toml now exists - start the dashboard + live sync
        input("\nPress Enter to close this window.")      # keep the console open so the owner can read the result
        return rc
    cfg = config.load(args.config)
    if args.cmd in ("sync", "report", "audit", "ask"):   # the dashboard (serve) starts regardless, so the
        from .licence import require                      # licence can be installed from its own Licence screen
        require(cfg)
    return {"check": cmd_check, "sync": cmd_sync, "report": cmd_report, "audit": cmd_audit, "ask": cmd_ask, "serve": cmd_serve,
            "dashboard": cmd_dashboard, "autoprofile": cmd_autoprofile,
            "status": cmd_status, "grants": cmd_grants, "licence": cmd_licence}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
