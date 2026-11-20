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

from . import PRODUCT, __version__, config, erp, notify, schema, store


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
    for w in _config_warnings(cfg):
        print(f"  WARNING: {w}")
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
    _nightly_backup(cfg)                               # item B5: a consistent copy once a day, after the daily sync
    return _sync_exit(res)


def _nightly_backup(cfg):
    """Item B5: keep a dated, power-loss-safe copy of the store after each daily sync. Off by default; the mill turns
    it on with [reports] backup = true. Never blocks the day's work (store.backup swallows its own errors)."""
    rep = cfg.get("reports", {}) if hasattr(cfg, "get") else cfg["reports"]
    if not rep.get("backup", False):
        return
    store_path = cfg.path(cfg["store"]["path"])
    bdir = rep.get("backup_dir")
    bdir = cfg.path(bdir) if bdir else store_path.parent / "backups"
    dest = store.backup(store_path, bdir, keep=int(rep.get("backup_keep", 14)))
    print(f"Backup written to {dest}" if dest else "Backup could not be made (the daily sync still succeeded).")


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
    if args.name == "contract":                          # the closing report (own module, not a day report)
        from .textile import contract_report as cr
        contract = getattr(args, "contract", None)
        quality = getattr(args, "quality", None)
        party = getattr(args, "party", None)
        if quality:                                      # by quality: all of one party's contracts of that quality
            if not party:
                print("For a by-quality closing give the party too: report contract --party <code> --quality <q>")
                return 2
            rep = cr.quality_report(con, cfg, party, quality)
            contract = f"{party}_{quality}"
        elif contract:
            rep = cr.contract_report(con, cfg, contract)
        else:
            print("Give a contract (report contract --contract <no>) or a party + quality "
                  "(report contract --party <code> --quality <q>)")
            return 2
        if not rep.get("found"):
            print("Nothing found for that selection.")
            return 1
        h = rep["header"]
        print(f"Contract {h.get('contract')} - {h.get('party')} - {h.get('quality') or ''}")
        for s in rep["sections"]:
            print(f"\n== {s['title']} ==")
            if s["rows"]:
                print("  " + " | ".join(str(c) for c in s["columns"]))
                for row in s["rows"][:200]:
                    print("  " + " | ".join(str(v) for v in row))
                if s.get("totals"):
                    print("  " + " | ".join(str(v) for v in s["totals"]))
            else:
                print("  (none)")
            if s.get("note"):
                print("  ! " + s["note"])
        out_dir = cfg.output_dir()
        import re as _re
        base = "contract_" + _re.sub(r"[^A-Za-z0-9_.-]+", "_", str(contract))[:50]
        if getattr(args, "xlsx", False):
            print("\nSaved", cr.write_xlsx(con, cfg, None, out_dir, f"{base}.xlsx", rep=rep))
        if args.pdf:
            print("Saved", cr.write_pdf(con, cfg, None, out_dir, f"{base}.pdf", product="Smart Layer", rep=rep))
        return 0
    if args.name == "zero":                              # the baseline moves only AFTER the alert is queued (item B1)
        rep = stock.new_zero(con, cfg, day, remember=False)
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
    if args.name == "zero" and args.push:                # route through the durable outbox, THEN move the baseline (B1)
        from . import outbox
        if rep.rows:
            body = "\n".join(rep.summary) + "\n\n" + rep.text(limit=15)
            outbox.queue(con, f"zero-{tag}", rep.title, body)
            outbox.flush(con, cfg)
        stock.commit_zero_baseline(con, cfg, day)        # the alert is now safely queued -> safe to advance the baseline
    elif args.push and (rep.rows or args.name != "zero"):
        body = "\n".join(rep.summary)
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
        from . import outbox
        for f in a.new_alerts(remember=False, urgent_only=True):   # mark pushed only AFTER delivery (item B1)
            outbox.queue(a.con, f"urgent-{f.key}", f"{f.section}: {f.title[:80]}",
                         (f.title + ("\n" + f.detail if f.detail else "")), priority="urgent",
                         click=_dashboard_link(cfg, f), meta={"keys": [f.key]})
        res = outbox.flush(a.con, cfg)
        a.mark_pushed([k for m in res["sent"] for k in m.get("keys", [])])
    elif args.push:                                  # the daily summary: everything else new, batched
        from . import outbox
        fresh = a.new_alerts(remember=False)
        if fresh:
            keys = [f.key for f in fresh]
            body = "\n".join(f"[{f.severity}] {f.title}" for f in fresh[:25])
            if len(fresh) > 25:
                body += f"\n... and {len(fresh) - 25} more in the report"
            outbox.queue(a.con, f"audit-{d1}-{min(keys)}-{len(keys)}", f"ERP audit {d1:%d-%b}: {len(fresh)} new", body,
                         priority="high", meta={"keys": keys},
                         click=(cfg["notify"].get("dashboard_url") or cfg["serve"].get("url") or None))
        res = outbox.flush(a.con, cfg)
        a.mark_pushed([k for m in res["sent"] for k in m.get("keys", [])])
    return 0


