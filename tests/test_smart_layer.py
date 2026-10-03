"""End-to-end tests on the demo ERP, plus the safety guards."""

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

import build_demo_erp  # noqa: E402
from smart_layer import audit, config, erp, store  # noqa: E402
from smart_layer.ask import Assistant  # noqa: E402
from smart_layer.reports import production, stock  # noqa: E402

END = date(2026, 10, 2)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo")
    build_demo_erp.build(tmp / "demo_erp.db", END)
    (tmp / "config.toml").write_text(f"""
[company]
name = "Test Mill"
[erp]
driver = "sqlite"
path = "demo_erp.db"
mapping = "{(ROOT / 'mappings' / 'demo_sqlite.toml').as_posix()}"
[store]
path = "store.db"
""")
    cfg = config.load(tmp / "config.toml")
    e = erp.ERP(*erp.connect(cfg["erp"], cfg.base_dir), erp.Mapping(cfg.path(cfg["erp"]["mapping"])))
    con = store.open_store(cfg.path(cfg["store"]["path"]))
    store.sync(con, e, 400, log=lambda *_: None)
    return cfg, con


def test_sync_fills_every_entity(env):
    _, con = env
    for table in ("items", "stock_balances", "store_receipts", "store_issues", "parties", "accounts",
                  "ledger_entries", "account_balances", "looms", "loom_production"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0, table


def test_audit_finds_every_planted_mistake(env):
    cfg, con = env
    found = audit.run(con, cfg, END).findings
    expected = {
        ("ACTION", "Finance", "keyed 60 days after"),
        ("ACTION", "Finance", "double payment: Punjab Bearings House"),
        ("CONFIRMED", "Finance", "does not balance by Rs 2,000"),
        ("CONFIRMED", "Finance", "Trial balance is out by Rs 2,000"),
        ("WATCH", "Finance", "Large payment"),
        ("CONFIRMED", "Store", "Sley sword bush with quantity 0"),
        ("ACTION", "Store", "Bearing 6205 issued again to LOOM-03"),
        ("ACTION", "Store", "Reed 72s: rate"),
        ("ACTION", "Store", "Lube oil 68: Rs 1,520 from Chenab"),
        ("ACTION", "Store", "bill V05-777"),
        ("CONFIRMED", "Store", "Proximity sensor: stock is"),
        ("ACTION", "Weaving", "LOOM-07: no production"),
        ("CONFIRMED", "Weaving", "LOOM-11 2026-10-02: efficiency 112%"),
        ("WATCH", "Weaving", "LOOM-15 (contract CON-42): rejection 20%"),
    }
    for sev, section, text in expected:
        assert any(f.severity == sev and f.section == section and text in f.title for f in found), (sev, text)


def test_audit_dismiss_rule_and_push_once(env):
    cfg, con = env
    cfg.data["audit"]["dismiss"] = [{"match": "^big-", "why": "advance payments are normal"}]
    try:
        a = audit.run(con, cfg, END)
        assert any(f.severity == "INFO" and "Large payment" in f.title for f in a.findings)
        first = a.new_alerts()
        assert first and all(f.severity in ("ACTION", "CONFIRMED") for f in first)
        assert audit.run(con, cfg, END).new_alerts() == []          # each finding is pushed once
    finally:
        cfg.data["audit"]["dismiss"] = []
        store.set_state(con, "audit_pushed", [])


def test_reports(env):
    cfg, con = env
    p = production.daily_production(con, cfg, END)
    assert len(p.rows) == 23 and "LOOM-07" in p.summary[1]
    s = stock.stock_status(con, cfg, END)
    assert [r for r in s.rows if r[2] == "EL-002"][0][0] == "OUT OF STOCK"
    assert any(r[2] == "EL-002" for r in stock.reorder_list(con, cfg, END).rows)


def test_new_zero_reports_only_newly_out_of_stock(env):
    cfg, con = env
    store.set_state(con, "stock_snapshot", None)
    con.execute("DELETE FROM state WHERE key = 'stock_snapshot'")
    assert stock.new_zero(con, cfg, END).rows == []                 # first run only records
    store.set_state(con, "stock_snapshot", {"EL-002": 5, "SP-010": 0})
    rows = stock.new_zero(con, cfg, END).rows
    assert [r[1] for r in rows] == ["EL-002"]


@pytest.mark.parametrize("sql", ["DELETE FROM items", "SELECT 1; DROP TABLE items", "UPDATE x SET y = 1",
                                 "  insert into t values (1)", "BEGIN; SELECT 1"])
def test_mapping_accepts_only_select(sql):
    with pytest.raises(erp.MappingError):
        erp.check_select(sql)


def test_mapping_select_forms_allowed():
    assert erp.check_select("-- note\nWITH a AS (SELECT 1) SELECT * FROM a;")


def test_bind_styles():
    sql = "SELECT * FROM t WHERE d >= :since AND t2.c::date < :today"
    assert erp.bind(sql, {"since": 1, "today": 2}, "named")[1] == {"since": 1, "today": 2}
    q, p = erp.bind("SELECT * FROM t WHERE a >= :since AND b <= :today AND c > :since", {"since": 1, "today": 2}, "qmark")
    assert q.count("?") == 3 and p == [1, 2, 1]


@pytest.mark.parametrize("sql", ["DELETE FROM items", "INSERT INTO items VALUES (1,2,3,4)", "DROP TABLE items",
                                 "ATTACH DATABASE 'x.db' AS x", "UPDATE items SET item_name = 'x'"])
def test_store_read_only_connection_blocks_writes(env, sql):
    cfg, _ = env
    con = store.open_read_only(cfg.path(cfg["store"]["path"]))
    with pytest.raises(sqlite3.Error):
        con.execute(sql)


class FakeClient:
    """Stands in for anthropic.Anthropic: asks for one query, then answers with its result."""

    def __init__(self, sql):
        self.sql, self.calls = sql, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1:
            return SimpleNamespace(stop_reason="tool_use", content=[
                SimpleNamespace(type="tool_use", id="t1", name="run_sql", input={"sql": self.sql})])
        result = kw["messages"][-1]["content"][0]
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=result["content"])])


