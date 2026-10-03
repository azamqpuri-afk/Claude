"""
53_verify_dashboards.py - read-only cross-check of every dashboard data file against the ERP, plus a scan of
every yarn / fabric / beam entry for mistyped or impossible values. Writes verify_report.json and prints a summary.
"""
import importlib.util
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("cc", HERE / "48_contract_closing.py")
cc = importlib.util.module_from_spec(_s); _s.loader.exec_module(cc)
dash = cc.dash
rows = dash.rows
checks, anomalies = [], defaultdict(list)


def chk(area, name, dash_v, erp_v, tol=1.0):
    ok = dash_v is not None and erp_v is not None and abs(float(dash_v) - float(erp_v)) <= tol
    checks.append([area, name, dash_v, erp_v, "OK" if ok else "MISMATCH"])


def main():
    cfg = dash.load_config()
    out = Path(cfg["output"]["folder"])
    D = json.loads((out / "business_dashboard_data.json").read_text(encoding="utf-8"))
    L = json.loads((out / "business_dashboard_ledgers.json").read_text(encoding="utf-8"))
    Y = json.loads((out / "yarn_fabric_data.json").read_text(encoding="utf-8"))
    cur = dash.connect(cfg).cursor()
    m = D["months"]
    # ---------- main dashboard (A, B, C)
    checks.append(["Main", "last month in data", m[-1], date.today().strftime("%Y-%m"), "OK" if m[-1] == date.today().strftime("%Y-%m") else "MISMATCH"])
    tot = [sum(s[i] for s in D["sales"]["series"].values()) for i in range(len(m))]
    chk("Main", "sales by type add up to revenue (all months, max diff Rs)", max(abs(a - b) for a, b in zip(tot, D["pnl"]["revenue"])), 0, 1)
    rev = {mm: float(v) for mm, v in rows(cur, "SELECT TO_CHAR(vd,'YYYY-MM'), -SUM(amt) FROM dostex.acl WHERE ac LIKE '35%' AND vt<>'OPN' GROUP BY TO_CHAR(vd,'YYYY-MM')")}
    for mm in m[-4:]:
        chk("Main", f"revenue {mm} (Rs)", D["pnl"]["revenue"][m.index(mm)], round(rev.get(mm, 0)), 2)
    exp = {mm: float(v) for mm, v in rows(cur, "SELECT TO_CHAR(vd,'YYYY-MM'), SUM(amt) FROM dostex.acl WHERE (ac LIKE '40%' OR ac LIKE '45%' OR ac LIKE '55%') AND vt<>'OPN' GROUP BY TO_CHAR(vd,'YYYY-MM')")}
    for mm in m[-3:]:
        chk("Main", f"expenses {mm} (Rs)", D["pnl"]["expenses"][m.index(mm)], round(exp.get(mm, 0)), 2)
    for key, pref, sign in (("receivables_net", "3003", 1), ("payables_net", "2001", -1), ("accrued_net", "2002", -1), ("cash_bank_total", "3008", 1)):
        v = rows(cur, f"SELECT SUM(amt) FROM dostex.acl WHERE ac LIKE '{pref}%'")[0][0]
        chk("Main", f"{key} today (Rs)", D["balances"][key][-1], round(sign * float(v or 0)), 2)
    pm = rows(cur, "SELECT SUM(total_mtr), SUM(eff_100) FROM dostex.wv_eff_v WHERE TO_CHAR(td,'YYYY-MM') = :mm", mm=m[-2])[0]
    chk("Main", f"loom meters {m[-2]}", D["production"]["mtr"][-2], round(float(pm[0] or 0)), 2)
    chk("Main", f"meters at 100% {m[-2]}", D["production"]["mtr100"][-2], round(float(pm[1] or 0)), 2)
    F = D["floor"]
    chk("Mill floor", "looms with an entry (last 400 days)", len(F["looms_last"]),
        rows(cur, "SELECT COUNT(DISTINCT lcode) FROM dostex.wv_eff_v WHERE td >= TRUNC(SYSDATE)-400")[0][0], 0)
    fab30 = rows(cur, "SELECT SUM(total_mtr) FROM dostex.wv_prod_v WHERE td >= TRUNC(SYSDATE)-30")[0][0]
    chk("Mill floor", "ERP fabric meters, last 30 days", sum(r[6] for r in F["last30"]["looms"]), round(float(fab30 or 0)), 40)
    rolls = rows(cur, "SELECT COUNT(DISTINCT trans_no||'-'||lcode) FROM dostex.wv_prod_v WHERE td >= TRUNC(SYSDATE)-30")[0][0]
    checks.append(["Mill floor", "rolls checked, last 30 days (ERP entries)", sum(l[8] for l in F["looms_last"]), rolls, "INFO"])
    bad_rpm = [p for p in F["rpm"]["shed"] if not (50 < p[1] < 300)]
    checks.append(["Mill floor", "scoreboard RPM points outside 50-300", len(bad_rpm), 0, "OK" if not bad_rpm else "MISMATCH"])
    # ---------- ledgers (B ledgers tab, D)
    bal = {ac: float(v) for ac, v in rows(cur, "SELECT ac, SUM(amt) FROM dostex.acl WHERE SUBSTR(ac,1,4) IN ('3003','2001','2002','3008') GROUP BY ac")}
    cnt = {ac: int(n) for ac, n in rows(cur, "SELECT ac, COUNT(*) FROM dostex.acl WHERE SUBSTR(ac,1,4) IN ('3003','2001','2002','3008') AND NVL(amt,0)<>0 GROUP BY ac")}
    bad = [(a["name"], round(sum(l[4] for l in a["lines"])), round(bal.get(a["ac"], 0))) for a in L["accounts"] if abs(sum(l[4] for l in a["lines"]) - bal.get(a["ac"], 0)) > 1]
    checks.append(["Ledgers", f"{len(L['accounts'])} ledger balances equal ERP", len(L["accounts"]) - len(bad), len(L["accounts"]), "OK" if not bad else "MISMATCH"])
    badc = [a["name"] for a in L["accounts"] if len(a["lines"]) != cnt.get(a["ac"], 0)]
    checks.append(["Ledgers", "every ledger line present", len(L["accounts"]) - len(badc), len(L["accounts"]), "OK" if not badc else "MISMATCH"])
    missing = [ac for ac in cnt if ac not in {a["ac"] for a in L["accounts"]}]
    checks.append(["Ledgers", "accounts with entries missing from the dropdown", len(missing), 0, "OK" if not missing else "MISMATCH"])
    # ---------- yarn & fabric (E)
    fst = rows(cur, "SELECT SUM(total_mtr) FROM dostex.wv_fab_stock_v")[0][0]
    chk("Yarn & fabric", "fabric stock, all contracts (m)", sum(c["stock"][3] for c in Y["contracts"]), round(float(fst or 0)), 40)
    atl = rows(cur, "SELECT COUNT(*), SUM(blength) FROM dostex.wv_beam_atloom_v")[0]
    onl = [b for b in Y["beams"] if b[2] == "On loom"]
    chk("Yarn & fabric", "beams on looms", len(onl), atl[0], 0)
    chk("Yarn & fabric", "meters left on looms", sum(b[6] or 0 for b in onl), round(float(atl[1] or 0)), 5)
    # a beam number can be painted on two beams (e.g. 00006 filled for two contracts): count distinct contract + beam + set
    bs = rows(cur, "SELECT COUNT(*) FROM (SELECT DISTINCT cont_no, beam_code, setno FROM dostex.beams_stock_v)")[0][0]
    chk("Yarn & fabric", "sized beams waiting for loom", len([b for b in Y["beams"] if b[2].startswith("Sized")]), bs, 0)
    # the ERP's own empty-beam view counts empty beams RETURNED as sent - count beams sent empty (last 90 days) with no later movement instead
    erp_empty = rows(cur, """SELECT COUNT(*) FROM dostex.wv_gp_beamsd d JOIN dostex.wv_gp_beamsm m ON m.trans_no=d.trans_no WHERE m.tt='OGP_EMPTY_BEAMS' AND m.td > SYSDATE-90
          AND NOT EXISTS (SELECT 1 FROM dostex.wybeams b JOIN dostex.wytm t ON t.trans_no=b.trans_no WHERE b.beam_id=d.beam_id AND t.td >= m.td)
          AND NOT EXISTS (SELECT 1 FROM dostex.wprdtd x JOIN dostex.wprdtm y ON y.trans_no=x.trans_no WHERE x.beam_id=d.beam_id AND y.tt='BEAMS_TO_LOOM' AND y.td >= m.td)
          AND NOT EXISTS (SELECT 1 FROM dostex.wprdtd x JOIN dostex.wprdtm y ON y.trans_no=x.trans_no WHERE x.beam_id=d.beam_id AND y.tt='BEAMS_FROM_LOOM' AND y.td > m.td + 3)""")[0][0]
    chk("Yarn & fabric", "empty beams at sizers", len([b for b in Y["beams"] if b[2] == "At sizer (empty)"]), erp_empty, 0)
    fo = rows(cur, "SELECT SUM(CASE WHEN v.trans_type='GPOUT' THEN v.total_mtr ELSE 0 END), SUM(CASE WHEN v.trans_type='GPIN' THEN v.total_mtr ELSE 0 END), COUNT(*) FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id")[0]
    chk("Yarn & fabric", "fabric dispatched, all time (m)", sum(r[10] for r in Y["fabric_reg"] if r[1] == "GPOUT"), round(float(fo[0] or 0)), 40)
    chk("Yarn & fabric", "fabric returned, all time (m)", sum(r[10] for r in Y["fabric_reg"] if r[1] == "GPIN"), round(float(fo[1] or 0)), 40)
    chk("Yarn & fabric", "fabric gate-pass lines", len(Y["fabric_reg"]), fo[2], 0)
    yl = rows(cur, "SELECT COUNT(*) FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no WHERE m.tt IN ('IGP_YARN','OGP_YARN','TO_SIZING','FROM_SIZING','IGP_SIZED_BEAMS','OPN_SIZED_BEAMS')")[0][0]
    chk("Yarn & fabric", "yarn gate-pass lines", len(Y["yarn_reg"]), yl, 0)
    woven = rows(cur, "SELECT SUM(total_mtr) FROM dostex.wv_prod_v")[0][0]
    chk("Yarn & fabric", "fabric woven, all contracts (m)", sum(c["woven"] for c in Y["contracts"]), round(float(woven or 0)), 80)

    # ---------- entry-by-entry scan
    for tt, tno, gp, d, cno, party, yd, bg, cn, typed, used, desc in rows(cur, f"""
        SELECT m.tt, m.tno, m.gp_no, TO_CHAR(m.td,'YYYY-MM-DD'), c.cont_no, a.an, y.yarn_desc, NVL(d.bags,0), NVL(d.cones,0), NVL(d.yarn_wt,0), {cc.KG}, m.tdesc
        FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no=m.trans_no LEFT JOIN dostex.yarn y ON y.ycode=d.ycode
        LEFT JOIN dostex.contm c ON c.cont_id=d.cont_id LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)
        WHERE m.tt IN ('IGP_YARN','OGP_YARN','TO_SIZING','FROM_SIZING','IGP_SIZED_BEAMS','YARN_TO_LOOMS','YARN_FROM_LOOMS')"""):
        typed, used = float(typed), float(used or 0)
        base = [d, tt, gp or tno, cno, (party or "").strip(), (yd or "").strip(), float(bg), float(cn), round(typed, 2), round(used, 2)]
        if abs(typed - used) > 1:
            anomalies["yarn weight corrected"].append(base + ["typed weight not believable for the bags/cones; bags/cones used"])
        elif typed == 0 and bg == 0 and cn == 0:
            anomalies["yarn line with no quantity"].append(base + ["no bags, cones or weight"])
        if cno is None:
            anomalies["yarn line without contract"].append(base + ["no contract number"])
    for d, tt, gp, cno, t, a, b, c, rate in rows(cur, """
        SELECT TO_CHAR(v.trans_date,'YYYY-MM-DD'), v.trans_type, v.gp_no, s.cont_no, v.total_mtr, NVL(v.a,0)+NVL(v.a2,0), NVL(v.b,0), NVL(v.c,0)+NVL(v.cp,0), v.rate
        FROM dostex.fab_gp_v v LEFT JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id=v.cont_sub_id"""):
        t, a, b, c = float(t or 0), float(a), float(b), float(c)
        if t <= 0:
            anomalies["fabric gate pass with zero meters"].append([d, tt, gp, cno, t])
        elif abs(a + b + c - t) > 1:
            anomalies["fabric grades do not add up"].append([d, tt, gp, cno, t, a, b, c])
        if tt == "GPOUT" and not rate:
            anomalies["dispatch without rate (not invoiced)"].append([d, tt, gp, cno, t])
    for d, loom, cno, m1, m100, sh in rows(cur, """
        SELECT TO_CHAR(e.td,'YYYY-MM-DD'), l.lname, MAX(e.quality), SUM(e.total_mtr), SUM(e.eff_100), SUM(e.tot_shifts)
        FROM dostex.wv_eff_v e JOIN dostex.looms l ON l.lcode=e.lcode
        WHERE e.td >= TRUNC(SYSDATE)-120 GROUP BY TO_CHAR(e.td,'YYYY-MM-DD'), l.lname"""):
        eff = float(m1 or 0) / float(m100) * 100 if m100 else None
        if eff is not None and (eff > 100 or eff < 40):
            anomalies["production entry efficiency impossible (>100% or <40%, last 120 days)"].append([d, loom, cno, round(eff, 1), float(m1 or 0), float(sh or 0)])
    # beams mounted on two looms at once
    on = defaultdict(list)
    for b in Y["beams"]:
        if b[2] == "On loom":
            on[b[3]].append(b[1])
    rep = {"checks": checks, "anomalies": {k: v for k, v in anomalies.items()}}
    (HERE / "business_dashboard" / "verify_report.json").write_text(json.dumps(rep, default=str, indent=1), encoding="utf-8")
    for c in checks:
        print(" | ".join(str(x) for x in c))
    for k, v in anomalies.items():
        print(f"ANOMALY {k}: {len(v)}")


if __name__ == "__main__":
    main()
