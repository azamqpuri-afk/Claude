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


def test_demo_mapping_every_entity_column_exists(env):
    # Every entity SQL in the demo mapping must execute and alias only real schema columns. This catches a mapping
    # that references a column the source does not have (the 0.17.6 Oracle 'thaan' vs 'than' blocker) for any ERP
    # we can run in CI. erp.fetch raises MappingError on an unknown/missing-required column and the DB driver raises
    # on a non-existent source column, so a clean pass over all mapped entities means none are broken.
    cfg, _ = env
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    e = erp.ERP(*erp.connect(cfg["erp"], cfg.base_dir), mapping, cfg["sources"], cfg.base_dir)
    for name in mapping.entities:
        if name not in schema.BY_NAME:
            continue
        # pulling the first row forces the query to execute and the column check in fetch() to run
        next(iter(e.fetch(name, date(1900, 1, 1))), None)


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
    assert "CONFIRMED Beam BM-20A on LOOM-20" in titles
    # Rehman is square by COUNT (BM-X9 came back under a different number), so it is no longer a false WATCH - it is
    # an INFO instead (item 2: judge empty beams by quantity per sizer, not by beam number).
    assert "WATCH Empty beam BM-X9" not in titles
    assert any(f.severity == "INFO" and "all back by count" in f.title for f in found)
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


# ---------------------------------------------------------------------------------------------------------------
# v0.13: sensor twin check, store room / demand / issue history, weekly beams, report delivery, sync freshness
# ---------------------------------------------------------------------------------------------------------------
from datetime import datetime as _dt, timedelta as _td  # noqa: E402

from smart_layer.audit import sensor as _sensor  # noqa: E402
from smart_layer.reports import delivery as _delivery  # noqa: E402
from smart_layer.textile import beams as _beams  # noqa: E402


def _twin_rows(base, n=6, a="2", b="4", gap_sec=1):
    """n twin stops of looms a & b: each stops together and restarts together (within a second)."""
    rows = []
    for i in range(n):
        t = base + _td(minutes=3 * i)
        rows.append((a, t.isoformat(sep=" "), (t + _td(seconds=40)).isoformat(sep=" ")))
        rows.append((b, (t + _td(seconds=gap_sec)).isoformat(sep=" "), (t + _td(seconds=40 + gap_sec)).isoformat(sep=" ")))
    return rows


def test_sensor_episodes_confirms_twin_no_layout():
    base = _dt(2026, 10, 2, 20, 0, 0)
    rows = _twin_rows(base, 6)
    rows.append(("9", (base + _td(hours=2)).isoformat(sep=" "), (base + _td(hours=2, seconds=3)).isoformat(sep=" ")))
    eps = _sensor.episodes(rows, {**_sensor.DEFAULTS})
    conf = [e for e in eps if e["confirmed"]]
    assert conf and {conf[0]["a"], conf[0]["b"]} == {"2", "4"} and conf[0]["twins"] >= 5
    assert conf[0]["near"] == "unknown"                       # no layout -> graded on timing alone


def test_sensor_episodes_layout_drops_far_pairs():
    base = _dt(2026, 10, 2, 20, 0, 0)
    # looms 2 & 4 neighbours in shed 1; loom 20 is in shed 2 (far from 2)
    t = {**_sensor.DEFAULTS, "sheds": {"1": {"columns": [[2, 4, 6, 8], [1, 3, 5, 7]]}, "2": {"columns": [[20, 22], [19, 21]]}}}
    near = _sensor.episodes(_twin_rows(base, 6, "2", "4"), t)
    assert any(e["confirmed"] and e["near"] in ("neighbours", "near") for e in near)
    far = _sensor.episodes(_twin_rows(base, 6, "2", "20"), t)
    assert far and all(not e["confirmed"] and e["near"] == "far" for e in far)   # far pair never confirmed


def test_sensor_run_pushes_confirmed_and_stale(env, tmp_path):
    cfg, _ = env
    con = store.open_store(tmp_path / "sensor_store.db")
    try:
        con.executemany("INSERT INTO looms (loom_code, loom_name) VALUES (?, ?)", [("2", "LOOM # 2"), ("4", "LOOM # 4")])
        base = _dt.now() - _td(minutes=40)                    # recent, so NOT stale
        rows = [(r[1][:10], r[0], r[1], r[2]) for r in _twin_rows(base, 6)]
        con.executemany("INSERT INTO loom_stops (stop_date, loom_code, stop_time, restart_time) VALUES (?, ?, ?, ?)", rows)
        con.commit()
        a = audit.run(con, cfg, END)
        assert any(f.section == "Looms" and f.key.startswith("sensor-") and f.severity == "ACTION"
                   and not f.key.startswith("sensor-stale") for f in a.findings)
        # urgent, so the live 5-minute run pushes it
        assert any(f.key.startswith("sensor-") for f in a.new_alerts(urgent_only=True))
    finally:
        con.close()
    # a store whose newest stop is old -> a stale ACTION
    con2 = store.open_store(tmp_path / "stale_store.db")
    try:
        old = _dt.now() - _td(days=3)
        con2.execute("INSERT INTO loom_stops (stop_date, loom_code, stop_time, restart_time) VALUES (?, ?, ?, ?)",
                     (old.date().isoformat(), "2", old.isoformat(sep=" "), (old + _td(seconds=5)).isoformat(sep=" ")))
        con2.commit()
        a2 = audit.run(con2, cfg, END)
        assert any(f.key.startswith("sensor-stale") and f.severity == "ACTION" for f in a2.findings)
    finally:
        con2.close()


def test_store_room_shows_all_items_not_just_out_low(env):
    cfg, con = env
    room = stock.store_room_rows(con, cfg, END)
    statuses = {r["status"] for r in room}
    assert "IN STOCK" in statuses                              # the whole room, not only out/low
    # more items than the out/low-in-window list the store tab used to show
    outlow = [r for r in stock._status_rows(con, END, int(cfg["reports"]["tracked_issue_days"]))
              if r["status"] != "IN STOCK"]
    assert len(room) > len(outlow)
    assert all("last_issue" in r and "last_receipt" in r for r in room)


def test_demand_report_lists_items_with_reasons(env):
    cfg, con = env
    rep = stock.demand_list(con, cfg, END)
    assert "Why listed" in rep.columns
    why = rep.columns.index("Why listed")
    assert rep.rows and all(r[why] for r in rep.rows)          # every listed item has a reason