def test_ask_runs_queries_on_the_store(env):
    cfg, _ = env
    bot = Assistant(cfg, client=FakeClient("SELECT COUNT(*) AS n FROM looms"))
    answer, queries = bot.ask("kitni looms hain?")
    assert json.loads(answer)["rows"] == [[24]] and queries == ["SELECT COUNT(*) AS n FROM looms"]
    first = bot.client.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["tools"][0]["name"] == "run_sql"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_ask_cannot_write(env):
    cfg, _ = env
    bot = Assistant(cfg, client=FakeClient("DELETE FROM looms"))
    bot.ask("delete everything")
    assert bot.messages[2]["content"][0]["is_error"] is True
    con = sqlite3.connect(cfg.path(cfg["store"]["path"]))
    assert con.execute("SELECT COUNT(*) FROM looms").fetchone()[0] == 24


# ---- contract closing and pilferage ---------------------------------------------------------------

from smart_layer.textile import closing, method, pilferage  # noqa: E402


def _closing(cfg, con, **over):
    saved = cfg.data.get("closing", {})
    cfg.data["closing"] = {**saved, **over}
    try:
        return {r["contract"]: r for r in closing.contract_rows(con, method.Method(cfg["closing"]), END)}
    finally:
        cfg.data["closing"] = saved


def test_closing_balances_follow_the_mills_method(env):
    cfg, con = env
    base = _closing(cfg, con)
    assert set(base) == {"CON-40", "CON-41", "CON-42"}          # CON-39 has nothing left to close: hidden
    r = base["CON-41"]
    assert r["received_bags"] > r["used_bags"] > 0 and abs(r["balance_bags"] - (r["received_bags"] - r["used_bags"])) < 1e-6
    # a smaller divisor means more yarn per meter, so less balance left with the mill
    assert _closing(cfg, con, divisor=800)["CON-41"]["used_bags"] > r["used_bags"]
    # allowance, basis and rejection rule all change the result
    assert _closing(cfg, con, wastage_allowance_pct=2)["CON-41"]["used_bags"] == pytest.approx(r["used_bags"] * 1.02)
    assert _closing(cfg, con, basis="woven")["CON-41"]["meters"] != r["meters"]
    assert _closing(cfg, con, basis="woven", rejection_charged_to="party")["CON-41"]["meters"] > \
        _closing(cfg, con, basis="woven")["CON-41"]["meters"]
    # per-party override touches only that party
    o = _closing(cfg, con, parties=[{"match": "Kohinoor", "divisor": 800}])
    assert o["CON-42"]["used_bags"] > base["CON-42"]["used_bags"] and o["CON-41"]["used_bags"] == r["used_bags"]
    # a party can close the same yarn at its own count
    pc = _closing(cfg, con, parties=[{"match": "Lahore", "yarns": {"Y40": {"closing_count": 35}}}])
    assert pc["CON-41"]["used_bags"] > r["used_bags"] and pc["CON-40"]["used_bags"] == base["CON-40"]["used_bags"]
    # the mill's own formula replaces the standard one
    f = _closing(cfg, con, weight_formula="threads_per_inch * width_in / 700 / count")
    assert f["CON-41"]["used_bags"] != r["used_bags"]


