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
from smart_layer import audit, autoprofile, config, erp, report_registry, schema, store  # noqa: E402
from smart_layer.ask import Assistant  # noqa: E402
from smart_layer.reports import payroll, production, stock  # noqa: E402

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
[sources.scoreboard]
driver = "sqlite"
path = "demo_scoreboard.db"
copy_first = true
""")
    cfg = config.load(tmp / "config.toml")
    # the demo DB built here is not the hash-locked shipped demo, so install a wildcard test licence for the
    # server tests (the dashboard server refuses to serve data without a valid licence).
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from smart_layer import licence
    priv = Ed25519PrivateKey.generate()
    pub = tmp / "licence_key.pub"
    pub.write_bytes(priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    licence.PUBLIC_KEY_FILE = pub
    from datetime import date as _date
    licence.install(cfg, licence.new_licence("Test Mill", "*", _date(2099, 1, 1), priv))
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    e = erp.ERP(*erp.connect(cfg["erp"], cfg.base_dir), mapping, cfg["sources"], cfg.base_dir)
    con = store.open_store(cfg.path(cfg["store"]["path"]))
    store.sync(con, e, 400, log=lambda *_: None)
    return cfg, con


def test_sync_fills_every_entity(env):
    _, con = env
    for table in ("items", "stock_balances", "store_receipts", "store_issues", "parties", "accounts", "ledger_entries",
                  "account_balances", "looms", "loom_production", "contracts", "yarn_movements", "fabric_dispatches",
                  "yarn_stock", "fabric_stock", "beams", "beam_movements", "loom_monitor"):
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
    assert any(r[1] == "EL-002" for r in stock.reorder_list(con, cfg, END).rows)   # Code is now column 1


def test_fabric_stock_history_is_captured_from_the_erp(env):
    _, con = env
    # the month-end fabric trend is read from the ERP's own view as-of each month-end, stored at sync time
    n = con.execute("SELECT COUNT(*) FROM fabric_stock_history").fetchone()[0]
    assert n > 0
    months = [m for (m,) in con.execute("SELECT month FROM fabric_stock_history")]
    assert all(len(m) == 7 and m[4] == "-" for m in months)       # YYYY-MM keys


def test_fabric_chart_uses_stored_history_not_recompute(env):
    cfg, con = env
    d = web_data.build(con, cfg, END)
    pts = d["charts"]["fabric_stock"]
    stored = dict(con.execute("SELECT month, meters FROM fabric_stock_history"))
    for p in pts:
        assert round(stored[p["month"]]) == p["meters"]           # chart == stored ERP figure, never recomputed


def test_is_own_sizer_matches_as_substring():
    from smart_layer.textile.beams import is_own_sizer
    assert is_own_sizer("HIBA FABRICS", ["Hiba"])
    assert is_own_sizer("Hiba", ["HIBA FABRICS"])
    assert not is_own_sizer("AL NOOR SIZING", ["Hiba"])
    assert not is_own_sizer("", ["Hiba"])


def test_open_loans_use_amount_minus_repaid(env):
    cfg, con = env
    rep = payroll.loans(con, cfg, END)
    rows = rep.rows
    assert rep.columns[-1] == "Balance"
    for r in rows:
        assert r[-1] == r[4] - r[5] and r[-1] > 1                 # Balance = Amount - Repaid, and only open loans listed


def test_payslips_show_designation(env):
    cfg, con = env
    rep = payroll.payslips(con, cfg, END)
    assert rep.columns[:3] == ["Code", "Name", "Designation"]        # Designation column added, after Name
    for r in rep.rows:
        assert len(r) == len(rep.columns) and r[2] is not None        # a designation cell per row (from payroll.designation)


def test_strict_similar_name_matching():
    from smart_layer.reports.stock import _strict_similar, _tokens
    assert _strict_similar(_tokens("6202"), _tokens("6202 SKF"))          # whole-word prefix
    assert _strict_similar(_tokens("6202 SKF"), _tokens("6202 ZZ"))       # shared part-number lead token
    assert not _strict_similar(_tokens("6202"), _tokens("6203"))          # different number
    assert not _strict_similar(_tokens("BELT"), _tokens("BELT GUARD"))    # lead token not a part number
    assert not _strict_similar(_tokens("6202"), _tokens("6202"))          # identical = same item, not "similar"


def test_store_check_flags_similar_named_item_in_stock(tmp_path):
    from smart_layer import store as store_mod
    from smart_layer.reports import stock as stock_mod
    con = store_mod.open_store(tmp_path / "s.db")
    con.execute("INSERT INTO items (item_code, item_name, unit) VALUES ('6202','6202','pcs'),('6202SKF','6202 SKF','pcs')")
    con.execute("INSERT INTO stock_balances (item_code, qty) VALUES ('6202',0),('6202SKF',10)")
    con.execute("INSERT INTO store_issues (doc_no, doc_date, item_code, qty) VALUES ('1', ?, '6202', 5)", (date(2026, 10, 1).isoformat(),))
    con.commit()
    rep = stock_mod.stock_status(con, {"reports": {"tracked_issue_days": 90}}, date(2026, 10, 16))
    assert rep.columns[-1] == "Similar item in stock"
    row = [r for r in rep.rows if r[2] == "6202"][0]
    assert row[0] == "OUT OF STOCK" and "6202 SKF" in row[-1] and "10" in row[-1]


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
    # base: yarn used = yarn in the fabric delivered to the party (WIP deductions off, to isolate the method knobs)
    base = _closing(cfg, con, wip=False)
    assert set(base) == {"CON-40", "CON-41", "CON-42"}          # contracts with fabric delivered to their party
    r = base["CON-41"]
    assert r["received_bags"] > r["used_bags"] > 0 and abs(r["balance_bags"] - (r["received_bags"] - r["used_bags"])) < 1e-6
    # a smaller divisor means more yarn per meter, so more is charged to the fabric
    assert _closing(cfg, con, wip=False, divisor=800)["CON-41"]["used_bags"] > r["used_bags"]
    # the wastage allowance scales the fabric consumption exactly
    assert _closing(cfg, con, wip=False, wastage_allowance_pct=2)["CON-41"]["used_bags"] == pytest.approx(r["used_bags"] * 1.02)
    # per-party override touches only that party
    o = _closing(cfg, con, wip=False, parties=[{"match": "Kohinoor", "divisor": 800}])
    assert o["CON-42"]["used_bags"] > base["CON-42"]["used_bags"] and o["CON-41"]["used_bags"] == r["used_bags"]
    # a party can close the same yarn at its own count
    pc = _closing(cfg, con, wip=False, parties=[{"match": "Lahore", "yarns": {"Y40": {"closing_count": 35}}}])
    assert pc["CON-41"]["used_bags"] > r["used_bags"] and pc["CON-40"]["used_bags"] == base["CON-40"]["used_bags"]
    # the mill's own formula replaces the standard one
    assert _closing(cfg, con, wip=False, weight_formula="threads_per_inch * width_in / 700 / count")["CON-41"]["used_bags"] != r["used_bags"]
    # turning WIP on deducts the party's yarn still in the mill, so more is accounted for (higher 'used')
    assert _closing(cfg, con)["CON-41"]["used_bags"] >= r["used_bags"]
    # excluding a contract drops it entirely
    assert "CON-41" not in _closing(cfg, con, wip=False, exclude_contracts=["CON-41"])


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


def test_closing_count_overrides_change_consumption(env):
    cfg, con = env
    base = {r["contract"]: r for r in closing.contract_rows(con, method.Method(cfg["closing"]), END)}["CON-41"]
    wy = con.execute("SELECT warp_yarn FROM contracts WHERE contract_no = 'CON-41'").fetchone()[0]
    m = method.Method(cfg["closing"])
    m.count_overrides = {str(wy): {"warp_closing_count": 1}}       # a tiny count -> far more yarn per meter
    over = {r["contract"]: r for r in closing.contract_rows(con, m, END)}["CON-41"]
    assert over["used_bags"] > base["used_bags"]                   # the what-if count flows into the closing


def test_closing_counts_prefill_lists_yarns(env):
    cfg, con = env
    pre = closing.closing_counts(con, cfg, END)
    assert pre["yarns"] and all("yarn" in y and "warp_closing_count" in y for y in pre["yarns"])
    # party filter narrows to that party's yarns only
    one = closing.closing_counts(con, cfg, END, party="Lahore")
    assert one["party"] == "Lahore"


def test_save_closing_counts_sidecar_merges_on_load(tmp_path):
    (tmp_path / "config.toml").write_text('[closing]\n[closing.yarns."Y40"]\nwarp_closing_count = 40\n')
    res = config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 33, "weft_closing_count": 34,
                                                         "yarn_name": "40/1 CTN"}}, who="Azam")
    assert res["ok"] and res["saved"] == ["Y40"]
    side = (tmp_path / "closing_counts.local.toml").read_text()
    assert "changed" in side and "Azam" in side and "40/1 CTN" in side      # date + who recorded
    cfg = config.load(tmp_path / "config.toml")
    assert cfg["closing"]["yarns"]["Y40"]["warp_closing_count"] == 33        # the saved count wins over config.toml
    assert cfg["closing"]["yarns"]["Y40"]["weft_closing_count"] == 34
    # a blank or out-of-range count is ignored, never written
    res2 = config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 9999}, "Y50": {"weft_closing_count": ""}})
    assert res2["saved"] == [] and "Y50" not in config.load(tmp_path / "config.toml")["closing"]["yarns"]


def test_closing_counts_yarn_default_and_party_count(tmp_path):
    (tmp_path / "config.toml").write_text('[closing]\n[closing.yarns."Y40"]\nwarp_closing_count = 40\n')
    name = 'QADRI TRADERS(EMB)'                               # brackets: the party key must be escaped, not a regex
    config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 41}}, who="A")            # yarn default
    config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 51}}, who="A", party=name, scope="party")
    cfg = config.load(tmp_path / "config.toml")
    m = method.Method(cfg["closing"])
    assert m.closing_count("warp", "Y40", 30, m.settings(party=name)) == 51     # the party count wins for that party
    assert m.closing_count("warp", "Y40", 30, m.settings(party="OTHER")) == 41  # others get the yarn default
    # clearing the party count falls back to the yarn default
    config.save_closing_counts(tmp_path, {}, party=name, scope="party", clear=["Y40"])
    m2 = method.Method(config.load(tmp_path / "config.toml")["closing"])
    assert m2.closing_count("warp", "Y40", 30, m2.settings(party=name)) == 41


def test_party_count_matches_exact_name_only(tmp_path):
    (tmp_path / "config.toml").write_text('[closing]\n[closing.yarns."Y40"]\nwarp_closing_count = 40\n')
    config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 31}}, party="HAROON FABRIC PVT LTD", scope="party")
    config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 29}}, party="SB", scope="party")
    m = method.Method(config.load(tmp_path / "config.toml")["closing"])
    cc = lambda p: m.closing_count("warp", "Y40", 30, m.settings(party=p))
    assert cc("HAROON FABRIC PVT LTD") == 31          # the full exact name gets its party count
    assert cc("HAROON") == 40                         # a shorter name does NOT (not a substring match)
    assert cc("WAQAS SB") == 40 and cc("AHSAN SB") == 40   # "SB" never leaks into "... SB" parties
    assert cc("SB") == 29                             # only a party named exactly "SB"


def test_closing_counts_only_changed_rows_saved(tmp_path):
    (tmp_path / "config.toml").write_text('[closing]\n')
    # a value equal to the current effective value is not written (never freezes config over config.toml)
    res = config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 40}},
                                     current={"Y40": {"warp_closing_count": 40, "weft_closing_count": None}})
    assert res["saved"] == [] and not (tmp_path / "closing_counts.local.toml").read_text().count("Y40")


def test_closing_counts_party_name_with_quote_roundtrips(tmp_path):
    (tmp_path / "config.toml").write_text('[closing]\n')
    name = 'AB"C\\D & SONS'                                   # quote + backslash must be TOML-escaped
    config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 52}}, party=name, scope="party")
    cfg = config.load(tmp_path / "config.toml")               # would raise if the sidecar were invalid TOML
    m = method.Method(cfg["closing"])
    assert m.closing_count("warp", "Y40", 30, m.settings(party=name)) == 52


def test_broken_sidecar_is_not_overwritten_and_load_survives(tmp_path):
    (tmp_path / "config.toml").write_text('[closing]\n[closing.yarns."Y40"]\nwarp_closing_count = 40\n')
    side = tmp_path / "closing_counts.local.toml"
    side.write_text('[yarns."Y40"]\nwarp_closing_count = ')   # truncated / invalid TOML
    res = config.save_closing_counts(tmp_path, {"Y40": {"warp_closing_count": 33}})
    assert res["ok"] is False and "error" in res              # refuses to save
    assert side.read_text().endswith("= ")                    # the broken file is left untouched (not wiped)
    cfg = config.load(tmp_path / "config.toml")               # load still works (warns), ignoring the bad sidecar
    assert cfg["closing"]["yarns"]["Y40"]["warp_closing_count"] == 40


def test_whatif_closing_is_labelled(env):
    cfg, con = env
    wy = con.execute("SELECT warp_yarn FROM contracts WHERE contract_no = 'CON-41'").fetchone()[0]
    rep = closing.contract_closing(con, cfg, END, None, {str(wy): {"warp_closing_count": 1}})
    assert "(what-if)" in rep.title and any("WHAT-IF" in s for s in rep.summary)
    assert "(what-if)" not in closing.contract_closing(con, cfg, END).title    # the official closing is not labelled


def test_log_trim_keeps_tail(tmp_path):
    from smart_layer.logs import MARKER, trim_log
    log = tmp_path / "run.log"
    assert trim_log(log) is False                             # missing file: no-op, no error
    log.write_bytes(b"".join(b"line %d\n" % i for i in range(300000)))   # a few MB
    big = log.stat().st_size
    assert trim_log(log, max_bytes=1_000_000, keep_bytes=200_000) is True
    out = log.read_bytes()
    assert len(out) < big and out.startswith(MARKER) and out.rstrip().endswith(b"line 299999")
    assert trim_log(log, max_bytes=1_000_000, keep_bytes=200_000) is False  # already small enough


def test_ask_blocks_dangerous_functions(env):
    cfg, con = env
    bot = Assistant(cfg, client=object())                     # run_sql does not use the API client
    assert json.loads(bot.run_sql("SELECT 1 AS x"))["rows"] == [[1]]
    with pytest.raises(sqlite3.Error):                        # randomblob is denied by the authorizer
        bot.run_sql("SELECT randomblob(1000000)")


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


def test_overview_sales_series_and_average(env):
    cfg, con = env
    d = web_data.build(con, cfg, END)
    sales = d["charts"]["sales"]
    assert sales and all({"month", "sales", "meters"} <= set(x) for x in sales)
    assert len(sales) == len(d["finance"]) and sales[-1]["month"] == d["finance"][-1]["month"]
    k = d["kpis"]
    # the average covers the 12 complete months before the current one, bounded by the REAL first ledger month
    # (not the chart window), so a month with data is never dropped
    yy = END.year * 12 + END.month - 1
    avg_keys = [f"{(yy - j) // 12:04d}-{(yy - j) % 12 + 1:02d}" for j in range(12, 0, -1)]
    first = con.execute("SELECT MIN(voucher_date) FROM ledger_entries WHERE voucher_kind <> 'opening'").fetchone()[0]
    keys = [kk for kk in avg_keys if kk >= str(first)[:7]]
    assert k["sales_avg_months"] == len(keys)                    # 12 when the ledger goes back far enough
    assert k["sales_avg_12m"] > 0 and "dispatched_avg_12m" in k


def test_overview_fabric_by_quality(env):
    cfg, con = env
    fbq = web_data.build(con, cfg, END)["yarn_fabric"]["fabric_by_quality"]
    assert isinstance(fbq, list)
    assert all(r["meters"] > 1 for r in fbq)
    assert fbq == sorted(fbq, key=lambda r: -r["meters"])      # biggest first


def test_needs_attention_acknowledge_clears_and_restores(env):
    cfg, con = env
    d = web_data.build(con, cfg, END)
    assert d["findings"], "the demo raises findings"
    fid = d["findings"][0]["id"]
    before = len(d["findings"])
    try:
        store.set_state(con, "acked", {fid: {"title": "seen it", "at": "now"}})   # mark read
        d2 = web_data.build(con, cfg, END)
        assert fid not in {f["id"] for f in d2["findings"]}                  # gone from the strip
        assert len(d2["findings"]) == before - 1
        assert any(a["id"] == fid for a in d2["acknowledged"])               # shown under "marked read"
        store.set_state(con, "acked", {})                                    # restore
        assert fid in {f["id"] for f in web_data.build(con, cfg, END)["findings"]}
    finally:
        store.set_state(con, "acked", {})


def test_cash_group_ledger_is_newest_first_and_ties_to_balance(env):
    _, con = env
    L = web_finance.group_ledger(con, "cash")
    stored = con.execute("""SELECT COALESCE(SUM(b.balance),0) FROM account_balances b JOIN accounts a USING (account_code)
                            WHERE a.account_group = 'cash'""").fetchone()[0]
    assert round(L["balance"]) == round(stored)
    if L["rows"]:
        assert round(L["rows"][0]["balance"]) == round(stored)     # newest row carries the current balance
        assert all(("received" in r and "paid" in r) for r in L["rows"])


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


def test_urgent_alerts_are_a_subset_and_pushed_once(env):
    cfg, con = env
    store.set_state(con, "audit_pushed", [])
    try:
        a = audit.run(con, cfg, END)
        urgent = a.new_alerts(urgent_only=True)
        assert urgent, "the demo should raise some urgent findings"
        assert all(audit.is_urgent(f, cfg["audit"]) for f in urgent)
        assert all(f.severity in ("ACTION", "CONFIRMED") for f in urgent)
        assert audit.run(con, cfg, END).new_alerts(urgent_only=True) == []     # each pushed once
    finally:
        store.set_state(con, "audit_pushed", [])


def test_report_registry_builds_every_report(env):
    cfg, con = env
    for name in report_registry.NAMES:
        rep = report_registry.build(con, cfg, name)
        assert rep.columns, name                              # every report has headers
        for row in rep.rows:
            assert len(row) == len(rep.columns), name          # rows match the header width


def test_server_reports_menu_and_download(env):
    cfg, _ = env
    cfg.data["serve"] = {"password": "s3cret-pass"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.handler(web_server.App(cfg)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    auth = {"Authorization": "Basic " + base64.b64encode(b"owner:s3cret-pass").decode()}

    def get(path):
        return urllib.request.urlopen(urllib.request.Request(url + path, headers=auth))

    try:
        menu = json.loads(get("/api/reports").read())["reports"]
        assert {r["name"] for r in menu} == set(report_registry.NAMES)
        j = json.loads(get("/api/report?name=stock&fmt=json&date=2026-10-02").read())
        assert j["columns"] and all(len(r) == len(j["columns"]) for r in j["rows"])
        r = get("/api/report?name=stock&fmt=csv&date=2026-10-02")
        assert r.headers["Content-Disposition"].endswith('stock_2026-10-02.csv"')
        assert r.read().decode("utf-8-sig").splitlines()[0]    # a header line
        with pytest.raises(urllib.error.HTTPError) as e:        # unknown report name is refused
            get("/api/report?name=nonsense&fmt=json")
        assert e.value.code == 404
    finally:
        httpd.shutdown()
        cfg.data["serve"] = {}


def test_server_closing_counts_endpoints(env):
    import urllib.parse
    cfg, con = env
    cfg.data["serve"] = {"password": "s3cret-pass"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web_server.handler(web_server.App(cfg)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    auth = {"Authorization": "Basic " + base64.b64encode(b"o:s3cret-pass").decode()}

    def get(path):
        return urllib.request.urlopen(urllib.request.Request(url + path, headers=auth))

    try:
        assert json.loads(get("/api/closing/counts?date=2026-10-02").read())["yarns"]
        wy = con.execute("SELECT warp_yarn FROM contracts WHERE contract_no = 'CON-41'").fetchone()[0]
        base = json.loads(get("/api/closing?fmt=json&date=2026-10-02").read())
        q = urllib.parse.quote(json.dumps({str(wy): {"warp_closing_count": 1}}, separators=(",", ":")))
        over = json.loads(get(f"/api/closing?fmt=json&date=2026-10-02&counts={q}").read())
        assert over["rows"] != base["rows"]                     # the what-if count recomputed the closing
        # save (a yarn code no demo contract uses, so the shared env closing is untouched) writes the side file
        body = json.dumps({"counts": {"Y_TEST": {"warp_closing_count": 22, "yarn_name": "x"}}, "who": "Azam"}).encode()
        req = urllib.request.Request(url + "/api/closing/counts", data=body, method="POST",
                                     headers={**auth, "Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req).read())
        assert res["ok"] and res["saved"] == ["Y_TEST"]
        assert (cfg.base_dir / "closing_counts.local.toml").exists()
    finally:
        httpd.shutdown()
        cfg.data["serve"] = {}
        cfg.data.get("closing", {}).get("yarns", {}).pop("Y_TEST", None)
        (cfg.base_dir / "closing_counts.local.toml").unlink(missing_ok=True)


def test_server_will_not_open_to_network_without_password(env):
    cfg, _ = env
    cfg.data["serve"] = {"host": "0.0.0.0", "port": 0}
    try:
        with pytest.raises(SystemExit):
            web_server.serve(cfg)
    finally:
        cfg.data["serve"] = {}


def test_pilferage_compares_per_party_and_yarn(env):
    """Weft booked to one contract but woven on another contract of the same party and yarn is not excess."""
    cfg, con = env
    con.execute("""INSERT INTO contracts SELECT 'CON-41B', party_code, party_name, contract_date, quality, ends_per_inch,
                   picks_per_inch, width_in, warp_yarn, warp_count, weft_yarn, weft_count, 0, rate_per_meter, rate_per_pick, yarn_owner
                   FROM contracts WHERE contract_no = 'CON-41'""")
    con.execute("UPDATE yarn_movements SET contract_no = 'CON-41B' WHERE contract_no = 'CON-41' AND move_type = 'to_looms'")
    try:
        _, weekly, _, _ = pilferage.analyse(con, cfg, END, END)
        row = [w for w in weekly if "CON-41" in w[0]][0]
        assert "CON-41B" in row[0] and row[4] == pytest.approx(519, abs=60)       # same account, same excess as before
        cfg.data["pilferage"] = {"compare_by": "contract"}
        _, weekly_c, _, _ = pilferage.analyse(con, cfg, END, END)
        by = {w[0]: w for w in weekly_c}
        assert by["CON-41B"][3] == 0 and by["CON-41"][2] <= 50                   # contract level: issues and fabric apart
    finally:
        cfg.data["pilferage"] = {}
        con.execute("UPDATE yarn_movements SET contract_no = 'CON-41' WHERE contract_no = 'CON-41B'")
        con.execute("DELETE FROM contracts WHERE contract_no = 'CON-41B'")


def test_sync_keeps_full_history_for_yarn_and_fabric(env):
    _, con = env
    since = dict(con.execute("SELECT entity, since FROM sync_log"))
    # yarn, fabric and production are all kept in full so stock (woven - dispatched) and contract life are complete
    assert since["yarn_movements"] == "1900-01-01" and since["fabric_dispatches"] == "1900-01-01"
    assert since["loom_production"] == "1900-01-01" and since["store_receipts"] == "1900-01-01"
    # windowed entities (e.g. the gate register) still roll
    assert since["gate_passes"] > "2025-01-01"


# ---- installation -------------------------------------------------------------------------------------

from smart_layer.setup_wizard import Wizard  # noqa: E402


def test_setup_wizard_writes_config_and_env(tmp_path):
    shutil_src = ROOT / "mappings"
    (tmp_path / "mappings").mkdir()
    for f in shutil_src.glob("*.toml"):
        (tmp_path / "mappings" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    answers = iter(["Test Mill", "Rs", "1", "10.0.0.5:1521/ORCL", "smartlayer_ro", "y", r"C:\oracle\bin", "",
                    "2", "y", "8080", "y"])
    secrets_ = iter(["db-pass", "dash-pass", ""])
    out = []
    w = Wizard(tmp_path, ask=lambda q: next(answers), ask_secret=lambda q: next(secrets_), out=out.append, install=False)
    cfg_path = w.run()
    cfg = config.load(cfg_path)
    assert cfg["company"]["name"] == "Test Mill" and cfg["erp"]["driver"] == "oracle"
    assert cfg["erp"]["client_lib_dir"] == r"C:\oracle\bin"                  # Windows path kept as typed
    assert cfg["erp"]["password"] == "db-pass" and cfg["serve"]["password"] == "dash-pass"
    assert cfg["serve"]["host"] == "0.0.0.0" and "ntfy" in cfg["notify"]["channels"]
    assert len(cfg["notify"]["ntfy_topic"]) > 20
    # the secret is kept out of config.toml and lives in .env - in plain text, or DPAPI-encrypted on Windows
    env = (tmp_path / ".env").read_text()
    assert "db-pass" not in cfg_path.read_text() and ("db-pass" in env or "ERP_PASSWORD=DPAPI:" in env)


def test_config_reads_windows_encodings(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_PASSWORD", raising=False)
    (tmp_path / "config.toml").write_bytes('\ufeff[company]\nname = "BOM"\n[erp]\npassword = "${ERP_PASSWORD}"\n'.encode())
    (tmp_path / ".env").write_bytes("ERP_PASSWORD=x1\r\n".encode("utf-16"))
    c = config.load(tmp_path / "config.toml")
    assert c["company"]["name"] == "BOM" and c["erp"]["password"] == "x1"


def test_grants_lists_every_mapped_object(tmp_path, capsys):
    from smart_layer import cli
    (tmp_path / "config.toml").write_text(f"""[erp]
