"""
Alerts to the owner's phone or screen. Channels are set in config [notify]:
  console  - print (default)
  ntfy     - push via ntfy (https://ntfy.sh or a self-hosted server).
             ntfy topics are readable by anyone who knows the name, so use a
             long random topic and, ideally, an access token (ntfy_token).
"""

import urllib.request


def send(cfg, title, body, priority="default", click=None):
    n = cfg["notify"]
    sent = []
    for ch in n.get("channels", ["console"]):
        if ch == "console":
            print(f"\n=== {title} ===\n{body}\n" + (f"(open: {click})\n" if click else ""))
            sent.append(ch)
        elif ch == "ntfy":
            topic = n.get("ntfy_topic", "")
            if len(topic) < 16:
                raise ValueError("ntfy_topic is missing or too short to be private - use a long random name")
            server = n.get("ntfy_server", "https://ntfy.sh").rstrip("/")
            headers = {"Title": title.encode("utf-8"), "Priority": priority}
            if click:                                # tap the alert to open the record on the dashboard
                headers["Click"] = click
            if n.get("ntfy_token"):
                headers["Authorization"] = f"Bearer {n['ntfy_token']}"
            req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"), method="POST", headers=headers)
            urllib.request.urlopen(req, timeout=30).read()
            sent.append(ch)
        else:
            raise ValueError(f"unknown notify channel '{ch}'")
    return sent
