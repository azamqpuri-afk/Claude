"""
76_push_store_pdfs.py - phone push (ntfy, same topic as the audit) with links to today's store PDFs in the Drive reports folder:
the weekly store stock check (store_stock_check_<date>.pdf) and the store demand / order list (weekly_vendor_order_list_<date>.pdf).
Owner, 2-Oct-2026: "I didn't receive today's store demand sheet" - the two Friday jobs only saved to Drive.
Run after run_weekly_vendor_order_list.ps1 (Friday 10:05). Waits for Google Drive to sync the files (up to 10 minutes).
python 76_push_store_pdfs.py [--date 2026-10-02]
"""
import configparser
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
FOLDER = "18CYkYptYXvrrq_szDHt4lbtsfyK4j3W5"
CFG = configparser.ConfigParser(); CFG.read(HERE / "report_config.ini")


def drive_links(names):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    drv = build("drive", "v3", credentials=creds)
    out = {}
    for n in names:
        r = drv.files().list(q=f"name = '{n}' and '{FOLDER}' in parents and trashed = false", fields="files(id)",
                             supportsAllDrives=True, includeItemsFromAllDrives=True).execute().get("files", [])
        if r:
            out[n] = f"https://drive.google.com/file/d/{r[0]['id']}/view"
    return out


def main(d):
    names = {f"store_stock_check_{d}.pdf": "Store stock check", f"weekly_vendor_order_list_{d}.pdf": "Store demand / order list"}
    links = {}
    for _ in range(20):                       # Drive sync of the N: folder can take a few minutes
        links = drive_links(names)
        if len(links) == len(names):
            break
        time.sleep(30)
    if not links:
        print("no store PDFs found in Drive for", d); return 1
    body = "\n".join(f"{names[n]}: {u}" for n, u in links.items())
    missing = [names[n] for n in names if n not in links]
    if missing:
        body += "\nNot found yet: " + ", ".join(missing)
    first = next(iter(links.values()))
    req = urllib.request.Request(url=f"https://ntfy.sh/{CFG.get('ntfy', 'topic')}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": f"Store sheets {d}", "Click": first, "Tags": "package"})
    urllib.request.urlopen(req, timeout=30).read()
    print("pushed:", body.replace("\n", " | "))
    return 0


if __name__ == "__main__":
    d = sys.argv[sys.argv.index("--date") + 1] if "--date" in sys.argv else date.today().isoformat()
    sys.exit(main(d))
