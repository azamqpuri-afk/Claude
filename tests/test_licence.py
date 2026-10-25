"""Licence checks: signed, tied to one server, expiry with warning and grace, enforced by the CLI and dashboard."""

import base64
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from smart_layer import cli, config, licence  # noqa: E402
from smart_layer.web import server as web_server  # noqa: E402

TODAY = date(2026, 10, 3)
HERE = "AAAA-BBBB-CCCC-DDDD"


@pytest.fixture
def keys(tmp_path, monkeypatch):
    priv = Ed25519PrivateKey.generate()
    pub = tmp_path / "licence_key.pub"
    pub.write_bytes(priv.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo))
    monkeypatch.setattr(licence, "PUBLIC_KEY_FILE", pub)
    monkeypatch.setattr(licence, "machine_id", lambda: HERE)
    return priv


@pytest.fixture
def mill(tmp_path):
    (tmp_path / "config.toml").write_text('[company]\nname = "Test Mill"\n[erp]\ndriver = "sqlite"\npath = "mill.db"\n'
                                          'mapping = "x.toml"\n[serve]\npassword = "pw-123456"\n')
    return config.load(tmp_path / "config.toml")


def code(priv, expires_in=200, machine=HERE):
    return licence.new_licence("Test Mill", machine, TODAY + timedelta(days=expires_in), priv, issued=TODAY)


@pytest.mark.parametrize("days, state", [(200, "valid"), (10, "expiring"), (-3, "grace"), (-8, "expired")])
def test_expiry_states(keys, days, state):
    st = licence.check_text(code(keys, days), TODAY)
    assert st.state == state
    assert st.allowed == (state != "expired")
    assert st.warning == (state in ("expiring", "grace"))


def test_other_server_and_any_server(keys):
    assert licence.check_text(code(keys, machine="1111-2222-3333-4444"), TODAY).state == "wrong_machine"
    assert licence.check_text(code(keys, machine="*"), TODAY).state == "valid"


def test_changed_or_forged_licence_is_refused(keys):
    good = code(keys)
    head, body, sig = good.split(".")
    p = json.loads(licence._unb64(body))
    p["expires"] = "2099-12-31"                                   # the mill edits the expiry date
    forged = f"{head}.{licence._b64(json.dumps(p).encode())}.{sig}"
    assert licence.check_text(forged, TODAY).state == "invalid"
    other = Ed25519PrivateKey.generate()                          # someone signs with their own key
    assert licence.check_text(code(other), TODAY).state == "invalid"
    assert licence.check_text("rubbish", TODAY).state == "invalid"


def test_no_public_key_means_no_licence(tmp_path, monkeypatch):
    monkeypatch.setattr(licence, "PUBLIC_KEY_FILE", tmp_path / "missing.pub")
    assert licence.check_text("SLIC1.a.b", TODAY).state == "invalid"


def test_install_from_code_and_file(keys, mill, tmp_path):
    assert licence.status(mill).state == "missing"
    assert licence.install(mill, "not a licence").state == "invalid"
    assert not licence.licence_path(mill).exists()
    assert licence.install(mill, code(keys)).state == "valid"
    f = tmp_path / "license_new.key"
    f.write_text(code(keys, 400))
    assert licence.install(mill, str(f)).allowed
    assert licence.licence_path(mill).with_name("license.key.bak").exists()
    assert licence.status(mill).state == "valid"


def test_demo_needs_no_licence(tmp_path):
    import shutil
    from pathlib import Path
    ref = Path(licence.__file__).resolve().parent.parent / "demo" / "demo_erp.db"  # the exact shipped demo DB
    shutil.copy(ref, tmp_path / "demo_erp.db")
    (tmp_path / "config.toml").write_text('[erp]\ndriver = "sqlite"\npath = "demo_erp.db"\nmapping = "x.toml"\n')
    assert licence.status(config.load(tmp_path / "config.toml")).state == "demo"