driver = "oracle"
mapping = "{(ROOT / 'mappings' / 'dost_textiles_oracle.toml').as_posix()}"
""")
    assert cli.main(["-c", str(tmp_path / "config.toml"), "grants"]) == 0
    sql = capsys.readouterr().out
    for obj in ("dostex.wv_prod_v", "dostex.wytm", "dostex.fab_gp_v", "dostex.acl"):
        assert f"GRANT SELECT ON {obj} TO smartlayer_ro;" in sql
    assert "GRANT EXECUTE ON dostex.get_item_stock" in sql and "INSERT" not in sql.upper().replace("INSERT INTO", "")


# ---- beams ----------------------------------------------------------------------------------------------

from smart_layer.textile import beams as beams_mod  # noqa: E402


def test_beam_days_left_backups_and_shrinkage(env):
    cfg, con = env
    by_c, _, mill, _ = beams_mod.learned_shrinkage(con, 7)
    assert by_c == pytest.approx({"CON-40": 0.07, "CON-41": 0.08, "CON-42": 0.09}, abs=0.002)
    rows = {r["loom_name"]: r for r in beams_mod.beams_on_looms(con, cfg, END)}
    assert len(rows) == 24
    assert rows["LOOM-05"]["days"] == pytest.approx(3, abs=0.6) and rows["LOOM-05"]["backup"] is None
    assert rows["LOOM-12"]["days"] == pytest.approx(4, abs=0.6) and rows["LOOM-12"]["backup"]["contract"] == "CON-42"
    assert rows["LOOM-20"]["over"]


def test_autoprofile_reads_and_fingerprints(env):
    cfg, _ = env
    con, _ = erp.connect(cfg["erp"], cfg.base_dir)
    p = autoprofile.profile(con, cfg["erp"]["driver"])
    # reads the ERP's own catalog
    assert p["tables"] > 10 and p["columns"] > 50
    # recognises the known ERP and names the mapping to load - no tuning needed for a known ERP
    assert p["recognised"] == "demo_sqlite" and p["confidence"] == 1.0 and p["mapping"] == "demo_sqlite.toml"
    # heuristic column matching (the unknown-ERP path) finds obvious fields by name/type
    sug = autoprofile.suggest_columns(p["schema"], schema.BY_NAME["employees"])
    assert sug["name"] and sug["name"][0][1] == "bp_name"
    assert sug["cnic"] and sug["cnic"][0][1] == "cnic"


def test_autoprofile_drafts_valid_mapping(env):
    import tomllib
    cfg, _ = env
    con, _ = erp.connect(cfg["erp"], cfg.base_dir)
    db = autoprofile.read_schema(con, cfg["erp"]["driver"])
    text, drafted, skipped = autoprofile.draft_mapping(db)
    d = tomllib.loads(text)                                   # the draft is valid TOML
    assert d["mapping"]["name"] and len(d["entities"]) >= 15
    assert "cnic AS cnic" in d["entities"]["employees"]["sql"]   # an obvious field is mapped, not left TODO


def test_selfcheck_passes_on_clean_demo(env):
    from smart_layer import selfcheck
    cfg, con = env
    findings = selfcheck.run(con, cfg, END)
    assert findings and "BAD" not in {f[0] for f in findings}   # a clean sync has no hard failures


def test_selfcheck_flags_bad_values():
    # a sync that produced an impossible efficiency and an unbalanced ledger must be caught
    from smart_layer import selfcheck
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE sync_log(entity TEXT, synced_at TEXT, since TEXT, rows INT);
        CREATE TABLE loom_monitor(read_date TEXT, loom_code TEXT, meters REAL, efficiency_pct REAL, rpm REAL, stops REAL);
        CREATE TABLE ledger_entries(voucher_date TEXT, account_code TEXT, amount REAL);
        INSERT INTO sync_log VALUES('loom_monitor','x',NULL,2),('ledger_entries','x',NULL,2);
        INSERT INTO loom_monitor VALUES('2026-10-01','L1',100,999,180,2),('2026-10-01','L2',100,85,180,2);
        INSERT INTO ledger_entries VALUES('2026-10-01','A',1000),('2026-10-01','B',-300);
    """)
    f = selfcheck.run(con, None, END)
    assert any(a == "loom_monitor" and "efficiency" in m for s, a, m in f)
    assert any(a == "ledger_entries" and "balance" in m for s, a, m in f)