# Item B4: which reports each weekly/monthly slot runs. Kept here (not in run_daily.cmd) so a missed Friday/1st/10th
# catches up automatically. The plain reports are saved only; the push flag below marks the ones the owner gets pushed.
_SLOT_REPORTS = {
    "weekly": [("stock", False), ("reorder", False), ("demand", False), ("sized-closing", False), ("pilferage", False),
               ("sizers", False), ("yarn-stock", False), ("fabric-stock", False), ("weekly-beams", True)],
    "monthly-1": [("pnl", False), ("balances", False), ("parties", False), ("store-recon", False)],
}


def _report_ns(name, push):
    return argparse.Namespace(name=name, date=None, from_=None, to=None, csv=True, pdf=True, xlsx=False,
                              push=push, contract=None, quality=None, party=None)


def _run_slot(cfg, slot, push):
    """Run one scheduled slot's reports. One report failing never stops the rest (nor the catch-up marker)."""
    if slot in _SLOT_REPORTS:
        for name, pushed in _SLOT_REPORTS[slot]:
            try:
                cmd_report(cfg, _report_ns(name, push and pushed))
            except Exception as ex:                       # a single bad report must not sink the whole weekly run
                print(f"  ! {name} report failed: {ex}")
    elif slot == "monthly-10":                            # the previous-month ERP audit
        prev = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        try:
            cmd_audit(cfg, argparse.Namespace(month=prev, date=None, csv=True, pdf=True, push=push, urgent=False))
        except Exception as ex:
            print(f"  ! previous-month audit failed: {ex}")


def cmd_run_scheduled(cfg, args):
    """Run every weekly/monthly job that is due today, including any whose trigger day was missed while the PC was off
    (item B4). Idempotent: a job that already ran this period is skipped."""
    from . import jobs
    con = _store(cfg)
    today = date.fromisoformat(args.date) if getattr(args, "date", None) else date.today()
    slots = jobs.due(con, today)
    if not slots:
        print("No weekly/monthly jobs are due today.")
        return 0
    for slot in slots:
        trig = jobs.trigger_date(slot, today)
        extra = f" - CATCH-UP for the one due {trig:%d-%b}" if trig < today else ""
        print(f"Running {jobs.SLOTS[slot]}{extra}")
        _run_slot(cfg, slot, bool(getattr(args, "push", False)))
        jobs.mark(con, slot, today)                        # mark done so it will not run again until its next due date
    return 0


def cmd_install_mapping(cfg, args):
    """Item S: install an updated ERP mapping package. Backs up the current mapping (both the plain .toml and the
    sealed .map for this mill), then puts the new file in its place, so an update never loses the working mapping and
    can be rolled back from mappings_backup/. Accepts a .map (what a mill is sent) or a .toml (in-house)."""
    import datetime
    import shutil
    from pathlib import Path
    src = Path(args.file)
    if not src.exists():
        print(f"No such mapping file: {src}")
        return 1
    if src.suffix not in (".toml", ".map"):
        print("The mapping file must be a .toml or a sealed .map")
        return 2
    target = cfg.path(cfg["erp"]["mapping"])
    folder, stem = target.parent, target.stem
    old_ver = _mapping_version_of(target)
    bdir = folder / "mappings_backup"
    bdir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backed = []
    for ext in (".toml", ".map"):                          # back up, then clear, BOTH forms so only the new one is used
        cur = folder / (stem + ext)
        if cur.exists():
            dest = bdir / f"{stem}-{old_ver or 'prev'}-{ts}{ext}"
            shutil.copy2(cur, dest)
            cur.unlink()
            backed.append(dest.name)
    shutil.copy2(src, folder / (stem + src.suffix))        # the new mapping takes the mill's configured name
    new_ver = _mapping_version_of(folder / (stem + src.suffix))
    print(f"Installed mapping '{stem}{src.suffix}'  (was version {old_ver or '?'}, now {new_ver or '?'}).")
    if backed:
        print(f"Previous mapping backed up to mappings_backup/ ({', '.join(backed)}).")
    w = _mapping_age_warning(cfg)
    if w:
        print("WARNING: " + w)
    print("Run  SmartLayer.exe check  to confirm every entity still reads from the ERP.")
    return 0