def test_issue_history_newest_first(env):
    cfg, con = env
    code = con.execute("SELECT item_code FROM store_issues GROUP BY item_code ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    h = stock.issue_history(con, code, 20)
    assert h["item_code"] == code and h["rows"]
    dates = [r["date"] for r in h["rows"] if r["date"]]
    assert dates == sorted(dates, reverse=True)               # newest first
    assert h["summary"]["days"] == 90


def test_weekly_beam_report_has_both_rates(env):
    cfg, con = env
    rep = _beams.weekly_beam_report(con, cfg, END)
    assert "Days (ERP)" in rep.columns and "Days (mon)" in rep.columns and "Finish" in rep.columns


def test_report_delivery_saves_lists_and_prunes(env, tmp_path):
    cfg, con = env
    cfg.data["reports"]["output_dir"] = str(tmp_path / "out")
    rep = report_registry.build(con, cfg, "demand")
    p = _delivery.save(cfg, rep, "demand", "2026-10-02")
    assert p.exists() and p.suffix == ".pdf"
    files = _delivery.listing(cfg)
    assert any(f["name"] == p.name for f in files)
    assert _delivery.read_file(cfg, p.name) and _delivery.read_file(cfg, "../config.toml") is None
    # retention: make the file old, then prune
    import os
    old = _dt.now().timestamp() - 400 * 86400
    os.utime(p, (old, old))
    cfg.data["reports"]["retention_weeks"] = 26
    assert _delivery.prune(cfg) >= 1 and not p.exists()


def test_sync_health_flags_stale(env):
    cfg, con = env
    h = web_data._sync_health(con, cfg)
    assert "live_stale" in h and "daily_stale" in h
    # the env's last sync time is 'now' at build, so it should be fresh at test time
    assert h["live_at"] is not None


# ---------------------------------------------------------------------------------------------------------------
# v0.13.2: loom_stops history cap, demand rule(a) 180d, sync lock, grants scoping
# ---------------------------------------------------------------------------------------------------------------
def test_loom_stops_history_is_capped_to_14_days(tmp_path):
    from datetime import date as _d, timedelta as _t

    class FakeMapping:
        name = "fake"
        entities = {"loom_stops": "x", "store_issues": "x"}
        source = {}

    class FakeERP:
        def __init__(self): self.mapping = FakeMapping(); self.calls = {}
        def fetch(self, name, since, as_of=None): self.calls[name] = since; return iter([])

    con = store.open_store(tmp_path / "s.db")
    fe = FakeERP()
    store.sync(con, fe, 400, entities=["loom_stops", "store_issues"],
               full_history=("store_issues",), fabric_history=False, log=lambda *_: None)
    assert fe.calls["loom_stops"] == _d.today() - _t(days=14)      # capped, not 400
    assert fe.calls["store_issues"] == _d(1900, 1, 1)              # full history untouched
    con.close()


def test_sync_lock_is_exclusive(tmp_path):
    with store.sync_lock(tmp_path) as a:
        assert a
        with store.sync_lock(tmp_path) as b:
            assert b is False                                     # a second sync cannot start while one holds it
    with store.sync_lock(tmp_path) as c:
        assert c                                                  # released on exit


def test_demand_rule_a_uses_180_day_window(env):
    cfg, con = env
    # an item used 120 days before END with zero stock must be listed (rule a is 180 days, not 90)
    code = "DEMAND-OLD-USE"
    with con:
        con.execute("INSERT INTO items (item_code, item_name, unit) VALUES (?,?,?)", (code, "Old-use spare", "pcs"))
        con.execute("INSERT INTO store_issues (doc_no, doc_date, item_code, qty) VALUES (?,?,?,?)",
                    ("D1", (END - __import__("datetime").timedelta(days=120)).isoformat(), code, 5))
        con.execute("INSERT INTO stock_balances (item_code, qty) VALUES (?, 0)", (code,))
    try:
        rep = stock.demand_list(con, cfg, END)
        assert any(r[1] == code for r in rep.rows)                # listed though last issue was > 90 days ago
    finally:
        with con:
            con.execute("DELETE FROM items WHERE item_code=?", (code,))
            con.execute("DELETE FROM store_issues WHERE item_code=?", (code,))
            con.execute("DELETE FROM stock_balances WHERE item_code=?", (code,))


def test_grants_exclude_non_erp_sources(capsys):
    from smart_layer import cli, config as _cfg
    data = {"erp": {"mapping": str(ROOT / "mappings" / "dost_textiles_oracle.toml"), "driver": "oracle"}}
    cli.cmd_grants(_cfg.Config(data, ROOT), SimpleNamespace(user="smartlayer_ro"))
    out = capsys.readouterr().out.lower()
    assert "dostex." in out                                       # real ERP tables are granted
    assert "grant select on loomstat" not in out                 # Access scoreboard tables are NOT
    assert "grant select on rtlsum" not in out


def test_short_history_entities_are_pruned(tmp_path):
    from datetime import date as _d, timedelta as _t
    con = store.open_store(tmp_path / "p.db")
    old = (_d.today() - _t(days=60)).isoformat()
    recent = (_d.today() - _t(days=1)).isoformat()
    with con:
        con.execute("INSERT INTO loom_stops (stop_date,loom_code,stop_time,restart_time) VALUES (?,?,?,?)",
                    (old, "2", old + " 10:00:00", old + " 10:01:00"))

    class FM:
        name = "f"; entities = {"loom_stops": "x"}; source = {}

    class FE:
        def __init__(s): s.mapping = FM()
        def fetch(s, n, since, as_of=None):
            return iter([(recent, "2", recent + " 10:00:00", recent + " 10:01:00", None, None)])

    store.sync(con, FE(), 400, entities=["loom_stops"], full_history=(), fabric_history=False, log=lambda *_: None)
    dates = [r[0] for r in con.execute("SELECT stop_date FROM loom_stops")]
    cap = (_d.today() - _t(days=14)).isoformat()
    assert dates == [recent]                                      # old row pruned, recent kept
    assert all(x >= cap for x in dates)
    con.close()


def test_short_history_prune_uses_full_cap_not_live_window(tmp_path):
    # the LIVE sync (small window) must still keep up to the cap, not trim loom_stops to the live window
    from datetime import date as _d, timedelta as _t
    con = store.open_store(tmp_path / "pc.db")
    within_cap = (_d.today() - _t(days=10)).isoformat()   # older than the 3-day live window, inside the 14-day cap
    beyond_cap = (_d.today() - _t(days=20)).isoformat()
    with con:
        for dt in (within_cap, beyond_cap):
            con.execute("INSERT INTO loom_stops (stop_date,loom_code,stop_time,restart_time) VALUES (?,?,?,?)",
                        (dt, "2", dt + " 10:00:00", dt + " 10:01:00"))

    class FM:
        name = "f"; entities = {"loom_stops": "x"}; source = {}

    class FE:
        def __init__(s): s.mapping = FM()
        def fetch(s, n, since, as_of=None): return iter([])

    store.sync(con, FE(), 3, entities=["loom_stops"], full_history=(), fabric_history=False, log=lambda *_: None)
    dates = sorted(r[0] for r in con.execute("SELECT stop_date FROM loom_stops"))
    assert within_cap in dates and beyond_cap not in dates    # 14-day cap honoured even on a 3-day live sync
    con.close()


def test_live_sync_refreshes_account_balances(env, tmp_path):
    # cash/bank (and receivable/payable) balances come from account_balances, so the live sync must refresh it
    cfg, _ = env
    assert "account_balances" in store.LIVE_ENTITIES
    mapping = erp.Mapping(cfg.path(cfg["erp"]["mapping"]))
    e = erp.ERP(*erp.connect(cfg["erp"], cfg.base_dir), mapping, cfg["sources"], cfg.base_dir)
    con = store.open_store(tmp_path / "live.db")
    res = store.sync(con, e, 3, entities=list(store.LIVE_ENTITIES), full_history=(), fabric_history=False, log=lambda *_: None)
    assert res.get("account_balances", 0) > 0                 # the live set pulled balances
    assert con.execute("SELECT COUNT(*) FROM account_balances").fetchone()[0] > 0
    con.close()


def test_change_feed_records_at_sync_time(env, tmp_path):
    # the feed is built from the sync-time change_log, NOT from entered_at; `since` is the real client cursor
    # (data.build()["change_cursor"]), and the new rows arrive through store.sync on a live sync.
    from datetime import date as _d
    cfg, _ = env
    today = _d.today().isoformat()

    class FM:
        name = "f"; source = {}
        entities = {"store_issues": "x", "items": "x"}

    class FE:
        def __init__(s, issues): s.mapping = FM(); s.issues = issues
        def fetch(s, n, since, as_of=None):
            if n == "items":
                return iter([("I1", "Reed", "pcs", None, None, None), ("I2", "Bearing", "pcs", None, None, None)])
            if n == "store_issues":
                return iter(s.issues)
            return iter([])

    iss1 = ("s1", today, None, "I1", 1, 50, "LOOM 5", None)
    iss2 = ("s2", today, None, "I2", 2, 80, "LOOM 31", None)
    dbp = tmp_path / "cl.db"
    ent = ["items", "store_issues"]
    # baseline sync (store_issues empty before -> no events recorded)
    con = store.open_store(dbp)
    store.sync(con, FE([iss1]), 3, entities=ent, full_history=(), fabric_history=False, track_changes=True, log=lambda *_: None)
    payload0 = web_data.build(con, cfg, _d.today())
    cursor0 = payload0["change_cursor"]                      # the id the client stores and passes back next time
    assert "T" not in payload0["now"] and payload0["now"][10] == " "   # display clock format (space, not 'T')
    con.close()
    # a new slip arrives; the live sync records it in change_log at sync time
    con = store.open_store(dbp)
    store.sync(con, FE([iss1, iss2]), 3, entities=ent, full_history=(), fabric_history=False, track_changes=True, log=lambda *_: None)
    res = web_data.changes(con, cfg, cursor0)
    assert web_data.changes(con, cfg, "")["events"] == []    # no cursor (first load) -> nothing animates
    assert res["cursor"] >= cursor0                          # cursor advances
    con.close()
    kinds = {e["kind"] for e in res["events"]}
    assert "store" in kinds                                  # the new slip shows up for the real client cursor
    assert any(e.get("item") == "Bearing" for e in res["events"] if e["kind"] == "store")


# ---- 0.15.0 feature coverage -----------------------------------------------------------------------------------
from datetime import date as _d, timedelta as _td   # noqa: E402
from smart_layer import manual as _manual, pushes as _pushes   # noqa: E402
from smart_layer.reports import store_extra as _se   # noqa: E402
from smart_layer.reports import finance as _repfin   # noqa: E402
from smart_layer.textile import closing as _cl, contracts as _contracts, sizing as _sizing   # noqa: E402
from smart_layer.web import finance as _webfin   # noqa: E402

_V15_RANGE = {"store-expense", "issue-slips", "store-returns", "loom-consumption", "weft-consumption"}
_V15_REPORTS = ["store-expense", "issue-slips", "store-returns", "unused-stock", "loom-consumption",
                "cost-metre", "contracts", "yarn-shortage", "sizer-ledger", "empty-beams",
                "sized-closing", "weft-consumption"]


def test_v015_every_new_report_builds(env):
    cfg, con = env
    d0 = date(END.year, END.month, 1)
    for name in _V15_REPORTS:
        rep = report_registry.build(con, cfg, name, END, d0, END) if name in _V15_RANGE \
            else report_registry.build(con, cfg, name, END)
        assert rep is not None and rep.columns, f"{name} returned no columns"
        assert isinstance(rep.text(), str)                   # renders without error


def test_v015_live_formula_xlsx_exports(env, tmp_path):
    cfg, con = env
    # a report that ships a live-formula workbook (sized_yarn_closing / sizer_ledger set xlsx_sheets)
    rep = _sizing.sizer_ledger_full(con, cfg, END)
    assert rep.xlsx_sheets, "sizer ledger should carry a live-formula workbook spec"
    p = rep.write_xlsx(tmp_path, "sizer.xlsx", cfg["company"]["name"])
    assert p.exists() and p.stat().st_size > 0
    # the workbook really contains formulas (not just values)
    import openpyxl
    wb = openpyxl.load_workbook(p)
    formulas = [c.value for ws in wb.worksheets for row in ws.iter_rows() for c in row
                if isinstance(c.value, str) and c.value.startswith("=")]
    assert formulas, "no live formulas written"


def test_v015_month_sales_and_bank_accounts(env):
    cfg, con = env
    ms = _webfin.month_sales(con, d0=date(2000, 1, 1), d1=date(2099, 1, 1))
    assert ms["count"] == len(ms["rows"]) and ms["total"] == sum(r["amount"] for r in ms["rows"])
    assert all(r["amount"] > 0 for r in ms["rows"])          # invoice value is the customer debit
    ba = _webfin.bank_accounts(con)
    assert ba["count"] == len(ba["accounts"]) and ba["total"] == sum(a["balance"] for a in ba["accounts"])
    assert all(a["side"] in ("in hand", "overdrawn") for a in ba["accounts"])


def test_v015_fabric_history_stock_matches_report(env):
    cfg, con = env
    fh = web_data.fabric_history(con, cfg, date(2000, 1, 1), END)
    # per-row stock sums to the totals, and totals.stock equals the fabric-stock report total
    assert fh["totals"]["stock"] == sum(r["stock"] for r in fh["rows"])
    live = con.execute("SELECT COALESCE(SUM(meters),0) FROM fabric_stock").fetchone()[0]
    assert fh["totals"]["stock"] == round(live)


def test_v015_closing_sheet_three_modes_and_export(env, tmp_path):
    cfg, con = env
    lst = _cl.contract_list(con)
    assert lst, "demo has contracts"
    cno, party = lst[0]["contract"], lst[0]["party"]
    one = _cl.closing_sheet(con, cfg, contract=cno)
    assert one["contracts"] and one["totals"]["contracts"] == 1
    whole = _cl.closing_sheet(con, cfg, party=party)
    assert whole["totals"]["contracts"] >= 1                 # party mode spans >= the one contract
    # IGP yarn + OGP fabric are date-wise lists
    assert isinstance(one["igp"], list) and isinstance(one["ogp"], list)
    # closing counts used and a per-yarn balance are exposed
    assert one.get("counts_used") and "warp" in one["counts_used"]
    assert isinstance(one.get("balance", {}).get("rows"), list)
    rep = _cl.closing_sheet_report(con, cfg, contract=cno)
    assert rep.xlsx_sheets and len(rep.xlsx_sheets) == 4      # Summary + Yarn balance + Yarn in + Fabric out
    assert any(sh["name"] == "Yarn balance" for sh in rep.xlsx_sheets)
    p = rep.write_xlsx(tmp_path, "closing.xlsx", cfg["company"]["name"])
    assert p.exists() and p.stat().st_size > 0


def test_v015_manual_inputs_roundtrip(env):
    cfg, con = env
    _manual.set_value(con, "rewind", "SET-1", "kg", "123.5", who="tester", note="counted")
    assert float(_manual.get(con, "rewind", "SET-1", "kg")) == 123.5
    _manual.set_value(con, "rewind", "SET-1", "kg", "130", who="tester")
    hist = _manual.history(con, "rewind", "SET-1", "kg")
    assert len(hist) >= 2 and float(hist[0]["value"]) == 130   # newest first, edit history kept
    latest = _manual.latest(con, "rewind")                     # {key: {field: value}}
    assert float(latest["SET-1"]["kg"]) == 130


def test_v015_pushes_run(env):
    cfg, con = env
    assert isinstance(_pushes._production_line(con, END), str)
    assert isinstance(_pushes.morning_summary(cfg, con, END), str)   # console channel: prints, returns the body
    wc = _pushes.weft_check(cfg, con, END)
    assert wc is None or isinstance(wc, str)                 # None only if the ERP day is not keyed


def test_v015_sized_closing_reads_erp_and_unit_is_lb(env):
    cfg, con = env
    assert con.execute("SELECT COUNT(*) FROM sized_sets").fetchone()[0] > 0   # ERP sized-set receipts synced
    rep = _sizing.sized_yarn_closing(con, cfg, END)
    assert rep.rows, "sized-closing has sets"
    counts = [r[13] for r in rep.rows if r[13]]
    assert counts, "a closing count was computed from ERP sized-set data (not all zero)"
    # kg -> lb MULTIPLIES by 2.2046, so the count lands near the warp count (10-80s), never the ~150+ the
    # divide-bug produced. The demo's CON-40 is 60s warp -> count in the 40s-60s band.
    assert all(8 <= c <= 90 for c in counts), f"counts out of sane band (unit bug?): {sorted(counts)[:3]}..."
    # lb/m column (12) must be larger than kg/m column (11) - lb is the bigger unit
    lbm = [r for r in rep.rows if r[12] and r[11]]
    assert lbm and all(r[12] > r[11] for r in lbm)


def test_v015_notify_changes_per_date_and_dedupe(env, tmp_path, monkeypatch):
    cfg, _ = env
    con = store.open_store(tmp_path / "pushstore.db")
    sent = []
    monkeypatch.setattr(_pushes.notify, "send", lambda cfg, title, body, **k: sent.append((title, body)))

    def add(kind, payload):
        con.execute("INSERT INTO change_log (synced_at, entity, kind, payload) VALUES (datetime('now'), ?, ?, ?)",
                    (kind, kind, json.dumps(payload)))
        con.commit()

    add("woven", {"meters": 500, "dates": {"2026-10-01": 500}})
    add("out", {"what": "fabric", "doc_no": "18", "gatepass_id": "G-1001", "meters": 1200, "party": "ACME"})
    # first run after upgrade: seed the cursor past the backlog, push NOTHING
    assert _pushes.notify_changes(cfg, con) == 0 and not sent
    # now new rows arrive: a new production date, a new OGP, and a DUPLICATE gatepass_id in the SAME batch
    # (same unique id, different doc_no) which must dedupe to one push.
    add("woven", {"meters": 300, "dates": {"2026-10-02": 300}})
    add("out", {"what": "fabric", "doc_no": "GPOUT-7", "gatepass_id": "G-1002", "meters": 800, "party": "BETA"})
    add("out", {"what": "fabric", "doc_no": "7B", "gatepass_id": "G-1002", "meters": 800, "party": "BETA"})
    n = _pushes.notify_changes(cfg, con)
    titles = " | ".join(t for t, _ in sent)
    assert "Production entered 02-Oct" in titles                     # pushed by production DATE, not calendar day
    assert titles.count("Fabric out") == 1                           # G-1002 deduped on gatepass_id
    assert n == 2
    # a repeat run pushes nothing new
    assert _pushes.notify_changes(cfg, con) == 0
    con.close()


def test_v015_combine_counts_formats_no_crash(env):
    from smart_layer.config import Config
    from smart_layer.textile.contracts import _canon_map
    # real [yarn] shape: element 0 is a display label, the rest are yarn NAMES (not numbers) - must not raise
    cfg = Config({"yarn": {"combine_counts": [["52/1 & 54/1 CTN", "52/1 CTN", "54/1 CTN"]]}, "reports": {}}, ".")
    canon = _canon_map(cfg)
    assert canon.get(52) == 52 and canon.get(54) == 52          # 52 and 54 netted under the smaller, 52
    # numeric [reports] shape still works; an unparsable group element is skipped, not raised
    cfg2 = Config({"yarn": {}, "reports": {"combine_counts": [[52, 54], ["no number here"]]}}, ".")
    assert _canon_map(cfg2).get(54) == 52
    # and yarn_shortage runs end-to-end with the label format in place
    cfg3, con = env
    cfg3.data.setdefault("yarn", {})["combine_counts"] = [["52/1 & 54/1 CTN", "52/1 CTN", "54/1 CTN"]]
    try:
        rep = _contracts.yarn_shortage(con, cfg3, END)
        assert rep.columns[0] == "Count"
    finally:
        cfg3.data.get("yarn", {}).pop("combine_counts", None)


def test_v015_sales_drill_month_and_history(env):
    cfg, con = env
    # the drill follows the given month (#28), not always the current one
    aug = _webfin.month_sales(con, as_of=date(2026, 8, 15), with_history=True)
    assert aug["month"] == "2026-08" and aug["count"] == len(aug["rows"])
    assert _webfin.month_sales(con, month="2026-09")["month"] == "2026-09"
    hist = aug["history"]
    assert hist and all(set(h) >= {"month", "total", "count"} for h in hist)
    assert hist == sorted(hist, key=lambda h: h["month"], reverse=True)   # newest month first


def test_v015_closing_selection_and_quality(env):
    cfg, con = env
    from smart_layer.textile import closing as _cl
    from smart_layer.textile.method import Method
    rws = _cl.contract_rows(con, Method(cfg["closing"]), END)
    party = rws[0]["party"]
    cc = _cl.closing_counts(con, cfg, END, party)
    assert cc["yarns"] and isinstance(cc["qualities"], list) and "third_party" in cc
    # count panel shows only this party's yarns
    all_rows = [r for r in rws if r.get("party") == party]
    assert {y["yarn"] for y in cc["yarns"]} <= {str(r["yarn"]) for r in all_rows}
    # closing for one quality keeps only that quality's contracts
    if cc["qualities"]:
        q = cc["qualities"][0]
        rep = _cl.contract_closing(con, cfg, END, party, None, q)
        full = _cl.contract_closing(con, cfg, END, party)
        assert len(rep.rows) <= len(full.rows)
        assert _cl.closing_counts(con, cfg, END, party, q)["quality"] == q

    # FIX 1: cross-party deliveries are filtered by the selected quality. Find a party with >=2 qualities, inject a
    # cross-party dispatch for a contract of one quality, and confirm the OTHER quality excludes it.
    pq = {}
    for r in rws:
        pq.setdefault(r["party"], {}).setdefault(r["quality"], set()).add(str(r["contract"]))
    cand = next(((p, qs) for p, qs in pq.items() if len([k for k in qs if k]) >= 2), None)
    if cand:
        p2, qs = cand
        quals = [k for k in qs if k]
        q_a, q_b = quals[0], quals[1]
        cno = sorted(qs[q_a])[0]
        other = con.execute("SELECT party_code FROM contracts WHERE party_code IS NOT NULL AND contract_no <> ? LIMIT 1", (cno,)).fetchone()[0]
        con.execute("""INSERT INTO fabric_dispatches (doc_no, dispatch_date, contract_no, quality, party_code, direction, meters)
                       VALUES ('TPTEST', ?, ?, ?, ?, 'out', 500)""", (END.isoformat(), cno, q_a, other))
        con.commit()
        try:
            tp_all = {str(e["contract"]) for e in _cl.closing_counts(con, cfg, END, p2)["third_party"]}
            tp_a = {str(e["contract"]) for e in _cl.closing_counts(con, cfg, END, p2, q_a)["third_party"]}
            tp_b = {str(e["contract"]) for e in _cl.closing_counts(con, cfg, END, p2, q_b)["third_party"]}
            assert cno in tp_all and cno in tp_a and cno not in tp_b       # quality filters the cross-party table
        finally:
            con.execute("DELETE FROM fabric_dispatches WHERE doc_no = 'TPTEST'")
            con.commit()


def test_v015_cost_per_metre_and_yarn_shortage(env):
    cfg, con = env
    cm = _repfin.cost_per_metre(con, cfg, END)
    assert cm.columns and cm.rows is not None
    ys = _contracts.yarn_shortage(con, cfg, END)
    assert ys.columns[0] == "Count"


def test_v015_scoreboard_live_shift_excludes_idle(env):
    cfg, con = env
    from smart_layer.web import data as _wd
    assert con.execute("SELECT COUNT(*) FROM loom_monitor_live").fetchone()[0] > 0   # live shift synced
    s = _wd._scb_today(con, cfg)
    assert s.get("scb_live") is True and s.get("scb_shift")                           # the CURRENT running shift
    assert s.get("scb_idle_excluded", 0) >= 2                                         # looms 10 & 12 are idle
    assert 50 <= s["scb_eff_today"] <= 100                                            # a sane shed efficiency
    # excluding the idle looms raises the shed efficiency vs including them
    incl = con.execute("""SELECT 100.0*SUM(elapsed_sec-stopped_sec)/NULLIF(SUM(elapsed_sec),0) FROM loom_monitor_live""").fetchone()[0]
    assert s["scb_eff_today"] >= round(incl, 1)
    # loom codes come off the monitor as FLOATs ('10.0', '2.0'); the number is still parsed, and exclude_looms
    # matches by number even for a float-coded loom
    assert (_wd._loom_num("10.0"), _wd._loom_num("2.0"), _wd._loom_num("L07"), _wd._loom_num("LOOM-12")) == (10, 2, 7, 12)
    con.execute("""INSERT INTO loom_monitor_live (loom_code, shift, shift_start, elapsed_sec, stopped_sec, picks,
                   deliveries, rpm, picks_per_inch, updated_at)
                   VALUES ('47.0', 2, '2026-10-02 18:00:00', 12960, 200, 9000, 1, 540, 88, '2026-10-02 21:36:00')""")
    con.commit()
    base = s["scb_looms"]                                     # running looms before the float-coded one was added
    assert _wd._scb_today(con, cfg)["scb_looms"] == base + 1  # loom 47 (running) is now counted
    cfg.data.setdefault("monitor", {})["exclude_looms"] = [47]
    try:
        after = _wd._scb_today(con, cfg)
        assert after["scb_looms"] == base and after["scb_eff_today"] is not None   # excluded by number despite '47.0'
    finally:
        cfg.data["monitor"]["exclude_looms"] = []
        con.execute("DELETE FROM loom_monitor_live WHERE loom_code = '47.0'")
        con.commit()


def test_v015_cost_headline_closed_months(env):
    cfg, con = env
    h = _repfin.cost_headline(con, cfg, END)                    # END = 2026-10-02, so Oct is the running month
    assert h["closed"] is True
    assert h["month"] < END.strftime("%Y-%m")                  # headline is a COMPLETE closed month, never the running one
    assert h["avg_no_sizing"] is not None and h["closed_months"] >= 1
    assert h["low"]["no_sizing"] <= h["high"]["no_sizing"]
    # the report has a Closed? column and marks the running month "not closed"
    cm = _repfin.cost_per_metre(con, cfg, END)
    assert cm.columns[-1] == "Closed?"
    run = [r for r in cm.rows if r[0] == END.strftime("%Y-%m")]
    assert run and run[0][-1] == "not closed"
    assert any("last closed month" in line for line in cm.summary)
    # the closed rule is configurable: require a bucket that is never posted -> nothing is closed
    cfg.data.setdefault("cost", {})["closed_requires"] = ["power", "sizing", "store", "payroll", "other"]
    try:
        h2 = _repfin.cost_headline(con, cfg, END)
    finally:
        cfg.data["cost"]["closed_requires"] = ["store", "payroll"]
    assert h2.get("closed") in (False, None)


from smart_layer import erp_edits as _edits   # noqa: E402


def test_v0156_edits_list_flags_and_names(env):
    cfg, con = env
    assert _edits.installed(con)
    st = _edits.status(con)
    assert st["installed"] and st["events"] >= 8
    L = _edits.list_edits(con, cfg)
    assert L["installed"] and L["rows"]
    docs = {r["doc"].split(" · ")[0] for r in L["rows"]}
    assert "BP-00412" in docs and "ISS-5233" in docs            # the planted voucher edit and the deleted store doc
    byd = {}
    for r in L["rows"]:
        byd.setdefault(r["doc"].split(" · ")[0], []).append(r)
    # the 24-day-old BP voucher: its line edits are flagged as old-entry AND amount changes, with the amount shown
    bp_lines = [r for r in byd["BP-00412"] if r["action"] == "U" and r["summary"]]
    assert bp_lines and all(r["is_old_edit"] and r["touches_amount"] for r in bp_lines)
    assert any("42,000 → 27,000" in r["summary"] for r in bp_lines)
    # the account change resolves the account CODE to its name in the summary
    sv = [r for r in L["rows"] if r["doc"].startswith("SV-00455") and r["summary"]]
    assert sv and "(" in sv[0]["summary"] and r"→" in sv[0]["summary"]
    assert sv[0]["touches_account"]
    # the store-doc delete is flagged as a delete; a same-day journal edit is NOT old
    assert any(r["is_delete"] for r in byd["ISS-5233"])
    jv = [r for r in L["rows"] if r["doc"].startswith("JV-00460")]
    assert jv and not any(r["is_old_edit"] for r in jv)
    # detail: every changed field old -> new
    bp_line1 = next(r for r in bp_lines if "42,000" in r["summary"])
    d = _edits.detail(con, cfg, bp_line1["event_id"])
    assert d and d["fields"] and d["fields"][0]["material"]


def test_v0156_edits_pairing_and_header_user(env):
    cfg, con = env
    L = _edits.list_edits(con, cfg)
    # a delete + insert of the SAME store line within a minute is flagged as a re-entered pair (not a scary delete)
    pairs = [r for r in L["rows"] if r.get("paired")]
    assert len(pairs) >= 2 and {r["action"] for r in pairs} == {"I", "D"}
    # a voucher LINE has no user of its own; it is attributed to its header's user
    line = next(r for r in L["rows"] if r["doc"].startswith("BP-00412 · line"))
    assert line["user"] == "acc2"
    # filters: by user, by action, by document
    assert all(r["user"] == "acc1" for r in _edits.list_edits(con, cfg, user="acc1")["rows"])
    assert all(r["is_delete"] for r in _edits.list_edits(con, cfg, action="D")["rows"])
    assert _edits.list_edits(con, cfg, doc="BP-00412")["total"] >= 1


def test_v0156_edits_by_user_attributes_lines(env):
    cfg, con = env
    rows = _edits.by_user(con, cfg=cfg)
    users = {r["user"]: r for r in rows}
    assert "—" not in users                                     # NULL-user lines are attributed to their header user
    assert users["acc2"]["edits"] >= 1                          # acc2 keyed the BP voucher + its lines
    assert any(r["deletes"] for r in rows)                      # the store deletes are counted


def test_v0156_edits_incremental_cursor(env, tmp_path):
    # a fresh store synced twice adds no duplicates; a brand-new event is picked up on the next sync
    cfg, _ = env
    e = erp.ERP(*erp.connect(cfg["erp"], cfg.base_dir), erp.Mapping(cfg.path(cfg["erp"]["mapping"])),
                cfg["sources"], cfg.base_dir)
    con2 = store.open_store(tmp_path / "s2.db")
    store.sync(con2, e, 400, log=lambda *_: None)
    n1 = con2.execute("SELECT COUNT(*) FROM erp_change_events").fetchone()[0]
    cur1 = store.get_state(con2, "edits_cursor_erp_change_events")
    store.sync(con2, e, 400, log=lambda *_: None)               # re-sync: cursor means nothing re-read
    assert con2.execute("SELECT COUNT(*) FROM erp_change_events").fetchone()[0] == n1
    assert store.get_state(con2, "edits_cursor_erp_change_events") == cur1
    con2.close()


def test_v0156_edit_pushes_and_morning_line(env):
    cfg, con = env
    sent = []
    import smart_layer.pushes as P
    orig = P.notify.send
    P.notify.send = lambda cfg, title, body, **k: sent.append((title, body, k))
    try:
        store.set_state(con, "edit_push_cursor", None)          # first run seeds the cursor, pushes nothing
        assert _pushes.edit_pushes(cfg, con) == 0 and not sent
        store.set_state(con, "edit_push_cursor", 0)             # now push the whole (small) demo history
        assert _pushes.edit_pushes(cfg, con) == 1
        title, body, k = sent[-1]
        assert "delete" in title.lower() and k.get("priority") == "high"
        assert "BP-00412" in body and "ISS-5233" in body
        # one voucher's header + two line edits collapse to a single line in the push
        assert body.count("BP-00412") == 1
        assert _pushes.edit_pushes(cfg, con) == 0               # cursor advanced: nothing new
        line = _pushes._edits_by_user_line(con, END, cfg)
        assert line.startswith("ERP edits today:") and "acc2" in line
    finally:
        P.notify.send = orig


def test_v0156_edits_not_installed(tmp_path):
    # a mill without the change-history layer: the entities are empty, so the tab is off and nothing errors
    con = store.open_store(tmp_path / "empty.db")
    try:
        assert not _edits.installed(con)
        assert _edits.list_edits(con, None)["installed"] is False
        assert _edits.status(con)["installed"] is False
        assert _edits.by_user(con) == []
        assert _edits.detail(con, None, 1) is None
    finally:
        con.close()


def test_v0156_edits_web_route(env):
    cfg, con = env
    app = web_server.App(cfg)
    code, body, _ctype, _h = web_server.get_response(app, "/api/edits", "")
    assert code == 200 and body["installed"] and body["rows"]
    # detail route
    eid = body["rows"][0]["event_id"]
    code2, body2, _c, _hh = web_server.get_response(app, "/api/edits/detail", f"id={eid}")
    assert code2 == 200 and body2["event_id"] == eid
    # the dashboard payload flags the tab on
    assert web_data.build(con, cfg, END)["edits_installed"] is True


def test_v0160_edits_header_fallback_and_user(env):
    cfg, con = env
    L = _edits.list_edits(con, cfg)
    by = {}
    for r in L["rows"]:
        by.setdefault(r["doc"].split(" · ")[0], []).append(r)
    # a LINE-ONLY edit on an old voucher with NO header event resolves its doc/date/user from erp_doc_headers,
    # and because the resolved date is old, is_old_edit fires (fix #1)
    pv = by.get("PV-00470")
    assert pv and pv[0]["doc_date"] and pv[0]["is_old_edit"] and pv[0]["user"] == "acc3"
    # the store delete was keyed by the DB owner (DOSTEX) -> display user falls back to the PC/host, stripped (fix #3)
    iss = next(r for r in L["rows"] if r["doc"].startswith("ISS-5233"))
    assert iss["user"] == "DESKTOP-KUMU41H"
    # an IC (item swap) change is treated as a material/account change (fix #4)
    icrow = next((r for r in L["rows"] if r["doc"].startswith("ISS-5240") or "SP-003" in (r["doc"] or "")), None)
    # the DDT delete carries an IC change -> touches_account
    dd = [r for r in L["rows"] if r["is_delete"] and r["touches_account"]]
    assert dd
    # doc filter matches the RESOLVED document (lines included), not just header rows (fix #2)
    pv_hits = _edits.list_edits(con, cfg, doc="PV-00470")
    assert pv_hits["total"] >= 1 and all("PV-00470" in r["doc"] for r in pv_hits["rows"])
    # user filter matches the resolved display user
    assert _edits.list_edits(con, cfg, user="acc3")["total"] >= 1


# ---- 0.16.0 feature coverage (items 29, 30, 32) ----------------------------------------------------------------
from smart_layer.web import looms as _looms, purchasing as _purch   # noqa: E402


def test_v0160_loom_efficiency_and_scoreboard(env):
    cfg, con = env
    lo, hi = _d(2026, 9, 3), END
    eff = _looms.efficiency(con, cfg, lo, hi)
    assert eff["rows"] and eff["totals"]["erp_eff"] is not None
    r0 = eff["rows"][0]
    assert r0["std_rpm"] is not None                      # loom standards (30c) feed the standard-RPM column
    assert eff["scoreboard_available"]
    # day-by-day drill for one loom
    ld = _looms.loom_days(con, cfg, r0["code"], lo, hi)
    assert ld["rows"] and ("A" in ld["rows"][0]["erp_shifts"] or ld["rows"][0]["erp_eff"] is not None)
    # scoreboard for a date: day/night split on BOTH sides (demo maps the monitor's shift)
    sd = _looms.scoreboard_date(con, cfg, END)
    assert sd["scb_has_shifts"] and set(sd["scb_shifts"]) >= {"day", "night"}
    assert sd["totals"]["scb"]["day"] is not None and sd["totals"]["erp_all"] is not None
    # the compare trend has per-date ERP + scoreboard efficiency
    tr = _looms.trend(con, cfg, lo, hi)
    assert tr["rows"] and any(x["erp"] is not None for x in tr["rows"])
    # the CSV/PDF report builds
    rep = _looms.efficiency_report(con, cfg, lo, hi)
    assert rep.columns[0] == "Loom" and rep.rows


def test_v0160_purchasing(env):
    cfg, con = env
    p = _purch.overview(con, cfg)
    assert p["installed"] and p["rows"]
    t = p["tiles"]
    assert t["po_lines"] >= 1 and (t["rate_diffs"] + t["qty_diffs"]) >= 1    # a planted PO≠GRN difference exists
    assert any(r["diff"] for r in p["rows"])
    assert any(not r["received"] for r in p["rows"])                         # a pending (unreceived) PO line
    # filters
    assert all(r["diff"] for r in _purch.overview(con, cfg, only="diff")["rows"])
    assert all(not r["received"] for r in _purch.overview(con, cfg, only="pending")["rows"])
    assert p["pending_indents"]                                             # an indent with no PO
    # item drill
    item = p["rows"][0]["item_code"]
    d = _purch.item_pos(con, item)
    assert d["installed"] and d["rows"]


def test_v0160_item_heads_in_store_reports(env):
    cfg, con = env
    from smart_layer.reports import store_extra
    # head column + a "By head" summary line appear once items carry heads (item 30e)
    rep = store_extra.store_expense(con, cfg, _d(2026, 9, 1), END)
    assert "Head" in rep.columns
    assert any(str(s).startswith("By head:") for s in rep.summary)
    un = store_extra.unused_stock(con, cfg, END)
    assert "Head" in un.columns


def test_v0160_set_password_and_grants_missing(tmp_path):
    # set-password re-seals into .env (plain off-Windows), preserving other keys
    import getpass
    from types import SimpleNamespace
    from smart_layer import cli, config as _cfg
    (tmp_path / "config.toml").write_text(
        "[company]\nname='M'\n[erp]\ndriver='sqlite'\npath='demo.db'\nmapping='m.toml'\n[store]\npath='s.db'\n")
    (tmp_path / "m.toml").write_text('[mapping]\nname="m"\n[entities.items]\nsql="SELECT 1 AS item_code, 1 AS item_name FROM (SELECT 1)"\n')
    (tmp_path / ".env").write_text("# c\nERP_PASSWORD=old\nKEEP=1\n")
    cfg = _cfg.load(tmp_path / "config.toml")
    seq = iter(["newpw", "newpw"])
    getpass.getpass = lambda *a, **k: next(seq)
    assert cli.cmd_set_password(cfg, SimpleNamespace(var="ERP_PASSWORD")) == 0
    txt = (tmp_path / ".env").read_text()
    # off Windows the value is stored in the clear; on Windows it is DPAPI-sealed (ERP_PASSWORD=DPAPI:...)
    assert ("ERP_PASSWORD=newpw" in txt or "ERP_PASSWORD=DPAPI:" in txt) and "KEEP=1" in txt and "# c" in txt


def test_v0160_scoreboard_web_routes(env):
    cfg, con = env
    app = web_server.App(cfg)
    for p, q in [("/api/scoreboard/live", ""), ("/api/scoreboard", "date=2026-10-02"),
                 ("/api/scoreboard/trend", ""), ("/api/looms/efficiency", "from=2026-09-03&to=2026-10-02"),
                 ("/api/purchasing", ""), ("/api/looms/days", "loom=L01&from=2026-09-25&to=2026-10-02")]:
        code, body, _c, _h = web_server.get_response(app, p, q)
        assert code == 200 and isinstance(body, dict)
    # the dashboard payload flags the Purchasing tab on
    assert web_data.build(con, cfg, END)["purchasing_installed"] is True


def test_v0161_config_warnings(tmp_path):
    from smart_layer import cli, config as _cfg
    # no dashboard password + ntfy enabled but no topic -> two clear warnings
    (tmp_path / "config.toml").write_text(
        '[company]\nname="M"\n[erp]\ndriver="sqlite"\npath="d.db"\nmapping="m.toml"\n[store]\npath="s.db"\n'
        '[serve]\nhost="0.0.0.0"\nport=8080\n[notify]\nchannels=["console","ntfy"]\n')
    cfg = _cfg.load(tmp_path / "config.toml")
    w = cli._config_warnings(cfg)
    assert any("DASHBOARD_PASSWORD missing" in x for x in w)
    assert any("NTFY_TOPIC missing" in x for x in w)
    # with a password set and a proper ntfy topic, no warnings
    (tmp_path / "config.toml").write_text(
        '[company]\nname="M"\n[erp]\ndriver="sqlite"\npath="d.db"\nmapping="m.toml"\n[store]\npath="s.db"\n'
        '[serve]\nhost="0.0.0.0"\npassword="secretpw"\n[notify]\nchannels=["console","ntfy"]\nntfy_topic="a-long-random-topic-name"\n')
    assert cli._config_warnings(_cfg.load(tmp_path / "config.toml")) == []
    # ntfy not a channel -> a short/absent topic is not flagged
    (tmp_path / "config.toml").write_text(
        '[company]\nname="M"\n[erp]\ndriver="sqlite"\npath="d.db"\nmapping="m.toml"\n[store]\npath="s.db"\n'
        '[serve]\npassword="x"\n[notify]\nchannels=["console"]\n')
    assert cli._config_warnings(_cfg.load(tmp_path / "config.toml")) == []


# ---- 0.16.1: balance wording, physical rejection count, ERP system routines -------------------

def test_v0161_balance_phrase_reads_as_a_sentence():
    # negative = the party owes the mill; positive = the mill holds the party's yarn; ~0 = square
    assert "owes the mill" in closing.balance_phrase("Kohinoor", -12.3, yarn="40 CARDED")
    assert "40 CARDED" in closing.balance_phrase("Kohinoor", -12.3, yarn="40 CARDED")
    held = closing.balance_phrase("Lahore Textile", 8.0)
    assert "mill holds" in held and "Lahore Textile's" in held
    assert "square" in closing.balance_phrase("A", 0.0)
    assert closing.balance_side(-5) == "owes mill"
    assert closing.balance_side(5) == "mill holds"
    assert closing.balance_side(0) == "square"
    # a value is appended in brackets when given
    assert "Rs 1,000" in closing.balance_phrase("A", -2, value=1000)


def test_v0161_system_routine_grouping():
    from smart_layer import erp_edits
    s = {"system_modules": {"UPDATE_AVG_RATE"}, "system_columns": {"AVG_RATE"}}
    # by module
    assert erp_edits._is_system("UPDATE_AVG_RATE", "U", ["QTY"], s) is True
    # by column-only update (AVG_RATE only)
    assert erp_edits._is_system("SOMEFORM", "U", ["AVG_RATE"], s) is True
    # a real edit that also touches another column is NOT a system routine
    assert erp_edits._is_system("SOMEFORM", "U", ["AVG_RATE", "RATE"], s) is False
    # an insert is never collapsed by column
    assert erp_edits._is_system("SOMEFORM", "I", ["AVG_RATE"], s) is False
    # a run of adjacent system rows collapses to one grouped line
    rows = [{"event_id": i, "at": "2026-11-01T0%d:00" % i, "table": "IM", "area": "Item", "user": "DOSTEX",
             "is_system": True, "module": "UPDATE_AVG_RATE", "system_cols": ["AVG_RATE"], "fields": 1} for i in range(5)]
    rows.append({"event_id": 9, "at": "2026-11-01T06:00", "table": "TM", "area": "Voucher", "user": "AMIR",
                 "is_system": False, "module": "VOUCHER", "fields": 3})
    out = erp_edits._collapse_system(rows)
    grouped = [r for r in out if r.get("grouped")]
    assert len(out) == 2 and len(grouped) == 1
    assert grouped[0]["count"] == 5 and "5 lines" in grouped[0]["summary"] and "AVG_RATE" in grouped[0]["summary"]


def test_v0161_physical_rejection_count_feeds_the_review(env):
    cfg, con = env
    from smart_layer import manual
    # a contract+quality with ERP fabric stock (not CON-41, which other closing tests pin)
    row = con.execute(
        "SELECT contract_no, quality, SUM(c_m)+SUM(cp_m) FROM fabric_stock "
        "WHERE contract_no <> 'CON-41' GROUP BY contract_no, quality "
        "HAVING SUM(meters) > 0 ORDER BY SUM(meters) DESC LIMIT 1").fetchone()
    assert row, "demo needs some fabric stock to count"
    cno, quality = str(row[0]), str(row[1] or "")
    key = f"{cno}|{quality}"
    # count the goods in hand with an explicit REJECTION, as-on today so nothing rolls forward
    manual.set_value(con, "fabric_count", key, "as_on", END.toordinal(), "tester", note=END.isoformat())
    manual.set_value(con, "fabric_count", key, "REJECTION", 1234.0, "tester")
    rev = closing.fabric_count_review(con, cfg, END)
    hit = next((r for r in rev["rows"] if r["contract"] == cno and (r["quality"] or "") == quality), None)
    assert hit is not None
    assert hit["physical_rejection"] == 1234            # as-on today -> exactly what was counted
    assert hit["as_on"] == END.isoformat()
    assert hit["difference"] == 1234 - hit["erp_rejection"]
    assert rev["counted"] >= 1


# ---- 0.17.0: gully / rewind yarn -------------------------------------------------------------

def test_v0170_gully_route_and_cone_weight():
    from smart_layer.textile import gully
    g = {"reuse_to": {"30/1 PC": "sizing", "default": "weft"},
         "cone_weight_kg": 0.060, "cone_weight_by_yarn": {"52/1 CTN": 0.07}}
    # route: named yarn (as a substring) -> its route; anything else -> the default
    assert gully.route_for(g, "30/1 PC CARDED") == "sizing"
    assert gully.route_for(g, "52/1 CTN") == "weft"
    assert gully.route_for({"reuse_to": {"default": "weft"}}, "anything") == "weft"
    # cone (tube) weight: per-yarn override, else the scalar default
    assert gully.cone_weight(g, "52/1 CTN") == pytest.approx(0.07)
    assert gully.cone_weight(g, "30/1 PC") == pytest.approx(0.060)


def test_v0170_gully_auto_rewound_match():
    from smart_layer.textile import gully
    line = {"date": "2026-05-10", "yarn": "30/1 PC", "kg": 1000.0}
    near = [{"date": "2026-05-12", "yarn": "30/1 PC", "kg": 1005.0}]      # within 3 days, within 1%
    far = [{"date": "2026-05-20", "yarn": "30/1 PC", "kg": 1000.0}]       # outside the window
    other = [{"date": "2026-05-10", "yarn": "40/1 CTN", "kg": 1000.0}]    # different yarn
    assert gully._auto_rewound(line, near, 3, 1.0) is False               # matches a fresh receipt -> fresh
    assert gully._auto_rewound(line, far, 3, 1.0) is True                 # no match -> rewound gully
    assert gully._auto_rewound(line, other, 3, 1.0) is True


def test_v0170_gully_net_of_tubes_and_balance(env):
    cfg, con = env
    from smart_layer import manual
    from smart_layer.textile import gully
    saved = cfg.data.get("gully", {})
    cfg.data["gully"] = {**config.DEFAULTS["gully"], "reuse_to": {"default": "weft"}, "cone_weight_kg": 0.060}
    try:
        sets0 = gully.gully_sets(con, cfg, END)
        set0 = next((r for r in sets0["rows"] if r["slip_kg"] > 0), None)
        assert set0, "demo needs a sized set with gully"
        # the gully-cone count comes from the set record; the empty tubes net off the gully weight
        assert set0["cones"] >= 0
        assert set0["tube_kg"] == pytest.approx(set0["cones"] * 0.060, abs=0.05)
        assert set0["net_kg"] == pytest.approx(max(0.0, set0["slip_kg"] - set0["tube_kg"]), abs=0.2)
        # the gate-app count is kept only as a cross-check (does not change the net)
        manual.set_value(con, "gully_set", str(set0["set_no"]), "cones", set0["cones"] + 9, "tester")
        r1 = next(r for r in gully.gully_sets(con, cfg, END)["rows"] if str(r["set_no"]) == str(set0["set_no"]))
        assert r1["gate_cones"] == set0["cones"] + 9 and r1["cones_diff"] == -9
        assert r1["net_kg"] == pytest.approx(set0["net_kg"], abs=0.1)     # net unchanged by the cross-check
        # book balance per yarn = opening + gully net - rewound re-used (0 on weft until marked) - waste (0)
        bal = gully.rewind_balance(con, cfg, END)
        yr = next((r for r in bal["rows"] if r["gully_returned_kg"] > 0), None)
        assert yr and yr["route"] == "weft"
        manual.set_value(con, "gully_yarn", yr["yarn"], "opening_kg", 500, "tester")
        manual.set_value(con, "gully_yarn", yr["yarn"], "physical_kg", 100, "tester")
        bal2 = gully.rewind_balance(con, cfg, END)
        yr2 = next(r for r in bal2["rows"] if r["yarn"] == yr["yarn"])
        assert yr2["book_balance_kg"] == 500 + yr2["gully_returned_kg"] - yr2["rewound_reused_kg"] - yr2["waste_kg"]
        assert yr2["physical_kg"] == 100
        assert yr2["difference_kg"] == yr2["book_balance_kg"] - 100
    finally:
        cfg.data["gully"] = saved


def test_v0170_yarn_weight_flags():
    from smart_layer.textile import gully

    class _Cfg:
        def __init__(self, g):
            self.g = {"gully": g}

        def __getitem__(self, k):
            return self.g.get(k, {})

    # the store's `kg` is already sanitised by the mapping (bags x bag_kg substituted for a bad ERP weight); the
    # check must run on the ERP's own weight in raw_kg, where present, or it can never see a missing weight.
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE yarn_movements (move_date TEXT, doc_no TEXT, move_type TEXT, yarn_name TEXT, "
                "contract_no TEXT, bags REAL, kg REAL, raw_kg REAL)")
    con.executemany("INSERT INTO yarn_movements VALUES (?,?,?,?,?,?,?,?)", [
        ("2026-01-10", "IGP-1", "party_receipt", "30/1 PC", "C1", 10, 453.6, 455.0),   # fine
        ("2024-01-05", "IGP-89", "party_receipt", "30/1 PC", "C1", 55, 2494.8, 0),     # kg substituted, raw ERP = 0
        ("2026-05-11", "IGP-543", "party_receipt", "30/1 PC", "C1", 10, 453.6, 5896.8),  # kg substituted, raw ERP huge
        ("2026-06-01", "IGP-NR", "party_receipt", "30/1 PC", "C1", 8, 362.9, None),    # no raw -> falls back to kg (fine)
    ])
    con.commit()
    cfg = _Cfg({"bag_kg": 45.359237, "weight_ratio_low": 0.5, "weight_ratio_high": 2.0})
    out = gully.yarn_weight_flags(con, cfg, date(2026, 10, 2))
    docs = {r["doc_no"]: r["issue"] for r in out["rows"]}
    assert out["count"] == 2
    assert docs["IGP-89"] == "weight missing"       # raw_kg 0, even though the stored kg is plausible
    assert docs["IGP-543"] == "weight too high"     # raw_kg huge, even though the stored kg is plausible
    assert "IGP-1" not in docs and "IGP-NR" not in docs


def test_v0172_per_yarn_bag_weight():
    from smart_layer.textile import gully

    class _Cfg:
        def __init__(self, g):
            self.g = {"gully": g}

        def __getitem__(self, k):
            return self.g.get(k, {})

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE yarn_movements (move_date TEXT, doc_no TEXT, gatepass_no TEXT, move_type TEXT, "
                "yarn_name TEXT, contract_no TEXT, bags REAL, kg REAL, raw_kg REAL)")
    # 50 lb bags: 10 bags ~ 224 kg. Against the default 45.36 yardstick the ratio is ~0.49 -> falsely "too low".
    con.executemany("INSERT INTO yarn_movements VALUES (?,?,?,?,?,?,?,?,?)", [
        ("2026-05-02", "V1", "IGP-900", "party_receipt", "120/1 VISCOSE", "C9", 10, 224.0, 224.0)])
    con.commit()
    g = {"bag_kg": 45.359237, "weight_ratio_low": 0.5, "weight_ratio_high": 2.0}
    assert gully.yarn_weight_flags(con, _Cfg(g), date(2026, 10, 2))["count"] == 1   # mis-flagged without the override
    g2 = {**g, "bag_kg_by_yarn": {"120/1 VISCOSE": 22.68}}
    out = gully.yarn_weight_flags(con, _Cfg(g2), date(2026, 10, 2))
    assert out["count"] == 0                                                        # correct with the per-yarn bag weight
    assert gully.bag_kg_for(g2, "120/1 VISCOSE CARDED") == pytest.approx(22.68)     # substring match
    assert gully.bag_kg_for(g2, "30/1 PC") == pytest.approx(45.359237)             # default


def test_v0172_weblogic_ignores_server_heartbeat():
    from smart_layer import erp_edits as E
    from datetime import datetime as _dt

    def ep(s):
        return _dt.fromisoformat("2026-11-07 15:00:" + s).timestamp()
    wl = E._WebLogicLog({"weblogic_access_log": "x", "server_hosts": ["DESKTOP-KUMU41H", "127.0.0.1", "localhost"]})
    # a dense client (.177) plus the loopback heartbeat in the same window
    wl._events = sorted([(ep("30"), "192.168.18.177"), (ep("31"), "192.168.18.177"), (ep("33"), "192.168.18.177"),
                         (ep("02"), "127.0.0.1"), (ep("59"), "127.0.0.1")])
    wl.enabled = True
    # even though a heartbeat (:02) is nearest to :01, the busy client IP wins
    assert wl.pc_for("2026-11-07 15:00:01") == "192.168.18.177"
    # a window with only the server heartbeat returns the loopback (shown later as "Server (local/AnyDesk)")
    wl._events = [(ep("00"), "127.0.0.1")]
    del wl._times
    assert wl.pc_for("2026-11-07 15:00:00") == "127.0.0.1"


def test_v0172_weblogic_range_reaches_newest_rotated_file(tmp_path):
    from smart_layer import erp_edits as E
    # many rotated files going back a year; the NEEDED day (7-Nov) is only in the last-by-name file. A global cap that
    # fills from the oldest files would never reach it (the 0.17.2 regression) - the date-range load must still find it.
    import os
    import time
    logdir = tmp_path / "logs"
    logdir.mkdir()
    # 00000..00086: one older day each (Jan onward), packed with loopback heartbeat lines
    for n in range(87):
        day = f"{(n % 28) + 1:02d}/{['Jan','Feb','Mar','Apr','May','Jun'][(n // 28) % 6]}/2026"
        lines = [f"127.0.0.1 - - [{day}:0{h % 10}:00:00 +0500] \"POST /forms/lservlet HTTP/1.1\" 200 1\n"
                 for h in range(200)]
        (logdir / f"access.log{n:05d}").write_text("".join(lines))
    # the newest file (current, no suffix) holds 7-Nov with heavy client traffic
    now = ["192.168.18.177 - - [07/Nov/2026:15:30:0%d +0500] \"POST /forms/lservlet HTTP/1.1\" 200 1\n" % (i % 10)
           for i in range(300)]
    (logdir / "access.log").write_text("".join(now))
    # set mtimes so the current file is newest and the rotated ones get older as the number grows
    base = time.time()
    os.utime(logdir / "access.log", (base, base))
    for n in range(87):
        t = base - (n + 1) * 86400
        os.utime(logdir / f"access.log{n:05d}", (t, t))

    s = {"weblogic_access_log": str(logdir / "access.log*"),
         "server_hosts": ["127.0.0.1", "localhost"], "log_match_window_seconds": 180}
    lo, hi = E._log_bounds(["2026-11-07 15:30:00"], s)
    wl = E._WebLogicLog(s, lo, hi)
    assert wl.pc_for("2026-11-07 15:30:00") == "192.168.18.177"   # found despite 87 older rotated files


def test_v0171_opening_date_bounds_the_balance(env):
    cfg, con = env
    from smart_layer import manual
    from smart_layer.textile import gully
    saved = cfg.data.get("gully", {})
    cfg.data["gully"] = {**config.DEFAULTS["gully"], "reuse_to": {"default": "weft"}}
    try:
        # pick a yarn that has gully, and the dates of its sets
        yarn = next((r["yarn"] for r in gully.rewind_balance(con, cfg, END)["rows"] if r["gully_returned_kg"] > 0), None)
        assert yarn
        dates = sorted(d for (d,) in con.execute(
            "SELECT DISTINCT s.set_date FROM sized_sets s JOIN contracts c ON c.contract_no = s.contract_no "
            "WHERE COALESCE(s.gullay_kg,0) > 0") if d)
        assert len(dates) >= 2
        # opening dated at the latest set date: only gully AFTER it counts, so gully_returned drops vs no-date
        full = next(r for r in gully.rewind_balance(con, cfg, END)["rows"] if r["yarn"] == yarn)
        manual.set_value(con, "gully_yarn", yarn, "opening_kg", 1000, "t")
        manual.set_value(con, "gully_yarn", yarn, "opening_at_iso",
                         date.fromisoformat(dates[-1]).toordinal(), "t", note=dates[-1])
        bounded = next(r for r in gully.rewind_balance(con, cfg, END)["rows"] if r["yarn"] == yarn)
        assert bounded["opening_at"] == dates[-1]
        assert bounded["gully_returned_kg"] < full["gully_returned_kg"]   # earlier sets no longer counted
        assert bounded["opening_kg"] == 1000
    finally:
        cfg.data["gully"] = saved


def test_v0171_line_tick_keyed_by_date_doc_yarn(env):
    cfg, con = env
    from smart_layer import manual
    from smart_layer.textile import gully
    saved = cfg.data.get("gully", {})
    # route every yarn to sizing so to_sizer lines appear as tickable
    cfg.data["gully"] = {**config.DEFAULTS["gully"], "reuse_to": {"default": "sizing"}}
    try:
        lines = gully.rewind_lines(con, cfg, END)
        assert lines and all("line_id" in ln for ln in lines)
        # the id is date|doc|yarn, so two lines sharing a doc_no on different dates get different ids
        ln = lines[0]
        assert ln["line_id"] == f"{str(ln['date'])[:10]}|{ln['doc_no']}|{gully._norm(ln['yarn'])}"
        # a tick on one line_id only flips that line
        manual.set_value(con, "gully_line", ln["line_id"], "rewound", (0 if ln["rewound"] else 1), "t")
        after = {x["line_id"]: x for x in gully.rewind_lines(con, cfg, END)}
        assert after[ln["line_id"]]["rewound"] == (not ln["rewound"])
        assert after[ln["line_id"]]["owner_set"] is not None
        others = [x for lid, x in after.items() if lid != ln["line_id"] and x["doc_no"] == ln["doc_no"]]
        for o in others:                                   # same doc_no, different date -> untouched
            assert o["owner_set"] is None
    finally:
        cfg.data["gully"] = saved


# ---- 0.17.4: issue-value audit - no false ACTION for ERP average-rate re-rating ----------------

def test_v0174_rerate_signals(env):
    cfg, con = env
    from smart_layer.audit import edits as ae
    a = audit.Audit(con, cfg, END, END)
    since, now = "2026-11-01 00:00:00", "2026-11-30 00:00:00"
    ids = [(99990001,), (99990002,), (99990003,)]
    try:
        con.executemany("INSERT INTO erp_change_events (event_id, changed_at, table_name, action, row_key, module) "
                        "VALUES (?,?,?,?,?,?)",
                        [(99990001, "2026-11-07 15:24:00", "DDT", "U", "TRANS_NO=2606;SR_NO=1", "UPDATE_AVG_RATE"),
                         (99990002, "2026-11-07 15:24:10", "DDT", "U", "TRANS_NO=2606;SR_NO=2", "UPDATE_AVG_RATE")])
        con.executemany("INSERT INTO erp_change_values (event_id, column_name, old_value, new_value) VALUES (?,?,?,?)",
                        [(99990001, "AVG_RATE", "0", "12.5"), (99990002, "AVG_RATE", "0", "13.0")])
        con.commit()
        assert ae._rerate_signals(a, since, now) == (True, False)       # only the routine touched rates
        con.execute("INSERT INTO erp_change_events (event_id, changed_at, table_name, action, row_key, module) "
                    "VALUES (?,?,?,?,?,?)", (99990003, "2026-11-08 10:00:00", "DMN", "U", "TRANS_NO=9;SR_NO=1", "DMN_ENTRY"))
        con.execute("INSERT INTO erp_change_values (event_id, column_name, old_value, new_value) VALUES (?,?,?,?)",
                    (99990003, "AMOUNT", "100", "200"))
        con.commit()
        assert ae._rerate_signals(a, since, now) == (True, True)        # a person also edited an amount
        assert ae._rerate_signals(a, "2027-01-01 00:00:00", "2027-02-01 00:00:00") == (False, False)  # window empty
    finally:
        con.executemany("DELETE FROM erp_change_events WHERE event_id = ?", ids)
        con.executemany("DELETE FROM erp_change_values WHERE event_id = ?", ids)
        con.commit()


def _one_issue_line(con, lo, hi):
    return con.execute("SELECT doc_no, item_code FROM store_issues WHERE doc_date BETWEEN '2026-07-01' AND '2026-09-01' "
                       "GROUP BY doc_no, item_code HAVING COUNT(*) = 1 AND SUM(qty) > 0 AND SUM(amount) BETWEEN ? AND ? "
                       "LIMIT 1", (lo, hi)).fetchone()


def test_v0174_rate_fill_is_info_not_pushed(env):
    cfg, con = env
    con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")
    doc, item = _one_issue_line(con, 2000, 100000)
    orig = con.execute("SELECT amount FROM store_issues WHERE doc_no=? AND item_code=?", (doc, item)).fetchone()[0]
    try:
        con.execute("UPDATE store_issues SET amount = 0 WHERE doc_no=? AND item_code=?", (doc, item))  # saved unrated
        audit.run(con, cfg, END, persist=True)                         # snapshot with the line at 0
        con.execute("UPDATE store_issues SET amount = 6000 WHERE doc_no=? AND item_code=?", (doc, item))  # routine fills the rate
        found = [f for f in audit.run(con, cfg, END, persist=True).findings if f.section == "Edits" and doc in f.title]
        fill = [f for f in found if "rate filled in" in f.title]
        assert fill and fill[0].severity == "INFO"                     # INFO is reported but never phone-pushed
        assert not any(f.severity == "ACTION" and doc in f.title for f in found)
    finally:
        con.execute("UPDATE store_issues SET amount = ? WHERE doc_no=? AND item_code=?", (orig, doc, item))
        con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")


def test_v0174_routine_rerate_downgrades_action_to_info(env):
    cfg, con = env
    con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")
    doc, item = _one_issue_line(con, 3000, 100000)
    orig = con.execute("SELECT amount FROM store_issues WHERE doc_no=? AND item_code=?", (doc, item)).fetchone()[0]
    try:
        audit.run(con, cfg, END, persist=True)                         # snapshot with the real (non-zero) amount
        snap = store.get_state(con, "edit_snapshot")
        snap["taken"] = "2026-11-01 00:00:00"                          # widen the window so the routine event falls in it
        store.set_state(con, "edit_snapshot", snap)
        con.execute("INSERT INTO erp_change_events (event_id, changed_at, table_name, action, row_key, module) "
                    "VALUES (?,?,?,?,?,?)", (99990201, "2026-11-07 15:24:00", "DDT", "U", "TRANS_NO=1;SR_NO=1", "UPDATE_AVG_RATE"))
        con.execute("INSERT INTO erp_change_values (event_id, column_name, old_value, new_value) VALUES (?,?,?,?)",
                    (99990201, "AVG_RATE", "0", "12.5"))
        con.execute("UPDATE store_issues SET amount = amount + 5000 WHERE doc_no=? AND item_code=?", (doc, item))
        con.commit()
        found = [f for f in audit.run(con, cfg, END, persist=False).findings if f.section == "Edits" and doc in f.title]
        assert any(f.severity == "INFO" and "average-rate routine" in f.detail for f in found)
        assert not any(f.severity == "ACTION" for f in found)
    finally:
        con.execute("UPDATE store_issues SET amount = ? WHERE doc_no=? AND item_code=?", (orig, doc, item))
        con.execute("DELETE FROM erp_change_events WHERE event_id = 99990201")
        con.execute("DELETE FROM erp_change_values WHERE event_id = 99990201")
        con.execute("DELETE FROM state WHERE key = 'edit_snapshot'")
        con.commit()


# ---- 0.17.5 ------------------------------------------------------------------------------------

class _AuditStub:
    """A minimal Audit for calling one module's run() in isolation on a hand-built store."""
    def __init__(self, con, cfg, d0, d1):
        self.con, self.cfg, self.t = con, cfg, cfg["audit"]
        self._win = (d0, d1)
        self.profile, self.findings, self.summary = {}, [], []

    @property
    def window(self):
        return self._win

    def q(self, sql, *params):
        return self.con.execute(sql, params).fetchall()

    def add(self, severity, section, key, title, detail=""):
        self.findings.append(SimpleNamespace(severity=severity, section=section, key=key, title=title, detail=detail))


def _weaving_store():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE loom_production (prod_date TEXT, loom_code TEXT, shift TEXT, contract_no TEXT, quality TEXT,
            beam_no TEXT, meters REAL, first_quality_m REAL, second_quality_m REAL, rejected_m REAL,
            efficiency_pct REAL, meters_at_100 REAL);
        CREATE TABLE looms (loom_code TEXT, loom_name TEXT);
        CREATE TABLE beams (beam_no TEXT, contract_no TEXT, loom_code TEXT, mounted_date TEXT, set_length_m REAL, woven_m REAL);
        CREATE TABLE contracts (contract_no TEXT, quality TEXT, picks_per_inch REAL);
        CREATE TABLE loom_monitor (read_date TEXT, loom_code TEXT, shift TEXT, meters REAL, efficiency_pct REAL, rpm REAL, stops REAL, shift_sec REAL);
    """)
    return con


def test_v0175_idle_loom_quality_change_is_info(env):
    from datetime import date as _d, timedelta as _td
    from smart_layer.audit import weaving as wv
    cfg, _ = env
    con = _weaving_store()
    end = _d(2026, 10, 2)
    con.execute("INSERT INTO looms VALUES ('L1','LOOM-01')")
    # last production 20 days ago -> idle; a new beam mounted 3 days ago -> within the quality-change grace window
    con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ((end - _td(days=20)).isoformat(), "L1", "A", "C1", "Q1", "B1", 300, 300, 0, 0, 75, None))
    con.execute("INSERT INTO beams VALUES ('B2','C1','L1',?,?,?)", ((end - _td(days=3)).isoformat(), 2000, 0))
    a = _AuditStub(con, cfg, end.isoformat(), end.isoformat())
    wv.run(a)
    idle = [f for f in a.findings if f.key == "idle-L1-" + (end - _td(days=20)).isoformat()]
    assert idle and idle[0].severity == "INFO" and "quality change" in idle[0].detail


def test_v0175_over_production_quality_change_is_info(env):
    from datetime import date as _d, timedelta as _td
    from smart_layer.audit import weaving as wv
    cfg, _ = env
    con = _weaving_store()
    end = _d(2026, 10, 2)
    con.execute("INSERT INTO looms VALUES ('L1','LOOM-01')")
    # ppi 40 -> 90, so the meters are expected to drop to ~40/90 of usual; the actual drop matches that (INFO)
    con.execute("INSERT INTO contracts VALUES ('C-OLD','Q-OLD',40),('C-NEW','Q-NEW',90)")
    # 'before' window (14..74 days ago): contract C-OLD at 200 m/day -> usual ~133 m/day
    for k in range(20, 60):
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-OLD", "Q-OLD", "B1", 200, 200, 0, 0, 75, None))
    # 'recent' 14 days: contract C-NEW at 60 m/day -> about the 59 expected from the ppi change
    for k in range(0, 14):
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-NEW", "Q-NEW", "B2", 60, 60, 0, 0, 75, None))
    a = _AuditStub(con, cfg, end.isoformat(), end.isoformat())
    wv.run(a)
    lh = [f for f in a.findings if f.key.startswith("lhist-L1")]
    assert lh and lh[0].severity == "INFO" and "quality changed" in lh[0].title and "40" in lh[0].title and "90" in lh[0].title
    # a ppi change that does NOT explain the size of the drop stays a WATCH
    con.execute("DELETE FROM loom_production WHERE loom_code='L1' AND prod_date > ?", ((end - _td(days=14)).isoformat(),))
    for k in range(0, 14):
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-NEW", "Q-NEW", "B2", 12, 12, 0, 0, 75, None))
    a2 = _AuditStub(con, cfg, end.isoformat(), end.isoformat())
    wv.run(a2)
    lh2 = [f for f in a2.findings if f.key.startswith("lhist-L1")]
    assert lh2 and lh2[0].severity == "WATCH" and "even allowing for the quality change" in lh2[0].detail


def test_v0175_empty_beam_by_quantity(env):
    cfg, con = env
    ins = "INSERT INTO beam_movements (move_date, beam_no, move_type, party_name) VALUES (?,?,?,?)"
    mk = []
    try:
        # NET OUT: a sizer owes 2 beams by count (3 out, 1 back) -> 2 WATCH, oldest first
        for i, bno in enumerate(("Z1", "Z2", "Z3")):
            con.execute(ins, (f"2026-08-0{i+1}", bno, "empty_to_sizer", "ZZ-NET-OUT"))
        con.execute(ins, ("2026-08-10", "Z9", "sized_received", "ZZ-NET-OUT"))
        # SQUARE: 2 out, 2 back but under different numbers -> net 0 -> INFO, no WATCH
        con.execute(ins, ("2026-08-01", "A1", "empty_to_sizer", "ZZ-SQUARE"))
        con.execute(ins, ("2026-08-02", "A2", "empty_to_sizer", "ZZ-SQUARE"))
        con.execute(ins, ("2026-08-05", "B7", "empty_from_sizer", "ZZ-SQUARE"))
        con.execute(ins, ("2026-08-06", "B8", "sized_received", "ZZ-SQUARE"))
        con.commit()
        found = [f for f in audit.run(con, cfg, END).findings if f.section == "Beams"]
        watch = [f for f in found if f.severity == "WATCH" and "ZZ-NET-OUT" in f.title]
        assert len(watch) == 2
        assert any(f.severity == "INFO" and "ZZ-SQUARE" in f.title and "all back by count" in f.title for f in found)
        assert not any("ZZ-SQUARE" in f.title and f.severity == "WATCH" for f in found)
    finally:
        con.execute("DELETE FROM beam_movements WHERE party_name IN ('ZZ-NET-OUT','ZZ-SQUARE')")
        con.commit()


def test_v0175_loom_numeric_order_field(env):
    from smart_layer.web import data as web_data
    cfg, con = env
    assert web_data._loom_num("L10") == 10 and web_data._loom_num("L2") == 2 and web_data._loom_num("9") == 9
    w = web_data.build(con, cfg, END)["weaving"]
    assert all("loom_no" in l for l in w["looms"])
    # the month-to-date fields the loom-by-loom table needs are present
    assert all(k in w["looms"][0] for k in ("erp_eff_month", "meters_month", "scb_eff_month", "scb_meters_month"))
    assert "erp_meters_month" in w["shed"] and all("meters" in m for m in w["monthly"])


def test_v0175_contract_report(env, tmp_path):
    from smart_layer.textile import contract_report as cr
    from smart_layer import report_registry
    cfg, con = env
    assert "closing" not in report_registry.REPORTS            # removed from the Reports menu (code kept)
    lst = cr.contract_list(con)
    assert lst and any(c["contract"] == "CON-41" for c in lst)
    rep = cr.contract_report(con, cfg, "CON-41")
    assert rep["found"]
    secs = {s["key"]: s for s in rep["sections"]}
    assert {"yarn_in", "sizing", "sets", "beams", "production", "picks_check", "weft", "fabric_out", "balances"} <= set(secs)
    # 0.17.6 retest fixes:
    # bug 4 - sizing shows the sized-beam yarn back plus gully and waste separately (real loss %)
    assert secs["sizing"]["columns"] == ["Sizer", "Yarn sent kg", "Sized-beam yarn kg", "Gully kg", "Waste kg", "Loss %"]
    # bug 5 - beams carry a running/finished State column
    assert "State" in secs["beams"]["columns"]
    # bug 6 - production total column relabelled and a reconciliation note added
    assert secs["production"]["columns"][1] == "Total m"
    # bug 7 - fabric_out has a Thaan column
    assert "Thaan" in secs["fabric_out"]["columns"]
    assert cr.contract_report(con, cfg, "NOPE")["found"] is False
    px = cr.write_xlsx(con, cfg, "CON-41", tmp_path, "c.xlsx")
    pp = cr.write_pdf(con, cfg, "CON-41", tmp_path, "c.pdf", product="SL")
    assert px.exists() and px.stat().st_size > 2000 and pp.exists() and pp.stat().st_size > 2000


def test_v0176_quality_change_date_is_first_new_contract_date(env):
    # Fix 2: the quality-change date is the first production date of the NEW contract on the loom - not the first
    # date inside the recent window. Regression guard: the new contract ran once well before the 14-day window.
    from datetime import date as _d, timedelta as _td
    from smart_layer.audit import weaving as wv
    cfg, _ = env
    con = _weaving_store()
    end = _d(2026, 10, 2)
    con.execute("INSERT INTO looms VALUES ('L1','LOOM-01')")
    con.execute("INSERT INTO contracts VALUES ('C-OLD','Q-OLD',40),('C-NEW','Q-NEW',90)")
    for k in range(15, 60):   # old quality fills the 'before' window
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-OLD", "Q-OLD", "B1", 200, 200, 0, 0, 75, None))
    con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",   # new quality's FIRST run, 30 days ago
                ((end - _td(days=30)).isoformat(), "L1", "A", "C-NEW", "Q-NEW", "B2", 200, 200, 0, 0, 75, None))
    for k in range(0, 14):    # new quality dominates the recent window
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-NEW", "Q-NEW", "B2", 60, 60, 0, 0, 75, None))
    a = _AuditStub(con, cfg, end.isoformat(), end.isoformat())
    wv.run(a)
    lh = [f for f in a.findings if f.key.startswith("lhist-L1")]
    assert lh, "expected an output-change finding"
    text = lh[0].title + " " + (lh[0].detail or "")
    assert (end - _td(days=30)).isoformat() in text          # the real first date of the new contract
    assert (end - _td(days=13)).isoformat() not in text       # NOT the first date inside the recent window


def test_v0176_same_ppi_quality_change_stays_watch(env):
    # Second retest item 1: a quality change that leaves the picks/inch the SAME cannot explain a meters swing,
    # so it stays WATCH (not a soft "not enough history" INFO). Loom 21: 238 -> ~114 m/day at ppi 82 -> 82.
    from datetime import date as _d, timedelta as _td
    from smart_layer.audit import weaving as wv
    cfg, _ = env
    con = _weaving_store()
    end = _d(2026, 10, 2)
    con.execute("INSERT INTO looms VALUES ('L1','LOOM-01')")
    con.execute("INSERT INTO contracts VALUES ('C-OLD','Q-OLD',82),('C-NEW','Q-NEW',82)")
    for k in range(15, 74):   # old quality across the 'before' window at 238 m/day
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-OLD", "Q-OLD", "B1", 238, 238, 0, 0, 75, None))
    for k in range(0, 14):    # new quality, same ppi, at 100 m/day (a real 0.4x drop)
        con.execute("INSERT INTO loom_production VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    ((end - _td(days=k)).isoformat(), "L1", "A", "C-NEW", "Q-NEW", "B2", 100, 100, 0, 0, 75, None))
    a = _AuditStub(con, cfg, end.isoformat(), end.isoformat())
    wv.run(a)
    lh = [f for f in a.findings if f.key.startswith("lhist-L1")]
    assert lh and lh[0].severity == "WATCH"
    assert "does not explain" in lh[0].detail and "picks/inch is the same" in lh[0].detail


def test_v0176_fabric_thaan_null_shows_blank(env):
    # Second retest item 2: a NULL thaan shows "—" (not a false 0); the total is blank when every gate pass is NULL.
    from smart_layer.textile import contract_report as cr
    cfg, _ = env
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE contracts (contract_no TEXT, party_code TEXT, party_name TEXT, contract_date TEXT, quality TEXT,
            ends_per_inch REAL, picks_per_inch REAL, width_in REAL, warp_yarn TEXT, warp_count REAL, weft_yarn TEXT,
            weft_count REAL, ordered_meters REAL, rate_per_meter REAL, rate_per_pick REAL, yarn_owner TEXT);
        CREATE TABLE fabric_dispatches (dispatch_date TEXT, doc_no TEXT, contract_no TEXT, quality TEXT, party_code TEXT,
            deliver_to TEXT, direction TEXT, fresh_m REAL, rejected_m REAL, meters REAL, thaan REAL, amount REAL);
    """)
    con.execute("INSERT INTO contracts (contract_no) VALUES ('K2')")
    ins = ("INSERT INTO fabric_dispatches (dispatch_date, doc_no, contract_no, direction, meters, fresh_m, rejected_m, thaan) "
           "VALUES (?,?,?,?,?,?,?,?)")
    con.execute(ins, ("2026-10-01", "5", "K2", "out", 1000, 900, 100, None))
    con.execute(ins, ("2026-10-02", "6", "K2", "out", 500, 500, 0, None))
    fo = next(s for s in cr.contract_report(con, cfg, "K2")["sections"] if s["key"] == "fabric_out")
    assert all(r[6] == "—" for r in fo["rows"]) and fo["totals"][6] == "—"   # all NULL -> blank
    con.execute("UPDATE fabric_dispatches SET thaan = 12 WHERE doc_no = '5'")
    fo2 = next(s for s in cr.contract_report(con, cfg, "K2")["sections"] if s["key"] == "fabric_out")
    assert fo2["totals"][6] == 12                                              # a real value totals up