def test_autoprofile_rejects_foreign_schema():
    # a database that is not a known ERP must not be mis-recognised
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE widgets(id INTEGER, label TEXT); CREATE TABLE orders_x(id INTEGER);")
    p = autoprofile.profile(con, "sqlite")
    assert p["recognised"] is None and p["confidence"] == 0.0


def test_beam_audit_and_sizer_ledger(env):
    cfg, con = env
    cfg.data["beams"] = {"warn_days": 5, "urgent_days": 3}
    try:
        found = [f for f in audit.run(con, cfg, END).findings if f.section == "Beams"]
    finally:
        cfg.data["beams"] = {}
    titles = " | ".join(f"{f.severity} {f.title}" for f in found)
    assert "ACTION LOOM-05: beam BM-05A finishes" in titles and "LOOM-12" not in titles
    assert "CONFIRMED Beam BM-20A on LOOM-20" in titles and "WATCH Empty beam BM-X9" in titles
    led = {r[0]: r for r in beams_mod.sizer_ledger(con, cfg, END).rows}
    # columns: Sizer, Empty sent, Back, Net out (qty), Number mismatch, Out over N days (by number)
    assert led["Rehman Sizing"][4] == 1 and led["Ali Sizing"][4] == ""   # Rehman has 1 beam back under a different number
    assert "BM-X9" in led["Rehman Sizing"][5]                            # the unaccounted beam is named