def test_hand_built_marker_file_is_not_demo(tmp_path):
    # a hand-built SQLite file with the demo marker added must NOT run unlicensed - it isn't the shipped demo file
    import sqlite3
    db = tmp_path / "demo_erp.db"
    sqlite3.connect(db).executescript(
        "CREATE TABLE _smartlayer_demo (token TEXT); INSERT INTO _smartlayer_demo VALUES ('Sitara-Weaving-demo-v1');")
    (tmp_path / "config.toml").write_text('[erp]\ndriver = "sqlite"\npath = "demo_erp.db"\nmapping = "x.toml"\n')
    assert licence.status(config.load(tmp_path / "config.toml")).state == "missing"


def test_demo_erp_with_real_store_is_not_demo(tmp_path):
    # the genuine demo ERP, but the local STORE was synced from a real ERP mapping -> must NOT run unlicensed
    import shutil
    import sqlite3
    from pathlib import Path
    ref = Path(licence.__file__).resolve().parent.parent / "demo" / "demo_erp.db"
    shutil.copy(ref, tmp_path / "demo_erp.db")
    store = sqlite3.connect(tmp_path / "store.db")
    store.executescript("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);")
    store.execute("INSERT INTO state VALUES ('synced_mapping', ?)", ('"dost_textiles_oracle"',))
    store.commit()
    store.close()
    (tmp_path / "config.toml").write_text('[erp]\ndriver = "sqlite"\npath = "demo_erp.db"\nmapping = "x.toml"\n[store]\npath = "store.db"\n')
    assert licence.status(config.load(tmp_path / "config.toml")).state == "missing"


def test_sync_exit_codes():
    from smart_layer.cli import _sync_exit
    assert _sync_exit({"a": 5, "b": 3, "_failed": []}) == 0          # all ok
    assert _sync_exit({"a": None, "b": None, "_failed": ["a", "b"]}) == 1   # everything failed
    assert _sync_exit({"a": 5, "b": None, "_failed": ["b"]}) == 2    # some failed


def test_renamed_real_db_is_not_demo(tmp_path):
    import sqlite3
    db = tmp_path / "demo_erp.db"                       # a real ERP copy renamed to look like the demo
    sqlite3.connect(db).execute("CREATE TABLE sales (x)")
    (tmp_path / "config.toml").write_text('[erp]\ndriver = "sqlite"\npath = "demo_erp.db"\nmapping = "x.toml"\n')
    assert licence.status(config.load(tmp_path / "config.toml")).state == "missing"


def test_cli_stops_without_licence(keys, mill, capsys):
    cfg_file = str(mill.path("config.toml"))
    for cmd in (["sync"], ["report", "stock"], ["audit"]):   # serve/dashboard start regardless (licence added there)
        with pytest.raises(SystemExit) as e:
            cli.main(["--config", cfg_file, *cmd])
        assert "Licence" in str(e.value)
    assert cli.main(["--config", cfg_file, "licence", "check"]) == 1
    assert cli.main(["--config", cfg_file, "licence", "install", code(keys)]) == 0
    assert cli.main(["--config", cfg_file, "licence", "check"]) == 0
    assert HERE in capsys.readouterr().out


def test_dashboard_blocks_data_until_licence_installed(keys, mill):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.handler(web_server.App(mill)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    auth = {"Authorization": "Basic " + base64.b64encode(b"owner:pw-123456").decode()}

    def call(path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + path, data=data, headers={**auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        status, body = call("/api/data")
        assert status == 403 and body["machine"] == HERE
        assert call("/api/ask", {"question": "hi"})[0] == 403
        assert call("/api/licence")[1]["state"] == "missing"
        assert call("/api/licence", {"text": "bad"})[0] == 400
        status, body = call("/api/licence", {"text": code(keys)})
        assert status == 200 and body["allowed"]
        assert call("/api/licence")[1]["state"] == "valid"
        with pytest.raises(urllib.error.HTTPError) as e:           # installing still needs the dashboard password
            urllib.request.urlopen(urllib.request.Request(url + "/api/licence", data=b'{"text": "x"}'))
        assert e.value.code == 401
    finally:
        httpd.shutdown()
