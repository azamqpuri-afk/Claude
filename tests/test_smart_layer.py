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