def _mini_contract_store():
    con = sqlite3.connect(":memory:")
    con.executescript("""
        CREATE TABLE contracts (contract_no TEXT, party_code TEXT, party_name TEXT, contract_date TEXT, quality TEXT,
            ends_per_inch REAL, picks_per_inch REAL, width_in REAL, warp_yarn TEXT, warp_count REAL, weft_yarn TEXT,
            weft_count REAL, ordered_meters REAL, rate_per_meter REAL, rate_per_pick REAL, yarn_owner TEXT);
        CREATE TABLE looms (loom_code TEXT, loom_name TEXT);
        CREATE TABLE loom_production (loom_code TEXT, contract_no TEXT, quality TEXT, beam_no TEXT, meters REAL,
            first_quality_m REAL, second_quality_m REAL, rejected_m REAL, picks_per_inch REAL, prod_date TEXT);
        CREATE TABLE beams (beam_no TEXT, status TEXT, set_no TEXT, contract_no TEXT, loom_code TEXT, sizer_name TEXT,
            ends REAL, set_length_m REAL, woven_m REAL, length_left_m REAL, mounted_date TEXT, received_date TEXT, bwidth REAL);
        CREATE TABLE beam_movements (move_date TEXT, doc_no TEXT, beam_no TEXT, move_type TEXT, party_name TEXT,
            set_no TEXT, contract_no TEXT, loom_code TEXT, length_m REAL, woven_m REAL);
    """)
    return con


