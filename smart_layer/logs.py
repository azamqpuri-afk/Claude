"""
Lock-safe log trimming for the scheduled-task and dashboard log files.

The old approach (read the file and write it back in one pipeline) could fail on
Windows because the read handle was still open when the write began, and the
compiled-product .cmd scripts had no cap at all, so logs grew without bound.

trim_log keeps the tail of a file by writing a fresh temp file and renaming it
over the original (atomic, no same-handle lock). If the file is held open by
another process (e.g. the dashboard is writing to it), it skips quietly instead
of crashing the run - the next run, when the file is free, trims it.
"""

import os
from pathlib import Path

MARKER = b"... [earlier log trimmed] ...\n"


def trim_log(path, max_bytes=5 * 1024 * 1024, keep_bytes=1 * 1024 * 1024):
    """Trim `path` to roughly its last keep_bytes when it grows past max_bytes. Returns True if it trimmed,
    False if nothing to do or the file was busy. Never raises on a locked / missing file."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False
    keep = min(keep_bytes, max_bytes)
    try:
        with p.open("rb") as f:
            f.seek(max(0, size - keep))
            tail = f.read()
    except OSError:
        return False
    nl = tail.find(b"\n")                      # drop a partial first line so the tail starts clean
    if 0 <= nl < len(tail) - 1:
        tail = tail[nl + 1:]
    tmp = p.with_suffix(p.suffix + ".trim.tmp")
    try:
        with tmp.open("wb") as f:
            f.write(MARKER)
            f.write(tail)
        os.replace(tmp, p)                     # atomic; fails cleanly if the original is locked (Windows)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