def test_yarn_groups_share_one_balance(env):
    cfg, con = env
    con.execute("UPDATE contracts SET weft_yarn = 'Y40B' WHERE contract_no = 'CON-41'")   # weft booked under a second code
    try:
        split = [r for r in closing.contract_rows(con, method.Method({}), END) if r["contract"] == "CON-41"]
        joined = [r for r in closing.contract_rows(con, method.Method({"yarn_groups": [["Y40", "Y40B"]]}), END)
                  if r["contract"] == "CON-41"]
        assert len(split) == 2 and len(joined) == 1
        assert joined[0]["balance_bags"] == pytest.approx(sum(r["balance_bags"] for r in split))
    finally:
        con.execute("UPDATE contracts SET weft_yarn = 'Y40' WHERE contract_no = 'CON-41'")


@pytest.mark.parametrize("formula", ["__import__('os').system('x')", "open('f')", "count.real", "[1, 2]", "x + 1"])
def test_closing_formula_is_arithmetic_only(formula):
    with pytest.raises(method.FormulaError):
        method.Method({"weight_formula": formula})


def test_pilferage_finds_planted_cases(env):
    cfg, con = env
    flags, weekly, sizing, _ = pilferage.analyse(con, cfg, date(2026, 7, 4), END)
    f = {(x["contract"], x["date"]): " ".join(x["reasons"]) for x in flags}
    assert "no production" in f[("CON-39", "2026-09-20")] and "outside working hours" in f[("CON-39", "2026-09-20")]
    assert "kg needed" in f[("CON-41", "2026-10-02")] and "outside working hours" in f[("CON-41", "2026-10-02")]
    assert "11 days after" in f[("CON-42", "2026-09-12")]
    loss = {(s["sizer"], s["contract"]): s["loss_pct"] for s in sizing}
    assert loss[("Rehman Sizing", "CON-42")] == pytest.approx(9.0, abs=0.2)
    assert loss[("Ali Sizing", "CON-41")] < 5


def test_daily_audit_pushes_yarn_action(env):
    cfg, con = env
    found = audit.run(con, cfg, END).findings
    assert any(f.severity == "ACTION" and f.section == "Yarn" and "CON-41" in f.title for f in found)


# ---- owner dashboard ----------------------------------------------------------------------------------

import base64  # noqa: E402
import threading  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from smart_layer.web import data as web_data  # noqa: E402
from smart_layer.web import server as web_server  # noqa: E402
from smart_layer.web import static_page  # noqa: E402


def test_dashboard_data(env):
    cfg, con = env
    d = web_data.build(con, cfg, END)
    assert d["as_of"] == "2026-10-02" and len(d["production"]) == 30
    assert d["kpis"]["action"] >= 9 and d["kpis"]["yarn_flags"] >= 1 and d["kpis"]["meters"] > 0
    assert {r["contract"] for r in d["closing"]} >= {"CON-40", "CON-41", "CON-42"}
    assert d["finance"][0]["sales"] > 0                       # leading empty months trimmed
    json.dumps(d)                                              # serialisable


def test_static_page_embeds_data_safely(env):
    cfg, con = env
    html = static_page(con, cfg, END)
    assert "/*__DATA__*/null" not in html and '"as_of": "2026-10-02"' in html
    assert "</script>" not in html.split("const STATIC_DATA = ", 1)[1].split(";\nconst $", 1)[0]


def test_server_requires_password(env):
    cfg, _ = env
    cfg.data["serve"] = {"password": "s3cret-pass"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.handler(web_server.App(cfg)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(url + "/api/data")
        assert e.value.code == 401
        req = urllib.request.Request(url + "/api/data?date=2026-10-02",
                                     headers={"Authorization": "Basic " + base64.b64encode(b"owner:s3cret-pass").decode()})
        assert json.loads(urllib.request.urlopen(req).read())["as_of"] == "2026-10-02"
    finally:
        httpd.shutdown()
        cfg.data["serve"] = {}


def test_server_will_not_open_to_network_without_password(env):
    cfg, _ = env
    cfg.data["serve"] = {"host": "0.0.0.0", "port": 0}
    try:
        with pytest.raises(SystemExit):
            web_server.serve(cfg)
    finally:
        cfg.data["serve"] = {}