def test_non_daily_audit_does_not_rewrite_edit_snapshot(env):
    # a dashboard load or the 5-minute urgent check (persist defaults False) must NOT move the edit baseline
    cfg, con = env
    store.set_state(con, "edit_snapshot", {"taken": "MARKER", "issues": {}, "prod": {}})
    audit.run(con, cfg, END)
    assert store.get_state(con, "edit_snapshot")["taken"] == "MARKER"
    con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")


def test_edits_detected_between_audits(env):
    cfg, con = env
    store.set_state(con, "edit_snapshot", None)
    con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")
    audit.run(con, cfg, END, persist=True)                             # first daily run: snapshot only
    doc, item = con.execute("""SELECT doc_no, item_code FROM store_issues
                               WHERE qty > 2 AND doc_date BETWEEN '2026-07-01' AND '2026-09-01' LIMIT 1""").fetchone()
    con.execute("UPDATE store_issues SET qty = qty + 1 WHERE doc_no = ? AND item_code = ?", (doc, item))
    d, loom = con.execute("SELECT prod_date, loom_code FROM loom_production WHERE prod_date BETWEEN '2026-09-01' AND '2026-09-20' LIMIT 1").fetchone()
    con.execute("UPDATE loom_production SET meters = meters + 40 WHERE prod_date = ? AND loom_code = ?", (d, loom))
    try:
        found = [f for f in audit.run(con, cfg, END, persist=True).findings if f.section == "Edits"]
        assert any(f.severity == "CONFIRMED" and doc in f.title and "quantity changed" in f.title for f in found)
        assert any(f.severity == "WATCH" and "production of" in f.title and "changed" in f.title for f in found)
    finally:
        con.execute("UPDATE store_issues SET qty = qty - 1 WHERE doc_no = ? AND item_code = ?", (doc, item))
        con.execute("UPDATE loom_production SET meters = meters - 40 WHERE prod_date = ? AND loom_code = ?", (d, loom))
        con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")


