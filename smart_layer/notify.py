"""
Alerts to the owner's phone or screen. Channels are set in config [notify]:
  console  - print (default)
  ntfy     - push via ntfy (https://ntfy.sh or a self-hosted server).
             ntfy topics are readable by anyone who knows the name, so use a
             long random topic and, ideally, an access token (ntfy_token).

A push can carry a PDF (reports). ntfy takes a file as the request body (PUT) with the message in headers; the
owner gets the PDF on the phone, inside the mill network or out of it. If the upload fails or the file is over the
server's limit, we fall back to a text-only push naming the saved file - the alert is never lost.
"""

import sys
import urllib.request

NTFY_MAX_BYTES = 15 * 1024 * 1024        # ntfy.sh allows ~15 MB attachments; our PDFs are far under this

# Channels that actually reach the owner away from the PC. 'console' is only a local log line, so it must NOT count
# as delivery of a phone alert (item B1): if ntfy is configured and fails, the push is NOT delivered and the outbox
# must keep retrying, even though console "succeeded".
REMOTE_CHANNELS = {"ntfy"}


class NotifyError(Exception):
    """Raised when delivery to the owner failed (so the caller/outbox can retry). This means either no channel at all
    delivered, or a configured REMOTE channel (the phone push) failed - a local console print is not a delivery."""


def force_utf8_stdout():
    """Make stdout/stderr UTF-8 with errors='replace' (item Q). On Windows a redirected console is cp1252, so a '→'
    in an Edit message raised UnicodeEncodeError inside the console channel - and because console is the first
    channel, the exception aborted send() before ntfy ever ran, so 90 pushes were lost. Call once at startup."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # Python 3.7+ text streams
        except Exception:
            pass


def _headers(n, title, priority, click, tags=None):
    h = {"Title": title.encode("utf-8"), "Priority": priority}
    if click:
        h["Click"] = click
    if tags:
        h["Tags"] = tags
    if n.get("ntfy_token"):
        h["Authorization"] = f"Bearer {n['ntfy_token']}"
    return h


def _send_console(title, body, click, filename):
    # errors='replace' so a stray '→' can never crash the console channel on a cp1252 console (item Q)
    msg = (f"\n=== {title} ===\n{body}\n" + (f"(open: {click})\n" if click else "")
           + (f"(file: {filename})\n" if filename else ""))
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.write(msg.encode("utf-8", "replace").decode("utf-8", "replace") + "\n")


def _send_ntfy(n, title, body, priority, click, attach, filename, tags):
    topic = n.get("ntfy_topic", "")
    if len(topic) < 16:
        raise ValueError("ntfy_topic is missing or too short to be private - use a long random name")
    server = n.get("ntfy_server", "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    if attach and filename and len(attach) <= NTFY_MAX_BYTES:
        try:
            h = _headers(n, title, priority, click, tags)
            h["Filename"] = filename
            h["Message"] = body.encode("utf-8")               # with a body attachment the text goes in the Message header
            req = urllib.request.Request(url, data=attach, method="PUT", headers=h)
            urllib.request.urlopen(req, timeout=60).read()
            return
        except Exception:
            pass                                              # fall through to a text-only push, below
    text = body + (f"\n(Report saved on the server: {filename})" if filename else "")
    req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST",
                                 headers=_headers(n, title, priority, click, tags))
    urllib.request.urlopen(req, timeout=30).read()


def send(cfg, title, body, priority="default", click=None, attach=None, filename=None, tags=None):
    """Send an alert on every configured channel. Each channel is ISOLATED (item Q): one channel's failure - a cp1252
    console crash, or a DNS error on ntfy - can no longer stop the others. `attach` (bytes) + `filename` attaches a
    file to the ntfy push; on failure or an over-size file it falls back to a text-only push that names the file.
    Returns the channels that delivered. Raises NotifyError when the alert did not reach the owner - either no channel
    delivered at all, OR a configured remote channel (ntfy) failed while only the local console succeeded (item B1) -
    so the outbox keeps the push pending and retries rather than losing a phone alert."""
    n = cfg["notify"]
    channels = n.get("channels", ["console"])
    sent, failed = [], []
    for ch in channels:
        try:
            if ch == "console":
                _send_console(title, body, click, filename)
            elif ch == "ntfy":
                _send_ntfy(n, title, body, priority, click, attach, filename, tags)
            else:
                raise ValueError(f"unknown notify channel '{ch}'")
            sent.append(ch)
        except Exception as exc:
            failed.append((ch, str(exc).splitlines()[0][:200]))
    failed_names = {c for c, _ in failed}
    remote_failed = [c for c in channels if c in REMOTE_CHANNELS and c in failed_names]
    if remote_failed or (not sent and failed):       # a phone channel failed, or nothing delivered at all
        raise NotifyError("; ".join(f"{c}: {e}" for c, e in failed))
    return sent
