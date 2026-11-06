"""
Command line for Project Smart Layer by Azam Puri.

  python -m smart_layer setup                    first-time setup: asks questions, writes config.toml and .env
  python -m smart_layer status                   health of this installation (last sync, dashboard, tasks)
  python -m smart_layer grants [--user NAME] [--missing]  SQL for the read-only DB user (--missing: only tables not yet granted)
  python -m smart_layer set-password [--var ERP_PASSWORD]  re-enter the ERP password (hidden) and seal it into .env (DPAPI)
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
import os
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
            # edit-history entities are read by a numeric event_id cursor, not a date window - probe from 0
            probe = 0 if ent.name in store.CURSOR_ENTITIES else since
            n = sum(1 for _ in e.fetch(ent.name, probe))
            src = e.mapping.source.get(ent.name, "erp")
            windowed = ent.date_column and ":since" in e.mapping.entities[ent.name]   # only claim a window the SQL applies
            print(f"  {ent.name:<17} ok ({n:,} rows{' in the last 30 days' if windowed else ''})"
                  + (f"  [source: {src}]" if src != "erp" else ""))
        except Exception as ex:                      # report every entity, not just the first failure
            # an optional edit-history entity whose source tables do not exist yet is "not installed", not a failure
            # (the mill has not installed the ERP change-history layer) - it must not make `check` report an error.
            msg = str(ex)
            if ent.name in store.CURSOR_ENTITIES and any(
                    s in msg for s in ("ORA-00942", "no such table", "does not exist", "Invalid object name",
                                       "doesn't exist", "Unknown table", "relation", "not found")):
                print(f"  {ent.name:<17} not installed (ERP change-history layer not present)")
                continue
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
    data_dir = cfg.path(cfg["store"]["path"]).parent
    with store.sync_lock(data_dir) as got:
        if not got:
            print("A sync is already running (dashboard or another task) - skipping this one.")
            return 0
        return _cmd_sync(cfg, args)


def _cmd_sync(cfg, args):
    e = _open_erp(cfg)
    short = {"loom_stops": int(cfg["sync"].get("loom_stops_days", 14))}   # cap the huge raw stop log
    if args.live:                                    # the 5-minute refresh: only the fast, window-safe entities
        days = int(cfg["sync"].get("live_days", 3))  # (LIVE_ENTITIES) - ledgers, attendance and month-end history
        ent = [x for x in args.entities if x in store.LIVE_ENTITIES] if args.entities else list(store.LIVE_ENTITIES)
        if args.entities and not ent:                # asked for entities, none are live-safe -> do nothing (never "all")
            print("Nothing to sync: none of the named data sets are refreshed by the live sync.")
            return 0
        print(f"Live sync from {e.mapping.name} (last {days} days: {', '.join(ent)})")  # rest are left to the daily sync
        return _sync_exit(store.sync(_store(cfg), e, days, entities=ent, full_history=(), fabric_history=False,
                                     short_history=short, track_changes=True))
    print(f"Syncing from {e.mapping.name} (last {cfg['sync']['history_days']} days; full history for yarn and fabric)")
    res = store.sync(_store(cfg), e, cfg["sync"]["history_days"], entities=args.entities,
                     full_history=cfg["sync"].get("full_history", store.FULL_HISTORY), short_history=short)
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
    d0 = date.fromisoformat(args.from_) if getattr(args, "from_", None) else None
    d1 = date.fromisoformat(args.to) if getattr(args, "to", None) else None
    if args.name == "zero":                              # only the daily push moves the "newly at zero" baseline
        rep = stock.new_zero(con, cfg, day, remember=bool(args.push))
    else:
        rep = report_registry.build(con, cfg, args.name, day, d0, d1)
    print(rep.text())
    tag = report_registry.report_day(args.name, d1 or day).isoformat()
    from .reports import delivery
    out_dir = cfg.output_dir()
    pdf_path = None
    if args.csv:
        print("\nSaved", rep.write_csv(out_dir, f"{args.name}_{tag}.csv"))
    if getattr(args, "xlsx", False):
        print("Saved", rep.write_xlsx(out_dir, f"{args.name}_{tag}.xlsx", cfg["company"]["name"]))
    if args.pdf:
        pdf_path = delivery.save(cfg, rep, args.name, tag)
        delivery.prune(cfg)
        print("Saved", pdf_path)
    if args.push and (rep.rows or args.name != "zero"):
        body = "\n".join(rep.summary) + ("\n\n" + rep.text(limit=15) if args.name == "zero" else "")
        attach = pdf_path.read_bytes() if (pdf_path and delivery.attach_enabled(cfg, args.name)) else None
        notify.send(cfg, rep.title, body, attach=attach, filename=(pdf_path.name if attach else None))
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
        print("\nSaved", rep.write_csv(cfg.output_dir(), f"audit_{d0}_{d1}.csv"))
    if args.pdf:
        print("Saved", rep.write_pdf(cfg.output_dir(), f"audit_{d0}_{d1}.pdf", cfg["company"]["name"], PRODUCT))
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


def _push_day(args):
    return date.fromisoformat(args.date) if getattr(args, "date", None) else None


def cmd_notify_changes(cfg, args):
    from . import pushes
    con = _store(cfg)
    try:
        n = pushes.notify_changes(cfg, con) + pushes.edit_pushes(cfg, con)
        print(f"{n} push(es)")
    finally:
        con.close()
    return 0


def cmd_morning(cfg, args):
    from . import pushes
    con = _store(cfg)
    try:
        pushes.morning_summary(cfg, con, _push_day(args))
    finally:
        con.close()
    return 0


def cmd_night_sensor(cfg, args):
    from . import pushes
    con = _store(cfg)
    try:
        pushes.night_sensor(cfg, con, _push_day(args))
    finally:
        con.close()
    return 0


def cmd_weft_check(cfg, args):
    from . import pushes
    con = _store(cfg)
    try:
        pushes.weft_check(cfg, con, _push_day(args))
    finally:
        con.close()
    return 0


def cmd_dashboard(cfg, args):
    from .web import static_page
    day = date.fromisoformat(args.date) if args.date else None
    out = cfg.path(args.out or (cfg.output_dir() / "dashboard.html"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(static_page(_store(cfg), cfg, day), encoding="utf-8")
    print("Saved", out)
    return 0


def _unreadable_tables(cfg, tables):
    """Of the schema-qualified tables the mapping reads, the ones the configured ERP user CANNOT currently SELECT
    (missing grant, or the table not created yet). Probes each with a 0-row SELECT through the ERP connection."""
    unreadable = []
    try:
        con, _style = erp.connect(cfg["erp"], cfg.base_dir)
    except Exception as ex:
        print(f"-- Could not connect to the ERP to check grants ({ex}); listing all mapped tables instead.")
        return list(tables)
    try:
        cur = con.cursor()
        for t in tables:
            try:
                cur.execute(f"SELECT * FROM {t} WHERE 1=0")   # validates access without reading any rows
                cur.fetchall()
            except Exception:
                unreadable.append(t)
    finally:
        try:
            con.close()
        except Exception:
            pass
    return unreadable


def cmd_grants(cfg, args):
    """SQL for the mill's IT person: a read-only database user that can see only what the mapping reads."""
    import re
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    # ONLY the entities that read the main ERP connection - not a second source (e.g. the MS Access loom
    # scoreboard: LOOMSTAT / RTLSUM), whose tables are not in this database and would fail with ORA-00942.
    erp_sql = "\n".join(s for name, s in mapping.entities.items() if mapping.source.get(name, "erp") == "erp")
    found = {m.lower() for m in re.findall(r"(?:FROM|JOIN)\s+([A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)?)", erp_sql, re.I)
             if m.lower() != "dual"}
    tables = sorted(t for t in found if "." in t)          # schema-qualified objects only (grantable with certainty)
    unqualified = sorted(t for t in found if "." not in t)  # CTE/derived aliases or same-schema objects - note, don't grant
    user, driver = args.user, cfg["erp"].get("driver", "oracle")
    # A <prefix>.<name>( is a package function only when <prefix> is a real SCHEMA (it owns a table in a FROM/JOIN,
    # e.g. dostex.get_item_stock). When the prefix is a table alias it is a column reference (e.g. emp.sec_code) and
    # must NOT become a GRANT EXECUTE line.
    schemas = {t.split(".")[0].lower() for t in tables}
    funcs = sorted({m.lower() for m in re.findall(r"\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\s*\(", erp_sql)
                    if "." in m and m.split(".")[0].lower() in schemas})
    if getattr(args, "missing", False):                    # print ONLY the GRANT SELECT lines the user still needs
        missing = _unreadable_tables(cfg, tables)
        if not missing:
            print(f"-- All {len(tables)} mapped table(s) are already readable by {user} - no grants needed.")
            return 0
        print(f"-- {len(missing)} of {len(tables)} mapped table(s) are NOT yet readable by {user} - grant these:")
        q = "'%s'@'%%'" % user if driver == "mysql" else user
        for t in missing:
            print(f"GRANT SELECT ON {t} TO {q};")
        return 0
    print(f"-- Read-only user for {PRODUCT} ({mapping.name})")
    print("-- Run as a database administrator. Replace the password; put the same one in .env as ERP_PASSWORD.")
    print("-- Grants are generated only for the main-ERP entities (a second source such as an MS Access loom")
    print("-- scoreboard is read directly from its own file, not through this database user).")
    if unqualified:
        print(f"-- NOTE: these objects are not schema-qualified in the mapping - qualify them with the owning schema")
        print(f"--       if they are real tables (ignore CTE / subquery aliases): {', '.join(unqualified)}")
    if driver == "oracle" and any(t.split(".")[0] != "dostex" and "." in t for t in tables):
        print("-- NOTE: where a DOSTEX view reads another schema's tables (e.g. PAYROLL), either grant SELECT on the")
        print("--       base tables below, or have that schema grant them to DOSTEX WITH GRANT OPTION, or selecting")
        print("--       the view as this read-only user fails.")
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


