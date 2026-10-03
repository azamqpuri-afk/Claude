"""End-to-end test of the separate cloud app: register a mill, push its store, view it behind a login."""

import base64
import io
import json
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

import build_demo_erp  # noqa: E402
from cloud_app import server as cloud_server  # noqa: E402
from smart_layer import config, erp, store  # noqa: E402

END = date(2026, 10, 2)


@pytest.fixture(scope="module")
def mill_zip(tmp_path_factory):
    """A real processed store + config, as the agent would upload."""
    tmp = tmp_path_factory.mktemp("mill")
    build_demo_erp.build(tmp / "demo_erp.db", END)
    (tmp / "config.toml").write_text(f"""
[company]
name = "Cloud Test Mill"
[erp]
driver = "sqlite"
path = "demo_erp.db"
mapping = "{(ROOT / 'mappings' / 'demo_sqlite.toml').as_posix()}"
[store]
path = "store.db"
[sources.scoreboard]
driver = "sqlite"
path = "demo_scoreboard.db"
copy_first = true
""")
    cfg = config.load(tmp / "config.toml")
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    e = erp.ERP(*erp.connect(cfg["erp"], cfg.base_dir), mapping, cfg["sources"], cfg.base_dir)
    con = store.open_store(cfg.path(cfg["store"]["path"]))
    store.sync(con, e, 400, log=lambda *_: None)
    con.close()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.write(tmp / "store.db", "store.db")
        z.write(tmp / "config.toml", "config.toml")
    return buf.getvalue()


@pytest.fixture
def cloud(tmp_path):
    app = cloud_server.Cloud(tmp_path / "cloud.db", tmp_path / "tenants")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), cloud_server.handler(app))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield app, url
    httpd.shutdown()


def _get(url, auth=None):
    req = urllib.request.Request(url)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url, data, headers):
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else {})
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, (json.loads(body) if body else {})


def test_full_cloud_flow(cloud, mill_zip):
    app, url = cloud
    m = app.reg.add("Sitara Weaving")
    mid, token, view = m["id"], m["token"], m["view_password"]
    assert mid == "sitara-weaving"

    # upload needs the right token
    assert _post(f"{url}/{mid}/api/upload", mill_zip, {"Authorization": "Bearer wrong"})[0] == 401
    assert _post(f"{url}/{mid}/api/upload", mill_zip, {"Authorization": f"Bearer {token}"}) == (200, {"ok": True})

    # viewing needs the right password
    assert _get(f"{url}/{mid}/api/data")[0] == 401
    assert _get(f"{url}/{mid}/api/data", auth="owner:wrong")[0] == 401
    status, body = _get(f"{url}/{mid}/api/data", auth=f"owner:{view}")
    assert status == 200
    assert json.loads(body)["as_of"]

    # the dashboard page loads, and /<mill> redirects to /<mill>/
    assert _get(f"{url}/{mid}/", auth=f"owner:{view}")[0] == 200
    assert b"<html" in _get(f"{url}/{mid}/", auth=f"owner:{view}")[1]

    # unknown mill is a 404
    assert _get(f"{url}/nope/api/data", auth=f"owner:{view}")[0] == 404


def test_disabling_blocks_everything(cloud, mill_zip):
    app, url = cloud
    m = app.reg.add("Qadri Textiles")
    mid, token, view = m["id"], m["token"], m["view_password"]
    _post(f"{url}/{mid}/api/upload", mill_zip, {"Authorization": f"Bearer {token}"})
    app.reg.set_enabled(mid, False)
    assert _get(f"{url}/{mid}/api/data", auth=f"owner:{view}")[0] == 401     # viewing blocked
    assert _post(f"{url}/{mid}/api/upload", mill_zip, {"Authorization": f"Bearer {token}"})[0] == 401  # upload blocked
    app.reg.set_enabled(mid, True)
    assert _get(f"{url}/{mid}/api/data", auth=f"owner:{view}")[0] == 200


def test_isolation_between_mills(cloud, mill_zip):
    app, url = cloud
    a = app.reg.add("Mill A")
    b = app.reg.add("Mill B")
    _post(f"{url}/{a['id']}/api/upload", mill_zip, {"Authorization": f"Bearer {a['token']}"})
    # Mill B's password cannot read Mill A, and B has no data yet
    assert _get(f"{url}/{a['id']}/api/data", auth=f"o:{b['view_password']}")[0] == 401
    assert _get(f"{url}/{b['id']}/api/data", auth=f"o:{b['view_password']}")[0] == 503