def test_new_reports_run(env):
    cfg, con = env
    from smart_layer.reports import finance, reconcile
    from smart_layer.reports.production import production_value
    from smart_layer.textile import stock_position
    pl = finance.pnl(con, cfg, END)
    assert pl.rows[-1][1] == "PROFIT / (LOSS)" and pl.rows[-1][-1] > 0
    assert "difference Rs 2,000" in finance.balances(con, cfg, END).summary[0]           # the planted unbalanced JV
    assert finance.parties(con, cfg, END).rows
    rec = reconcile.store_consumption(con, cfg, END)
    assert "5103" in rec.summary[0] and rec.rows
    v = production_value(con, cfg, END)
    assert v.rows and all(r[4] for r in v.rows) and "Rs" in v.summary[0]
    assert stock_position.fabric_stock(con, cfg, END).rows and stock_position.yarn_stock(con, cfg, END).rows


def test_pdf_output(env, tmp_path):
    cfg, con = env
    from smart_layer.textile import beams as bm
    for rep in (audit.run(con, cfg, END).report(), bm.beam_report(con, cfg, END), stock.stock_status(con, cfg, END)):
        p = rep.write_pdf(tmp_path, "x.pdf", "Test Mill", "Smart Layer")
        assert p.read_bytes()[:4] == b"%PDF" and p.stat().st_size > 2000