def cmd_set_password(cfg, args):
    """Ask for the ERP password with hidden typing and seal it into .env with DPAPI (machine-bound on Windows), so
    the owner can re-seal it himself after it was entered in plain text. Off Windows it is written as-is (demo/dev).
    Only the one variable line is touched; the rest of .env (comments, other keys) is preserved."""
    import getpass
    from . import secret
    var = (getattr(args, "var", None) or "ERP_PASSWORD").strip()
    base = (cfg.source_path.parent if cfg.source_path else cfg.base_dir)
    env_path = base / ".env"
    pw = getpass.getpass(f"Enter the ERP password for {var} (typing is hidden): ")
    if not pw:
        print("No password entered - nothing changed.")
        return 1
    if getpass.getpass("Re-enter to confirm: ") != pw:
        print("The two entries did not match - nothing changed.")
        return 1
    sealed = secret.seal_secret(pw)
    lines = []
    if env_path.exists():
        from .config import read_text_any
        lines = read_text_any(env_path).splitlines()
    out, replaced = [], False
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s and s.split("=", 1)[0].strip() == var:
            out.append(f"{var}={sealed}")
            replaced = True
        else:
            out.append(ln)
    if not replaced:
        out.append(f"{var}={sealed}")
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    where = "sealed with DPAPI (machine-bound)" if (os.name == "nt" and sealed.startswith("DPAPI:")) else "written (plain - not on Windows)"
    print(f"{var} {where} in {env_path}.")
    if os.name == "nt":
        print("The scheduled tasks and the dashboard will use it on their next run.")
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
    r.add_argument("--from", dest="from_", metavar="YYYY-MM-DD", help="range start (for period reports)")
    r.add_argument("--to", metavar="YYYY-MM-DD", help="range end / as-of date")
    r.add_argument("--csv", action="store_true")
    r.add_argument("--pdf", action="store_true")
    r.add_argument("--xlsx", action="store_true", help="also save an Excel workbook (live formulas where the report has them)")
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
    sub.add_parser("notify-changes")                     # live-sync pushes: new production + OGP fabric
    mg = sub.add_parser("morning")
    mg.add_argument("--date")
    ns = sub.add_parser("night-sensor")
    ns.add_argument("--date")
    wc = sub.add_parser("weft-check")
    wc.add_argument("--date")
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
    g.add_argument("--missing", action="store_true", help="print only GRANT lines for tables the ERP user cannot yet read")
    spw = sub.add_parser("set-password")
    spw.add_argument("--var", default="ERP_PASSWORD", help="the .env variable to set (default ERP_PASSWORD)")
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
    if args.cmd in ("sync", "report", "audit", "ask", "notify-changes", "morning", "night-sensor", "weft-check"):
        from .licence import require                      # licence can be installed from its own Licence screen
        require(cfg)
    return {"check": cmd_check, "sync": cmd_sync, "report": cmd_report, "audit": cmd_audit, "ask": cmd_ask, "serve": cmd_serve,
            "dashboard": cmd_dashboard, "autoprofile": cmd_autoprofile, "notify-changes": cmd_notify_changes,
            "morning": cmd_morning, "night-sensor": cmd_night_sensor, "weft-check": cmd_weft_check,
            "status": cmd_status, "grants": cmd_grants, "licence": cmd_licence,
            "set-password": cmd_set_password}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
