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

import urllib.request

NTFY_MAX_BYTES = 15 * 1024 * 1024        # ntfy.sh allows ~15 MB attachments; our PDFs are far under this


def _headers(n, title, priority, click, tags=None):
    h = {"Title": title.encode("utf-8"), "Priority": priority}
    if click:
        h["Click"] = click
    if tags:
        h["Tags"] = tags
    if n.get("ntfy_token"):
        h["Authorization"] = f"Bearer {n['ntfy_token']}"
    return h


def send(cfg, title, body, priority="default", click=None, attach=None, filename=None, tags=None):
    """Send an alert on every configured channel. `attach` (bytes) + `filename` attaches a file to the ntfy push;
    on failure or an over-size file it falls back to a text-only push that names the file. Returns the channels sent."""
    n = cfg["notify"]
    sent = []
    for ch in n.get("channels", ["console"]):
        if ch == "console":
            print(f"\n=== {title} ===\n{body}\n" + (f"(open: {click})\n" if click else "")
                  + (f"(file: {filename})\n" if filename else ""))
            sent.append(ch)
        elif ch == "ntfy":
            topic = n.get("ntfy_topic", "")
            if len(topic) < 16:
                raise ValueError("ntfy_topic is missing or too short to be private - use a long random name")
            server = n.get("ntfy_server", "https://ntfy.sh").rstrip("/")
            url = f"{server}/{topic}"
            if attach and filename and len(attach) <= NTFY_MAX_BYTES:
                try:
                    h = _headers(n, title, priority, click, tags)
                    h["Filename"] = filename
                    h["Message"] = body.encode("utf-8")       # with a body attachment the text goes in the Message header
                    req = urllib.request.Request(url, data=attach, method="PUT", headers=h)
                    urllib.request.urlopen(req, timeout=60).read()
                    sent.append(ch)
                    continue
                except Exception:
                    pass                                      # fall through to a text-only push, below
            text = body + (f"\n(Report saved on the server: {filename})" if filename else "")
            req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST",
                                         headers=_headers(n, title, priority, click, tags))
            urllib.request.urlopen(req, timeout=30).read()
            sent.append(ch)
        else:
            raise ValueError(f"unknown notify channel '{ch}'")
    return sent