def test_loom_monitor_second_source(env):
    cfg, con = env
    found = {f.title.split(" ")[0]: f for f in audit.run(con, cfg, END).findings if f.section == "Looms"}
    assert set(found) == {"LOOM-15", "LOOM-22"}
    assert "%)" in found["LOOM-15"].title and "-" in found["LOOM-15"].title.split("(")[-1]
    assert "+" in found["LOOM-22"].title.split("(")[-1]


def test_csv_source(tmp_path):
    (tmp_path / "exp").mkdir()
    (tmp_path / "exp" / "scb_2026-10-01.csv").write_text("LOOM NO,DATE,TOTAL METERS\n7,2026-10-01,210.5\n8,2026-10-01,190\n")
    (tmp_path / "exp" / "scb_2026-10-02.csv").write_bytes("LOOM NO,DATE,TOTAL METERS\n7,2026-10-02,205\n".encode("utf-8-sig"))
    con = erp.csv_connection({"files": "exp/*.csv", "table": "readings"}, tmp_path)
    assert con.execute("SELECT COUNT(*), SUM(TOTAL_METERS) FROM readings").fetchone() == (3, 605.5)


# --- Finance (ledger desk): classification, party ledger, KPIs, daily summary ---
from smart_layer.web import finance as web_finance  # noqa: E402


