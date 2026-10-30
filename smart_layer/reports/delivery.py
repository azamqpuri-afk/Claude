"""
Report delivery - local only, no cloud API (new mills have no Google Drive):

  save      write a report as a date-named PDF (and optional CSV) into [reports] output_dir
  prune     delete saved report files older than [reports] retention_weeks
  deliver   save + prune + push an ntfy alert, attaching the PDF so the owner gets it on the phone anywhere
  listing   the saved files for the dashboard's Reports page (newest first)

output_dir defaults to a writable per-machine folder (Windows: C:\\ProgramData\\Smart Layer\\output). Point it at a
synced OneDrive / Google-Drive-for-desktop folder to get the files in the cloud - Smart Layer stays API-free.
"""

import re
import time
from datetime import date, datetime
from pathlib import Path

from .. import PRODUCT, notify

SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(name):
    return SAFE.sub("-", str(name)).strip("-") or "report"


def save(cfg, report, name, tag=None, csv=False):
    """Write the report as <name>_<tag>.pdf into output_dir and return the path. tag defaults to today's date."""
    tag = tag or date.today().isoformat()
    out = cfg.output_dir()
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{_safe(name)}_{_safe(tag)}"
    pdf = report.write_pdf(out, f"{stem}.pdf", cfg["company"]["name"], PRODUCT)
    if csv:
        report.write_csv(out, f"{stem}.csv")
    return Path(pdf)


def prune(cfg):
    """Delete saved report files older than retention_weeks (0 = keep everything). Only touches our own output
    files (*.pdf / *.csv) inside output_dir, never anything else."""
    weeks = int(cfg["reports"].get("retention_weeks", 26) or 0)
    out = cfg.output_dir()
    if weeks <= 0 or not out.exists():
        return 0
    cutoff = time.time() - weeks * 7 * 86400
    removed = 0
    for p in out.iterdir():
        if p.is_file() and p.suffix.lower() in (".pdf", ".csv") and p.stat().st_mtime < cutoff:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def attach_enabled(cfg, name):
    """Whether this report's push carries the PDF. [reports] attach_pdf is the default (on); attach_pdf_by_report
    can override per report name, e.g. [reports.attach_pdf_by_report]  weekly-beams = true, demand = false."""
    r = cfg["reports"]
    per = r.get("attach_pdf_by_report") or {}
    if name in per:
        return bool(per[name])
    return bool(r.get("attach_pdf", True))


def deliver(cfg, report, name, tag=None, push=True, csv=False, click=None, tags="page_facing_up"):
    """Save the PDF, prune old files, and (push=True) send an ntfy alert with the PDF attached. Returns the path."""
    pdf = save(cfg, report, name, tag, csv=csv)
    prune(cfg)
    if push:
        body = "\n".join(report.summary) if report.summary else report.title
        attach = pdf.read_bytes() if attach_enabled(cfg, name) else None
        notify.send(cfg, report.title, body, priority="default", click=click,
                    attach=attach, filename=pdf.name if attach else pdf.name, tags=tags)
    return pdf


def listing(cfg, limit=200):
    """Saved report files for the dashboard Reports page, newest first: name, size, when."""
    out = cfg.output_dir()
    if not out.exists():
        return []
    files = []
    for p in sorted(out.iterdir(), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
        if p.is_file() and p.suffix.lower() in (".pdf", ".csv"):
            st = p.stat()
            files.append({"name": p.name, "size": st.st_size,
                          "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                          "kind": p.suffix.lower().lstrip(".")})
        if len(files) >= limit:
            break
    return files


def read_file(cfg, name):
    """One saved file's bytes for download, or None. Serves ONLY a plain file inside output_dir - no path traversal
    (the name may not contain a separator or '..'), so the Reports page can never reach outside the folder."""
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    out = cfg.output_dir()
    p = (out / name)
    try:
        if p.is_file() and p.resolve().parent == out.resolve() and p.suffix.lower() in (".pdf", ".csv"):
            return p.read_bytes()
    except OSError:
        pass
    return None
