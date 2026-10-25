"""
Mill-side agent: sync the ERP locally (read-only, as always), then push the processed store to the cloud.

    python -m cloud_app.agent --config config.toml --url https://cloud.example.com --mill sitara --token <token>

Only data/smart_layer.db and config.toml are sent (no .env, no passwords, and never the raw ERP). Schedule this
in place of (or alongside) the local daily run when a mill is on the cloud plan.
"""

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smart_layer import config as sl_config  # noqa: E402
from smart_layer import erp as sl_erp  # noqa: E402
from smart_layer import store as sl_store  # noqa: E402


def sync_local(cfg):
    mapping = sl_erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    e = sl_erp.ERP(*sl_erp.connect(cfg["erp"], cfg.base_dir), mapping, cfg["sources"], cfg.base_dir)
    con = sl_store.open_store(cfg.path(cfg["store"]["path"]))
    try:
        return sl_store.sync(con, e, int(cfg["sync"]["history_days"]), log=print)
    finally:
        con.close()


def make_zip(cfg):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(cfg.path(cfg["store"]["path"]), "store.db")
        cfg_file = cfg.base_dir / "config.toml"
        if cfg_file.exists():
            z.write(cfg_file, "config.toml")              # settings only; secrets live in .env, which is NOT sent
    return buf.getvalue()


def push(url, mill, token, blob):
    req = urllib.request.Request(f"{url.rstrip('/')}/{mill}/api/upload", data=blob, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/zip"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.status


def main(argv=None):
    p = argparse.ArgumentParser(description="Smart Layer cloud agent (runs at the mill)")
    p.add_argument("--config", default="config.toml")
    p.add_argument("--url", required=True, help="cloud base URL, e.g. https://cloud.example.com")
    p.add_argument("--mill", required=True, help="the mill id given by Azam Puri")
    p.add_argument("--token", required=True, help="the upload token given by Azam Puri")
    p.add_argument("--no-sync", action="store_true", help="upload the existing store without re-syncing first")
    p.add_argument("--force", action="store_true", help="upload even if some data sets failed to sync")
    a = p.parse_args(argv)
    cfg = sl_config.load(a.config)
    if not a.no_sync:
        res = sync_local(cfg)
        failed = (res or {}).get("_failed") or []
        done = [k for k, v in (res or {}).items() if k != "_failed" and v is not None]
        if failed and not done:
            print(f"Sync failed completely ({', '.join(failed)}) - NOT uploading stale data.")
            return 1
        if failed and not a.force:                        # some sets failed: don't quietly ship a partial store
            print(f"WARNING: {len(failed)} data set(s) did not sync: {', '.join(failed)}. "
                  "Not uploading (use --force to upload anyway, or fix the mapping first).")
            return 2
    status = push(a.url, a.mill, a.token, make_zip(cfg))
    print("Uploaded." if status == 200 else f"Upload returned {status}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