def test_finance_kpis_match_balances(env):
    _, con = env
    k = web_finance.kpis(con)
    # cash and bank KPIs equal the summed balances of those account groups
    for grp in ("cash", "bank"):
        want = con.execute("""SELECT COALESCE(SUM(b.balance),0) FROM account_balances b
                              JOIN accounts a USING (account_code) WHERE a.account_group = ?""", (grp,)).fetchone()[0]
        assert round(k[grp]) == round(want), grp
    # receivable_net is the signed sum over customers; gross and advances split it by sign
    assert k["receivable_gross"] >= 0 and k["advances_from_parties"] >= 0
    assert round(k["receivable_net"]) == round(k["receivable_gross"] - k["advances_from_parties"])


def test_finance_party_ledger_runs_to_balance(env):
    _, con = env
    parties = web_finance.parties(con, "conversion")
    assert parties and parties[0]["side"] in ("Owes us", "Advance received", "Settled")
    led = web_finance.ledger(con, parties[0]["code"])
    assert led["rows"], "ledger has rows"
    # rows are newest-first: the FIRST row's running balance is the current (stored) balance and the header balance
    stored = con.execute("SELECT balance FROM account_balances WHERE account_code = ?", (led["code"],)).fetchone()[0]
    assert round(led["rows"][0]["balance"]) == round(stored) == round(led["balance"])
    # an opening (old-ERP) line, when present, is the oldest, so it sorts LAST now
    kinds = [r["kind"] for r in led["rows"]]
    if "opening" in kinds:
        assert kinds[-1] == "opening"