def test_v0176_production_grades_include_b_and_shrinkage_finished_only(env):
    # Fix 4: rejection that already includes B (Dost B+C+CP) -> Total = A + Rejection ties; a C/CP column is shown.
    # Fix 3: a finished beam is pulled from beam_movements with its REAL number (not an internal production id).
    # Fix 5: shrinkage is computed on finished beams only.
    from smart_layer.textile import contract_report as cr
    cfg, _ = env
    con = _mini_contract_store()
    con.execute("INSERT INTO contracts (contract_no, picks_per_inch) VALUES ('K1', 66)")
    con.execute("INSERT INTO looms VALUES ('L29','LOOM-29')")
    # Dost-style grades: Total 11,290 = A 9,261 + Rejection 2,029 (= B 1,818 + C/CP 211)
    con.execute("INSERT INTO loom_production VALUES ('L29','K1','Q','2561',11290,9261,1818,2029,66,'2026-10-01')")
    # a running beam (snapshot) and a FINISHED beam known only to beam_movements, with its painted number 00019
    con.execute("INSERT INTO beams VALUES ('00020','on_loom','S1','K1','L29',NULL,0,13092,8382,4710,'2026-09-01',NULL,1)")
    con.execute("INSERT INTO beam_movements VALUES ('2026-09-20','GP1','00019','from_loom_finished',NULL,'S2','K1','L29',3273,3100)")
    rep = cr.contract_report(con, cfg, "K1")
    secs = {s["key"]: s for s in rep["sections"]}
    prod = secs["production"]
    assert "C/CP m" in prod["columns"] and "already includes" in (prod["note"] or "")
    assert prod["totals"][1] == 11290 and prod["totals"][1] == prod["totals"][2] + prod["totals"][5]   # Total = A + Rejection
    beams = secs["beams"]
    by_beam = {r[0]: r for r in beams["rows"]}
    assert "00019" in by_beam and by_beam["00019"][5] == "finished"      # real number, resolved from movements
    assert "2561" not in by_beam                                          # the internal production id is never shown
    assert any("running" in str(r[5]) for r in beams["rows"])            # the on-loom beam is marked running
    shrink = [r for r in secs["balances"]["rows"] if "Shrinkage" in r[0]][0]
    assert "finished beams only" in shrink[0]                             # computed on the one finished beam