def _mapping_version_of(path):
    try:
        m = erp.Mapping(path)
        return m.version or (m.app_version and f"app {m.app_version}") or ""
    except Exception:
        return ""


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
        pushes.heartbeat(cfg, con)                        # item B3: data-gap push + external dead-man's-switch ping
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


def _config_warnings(cfg):
    """Plain-language warnings about .env / config gaps that otherwise only show up deep inside the dashboard loop:
    a missing dashboard password (the dashboard refuses to serve on a network host) and a missing/short ntfy topic
    when push alerts are on (pushes silently fail). Returns a list of one-line strings (empty when all is well)."""
    out = []
    sv = cfg["serve"]
    host = str(sv.get("host") or "127.0.0.1")
    if not str(sv.get("password") or ""):
        where = "network" if host not in ("127.0.0.1", "localhost") else "local-only"
        out.append(f"DASHBOARD_PASSWORD missing in .env - the dashboard has no password ({where} host '{host}'); "
                   f"it will refuse to serve on a network host. Set DASHBOARD_PASSWORD in .env (or [serve] password).")
    n = cfg["notify"]
    if "ntfy" in (n.get("channels") or []) and len(str(n.get("ntfy_topic") or "")) < 16:
        out.append("NTFY_TOPIC missing (or too short) in .env - push alerts are enabled but will fail, so pushes are "
                   "disabled. Set NTFY_TOPIC in .env to a long random name (or remove 'ntfy' from [notify] channels).")
    mw = _mapping_age_warning(cfg)                        # item S: mill mapping older than the app
    if mw:
        out.append(mw)
    return out


def _mapping_age_warning(cfg):
    """Item S: warn when the mill's ERP mapping was built for an older Smart Layer release than the one running."""
    try:
        m = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
        return m.age_warning(__version__)
    except Exception:                                     # never let a mapping-load hiccup hide the real warnings
        return None


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
        for task in ("Smart Layer Daily", "Smart Layer Morning", "Smart Layer Live", "Smart Layer Dashboard"):
            r = subprocess.run(["schtasks", "/query", "/tn", task, "/fo", "list"], capture_output=True, text=True)
            state = next((ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines() if ln.startswith("Status")), "not installed")
            print(f"  Task        : {task} - {state}")
    for w in _config_warnings(cfg):
        print(f"  WARNING     : {w}")
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
    notify.force_utf8_stdout()                            # item Q: a redirected cp1252 console must not crash a push
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
    r.add_argument("name", choices=report_registry.NAMES + ["contract"])
    r.add_argument("--contract", help="for 'report contract': the contract number")
    r.add_argument("--party", help="for 'report contract' by quality: the party code")
    r.add_argument("--quality", help="for 'report contract' by quality: combine the party's contracts of this quality")
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
    rs = sub.add_parser("run-scheduled")                 # item B4: weekly/monthly jobs with catch-up
    rs.add_argument("--date", help="treat this as 'today' (for testing / a manual catch-up)")
    rs.add_argument("--push", action="store_true")
    im = sub.add_parser("install-mapping")               # item S: back up + replace the mill's ERP mapping
    im.add_argument("file", help="the new mapping package (.map that the mill was sent, or a .toml)")
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
    if args.cmd in ("sync", "report", "audit", "ask", "notify-changes", "morning", "night-sensor", "weft-check",
                    "run-scheduled"):
        from .licence import require                      # licence can be installed from its own Licence screen
        require(cfg)
    return {"check": cmd_check, "sync": cmd_sync, "report": cmd_report, "audit": cmd_audit, "ask": cmd_ask, "serve": cmd_serve,
            "dashboard": cmd_dashboard, "autoprofile": cmd_autoprofile, "notify-changes": cmd_notify_changes,
            "morning": cmd_morning, "night-sensor": cmd_night_sensor, "weft-check": cmd_weft_check,
            "run-scheduled": cmd_run_scheduled,
            "status": cmd_status, "grants": cmd_grants, "licence": cmd_licence,
            "install-mapping": cmd_install_mapping,
            "set-password": cmd_set_password}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