def test_trial_balance_and_unbalanced_reports(env):
    from smart_layer.reports import finance as fin_rep
    cfg, con = env
    acc = con.execute("SELECT account_code FROM accounts LIMIT 1").fetchone()[0]
    # a clean ledger: trial balance agrees and the detection report is empty
    tb = fin_rep.trial_balance(con, cfg, END)
    assert tb.columns[-1].startswith("Difference") and any("Trial balance:" in s for s in tb.summary)
    base_unbal = len(fin_rep.unbalanced(con, cfg, date(2025, 1, 1), END).rows)
    # plant a NEGATIVE out-of-balance voucher (tests abs() in the summary) and a tiny within-tolerance one
    con.execute("""INSERT INTO ledger_entries (voucher_id, voucher_no, voucher_type, voucher_kind, voucher_date, account_code, amount)
                   VALUES ('TB-TEST', '9999', 'JV', 'journal', ?, ?, -12345)""", (END.isoformat(), acc))
    con.execute("""INSERT INTO ledger_entries (voucher_id, voucher_no, voucher_type, voucher_kind, voucher_date, account_code, amount)
                   VALUES ('TB-TINY', '9998', 'JV', 'journal', ?, ?, 5)""", (END.isoformat(), acc))   # under the Rs 10 tolerance
    try:
        unb = fin_rep.unbalanced(con, cfg, date(2025, 1, 1), END)
        planted = [r for r in unb.rows if r[2] == "9999"][0]
        assert planted[-1] == -12345                          # the signed difference is still shown per row
        assert "Rs -" not in " ".join(unb.summary)            # but the summary total uses abs()
        tb2 = fin_rep.trial_balance(con, cfg, END)
        assert any("OUT OF BALANCE" in s for s in tb2.summary) and any(r[2] == "9999" for r in tb2.rows)
        assert not any(r[2] == "9998" for r in tb2.rows)      # the Rs 5 voucher is under tolerance - not listed
        assert any("under the" in s and "tolerance" in s for s in tb2.summary)   # but noted
    finally:
        con.execute("DELETE FROM ledger_entries WHERE voucher_id IN ('TB-TEST', 'TB-TINY')")


def test_report_range_filter_routes_through_build(env):
    cfg, con = env
    # the From/To window reaches the range report; a tiny window yields no (or fewer) unbalanced vouchers
    wide = report_registry.build(con, cfg, "unbalanced", d0=date(2020, 1, 1), d1=END)
    narrow = report_registry.build(con, cfg, "unbalanced", d0=END, d1=END)
    assert "2020" in wide.title and len(narrow.rows) <= len(wide.rows)


def test_finance_daily_balances(env):
    _, con = env
    # the demo's last ledger day
    day = con.execute("SELECT MAX(voucher_date) FROM ledger_entries WHERE voucher_kind <> 'opening'").fetchone()[0]
    d = web_finance.daily(con, date.fromisoformat(day))
    assert d["summary"], "has voucher-type lines"
    # vouchers balance, so the day's debit and credit totals agree bar the odd planted error
    assert d["totals"]["debit"] > 0 and abs(d["totals"]["debit"] - d["totals"]["credit"]) <= 0.02 * d["totals"]["debit"]
    assert all(s["kind"] in ("receipt", "payment", "journal", "sale", "purchase", "other") for s in d["summary"])


def test_dashboard_has_finance_weaving_charts(env):
    cfg, con = env
    d = web_data.build(con, cfg, END)
    assert set(d["fin"]["kpis"]) >= {"receivable_net", "cash", "bank", "accrued_payables"}
    assert d["fin"]["chips"] and d["fin"]["parties"]
    assert len(d["charts"]["cash_bank"]) == 30 and len(d["charts"]["receivables"]) == 12
    assert d["weaving"]["looms"] and "scoreboard_available" in d["weaving"]
    # the floor shows only real looms (ever had ERP production) - never scoreboard-only phantom machines
    prod_looms = {r[0] for r in con.execute("SELECT DISTINCT loom_code FROM loom_production")}
    assert all(l["code"] in prod_looms for l in d["weaving"]["looms"])
    # scoreboard efficiency is a percentage, not multiplied by 100
    effs = [l["scb_eff"] for l in d["weaving"]["looms"] if l["scb_eff"] is not None]
    assert effs and all(0 <= e <= 100 for e in effs)
    assert d["yarn_fabric"]["yarn_hand_bags"] >= 0 and d["yarn_fabric"]["fabric_m"] >= 0
    # count-wise weft stock: rows carry count/brand/cone_pbag and the totals equal the per-row sums
    yf = d["yarn_fabric"]
    assert yf["weft_stock"] and all({"count", "brand", "cone_pbag", "bags", "cones"} <= set(r) for r in yf["weft_stock"])
    assert yf["weft_total"]["bags"] == sum(r["bags"] for r in yf["weft_stock"])
    assert yf["weft_total"]["cones"] == sum(r["cones"] for r in yf["weft_stock"])
    # godown bags roll the loose cones in, so they are at least the whole-bag total
    assert yf["yarn_hand_bags"] >= yf["weft_total"]["bags"]
    assert yf["yarn_counts"] == len({r["count"] for r in yf["weft_stock"]})
    json.dumps(d)


def test_finance_endpoints(env):
    cfg, con = env
    app = web_server.App(cfg)
    code = web_finance.parties(con, "conversion")[0]["code"]
    led = app.ledger(code)
    assert led and led["code"] == code and led["rows"]
    pl = app.fin_parties("conversion", "")
    assert pl["parties"] and all(p["group"] == "customer" for p in pl["parties"])
    day = con.execute("SELECT MAX(voucher_date) FROM ledger_entries WHERE voucher_kind <> 'opening'").fetchone()[0]
    dd = app.fin_daily(date.fromisoformat(day))
    assert dd["totals"]["debit"] > 0 and dd["totals"]["credit"] > 0
