"""
52_yarn_fabric_data.py
Data for dashboard E "Yarn & fabric": yarn stock, yarn at sizers, empty/sized/on-loom beams, fabric stock
with ageing, gate-pass registers and a contract-wise statement. Read-only (SELECT) from the new ERP (DOSTEX).
Writes yarn_fabric_data.json to the Drive reports folder; the published page re-reads it daily.

Rules (owner, 26-Sep-2026): 1 bag = 100 lb; a typed yarn kg is used only when it is 15-60 kg per bag,
otherwise bags x 45.36 (see 48_contract_closing.KG). A yarn OGP with a price ("@29000") is a sale to the
party, not a return.
"""
import importlib.util
import json
import shutil
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("cc", HERE / "48_contract_closing.py")
cc = importlib.util.module_from_spec(_s); _s.loader.exec_module(cc)
dash = cc.dash
rows = dash.rows
KG_PER_LB = 0.45359237
OUT_NAME = "yarn_fabric_data.json"
RUNNING_DAYS = 15                       # contract "running" = production in the last 15 days
SAME_YARN = {18: 6}                     # 54/1 CTN and 52/1 CTN are the same yarn in the same bags (owner, 28-Sep-2026)
SAME_NAME = {6: "52/1 & 54/1 CTN"}
TT = "CASE WHEN m.tt = 'OGP_YARN' AND REGEXP_LIKE(m.tdesc, '@ *[0-9]') THEN 'OGP_SALE' ELSE m.tt END"

CONTRACTS_SQL = """
 SELECT c.cont_id, c.cont_no, a.an, TO_CHAR(c.cont_date,'YYYY-MM-DD'), c.cont_qty, q.quality, c.warp_yarn_code, c.weft_yarn_code,
        c.conver_per_meter, c.cont_type
 FROM dostex.contm c LEFT JOIN dostex.ach a ON a.ac = c.party_code
 LEFT JOIN (SELECT cont_id, MAX(quality) quality FROM dostex.wv_prod_v GROUP BY cont_id) q ON q.cont_id = c.cont_id"""
PROD_SQL = """
 SELECT cont_id, TO_CHAR(td,'YYYY-MM-DD'), SUM(total_mtr) FROM dostex.wv_prod_v GROUP BY cont_id, TO_CHAR(td,'YYYY-MM-DD')"""
YARN_SQL = f"""
 SELECT d.cont_id, {TT}, m.tno, m.gp_no, TO_CHAR(m.td,'YYYY-MM-DD'), a.an, d.ycode, y.yarn_desc,
        NVL(d.bags,0), NVL(d.cones,0), {cc.KG}, m.tdesc, NVL(d.beams,0)
 FROM dostex.wytm m JOIN dostex.wytd d ON d.trans_no = m.trans_no
 LEFT JOIN dostex.yarn y ON y.ycode = d.ycode LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2)
 WHERE m.tt IN ('IGP_YARN','OGP_YARN','TO_SIZING','FROM_SIZING','YARN_TO_LOOMS','YARN_FROM_LOOMS','IGP_SIZED_BEAMS','OPN_SIZED_BEAMS')
 ORDER BY m.td, m.tno"""
FAB_SQL = """
 SELECT s.cont_id, v.trans_type, v.gp_no, TO_CHAR(v.trans_date,'YYYY-MM-DD'), v.inv_no, v.party, v.deliver_to,
        NVL(v.a,0)+NVL(v.a2,0), NVL(v.b,0), NVL(v.c,0)+NVL(v.cp,0), v.total_mtr, v.rate, v.amt
 FROM dostex.fab_gp_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id ORDER BY v.trans_date, v.trans_no"""
FSTOCK_SQL = """
 SELECT s.cont_id, SUM(NVL(v.a,0)+NVL(v.a2,0)), SUM(NVL(v.b,0)), SUM(NVL(v.c,0)+NVL(v.cp,0)), SUM(v.total_mtr)
 FROM dostex.wv_fab_stock_v v JOIN dostex.wc_cont_sub_v s ON s.cont_sub_id = v.cont_sub_id GROUP BY s.cont_id"""
# beam events: empty beams to/from sizers, sized beams in, beams to/from looms
EMPTY_SQL = """
 SELECT e.tt, TO_CHAR(e.td,'YYYY-MM-DD'), e.an, ABS(e.beam_id), e.beam_code, e.gp_no, e.tno FROM dostex.wv_empty_beam_stock_v e
 WHERE e.tt IN ('OGP_EMPTY_BEAMS','IGP_EMPTY_BEAMS')"""
SIZED_SQL = """
 SELECT TO_CHAR(m.td,'YYYY-MM-DD'), b.beam_id, b.bcode, b.setno, b.blength, d.cont_id, a.an, m.gp_no, m.tt
 FROM dostex.wybeams b JOIN dostex.wytm m ON m.trans_no = b.trans_no
 JOIN dostex.wytd d ON d.trans_no = b.trans_no AND d.sr_no = b.ref_sr LEFT JOIN dostex.ach a ON a.ac = NVL(m.ac1, m.ac2)
 WHERE m.tt IN ('IGP_SIZED_BEAMS','OPN_SIZED_BEAMS') AND b.beam_id IS NOT NULL"""
LOOM_EV_SQL = """
 SELECT m.tt, TO_CHAR(m.td,'YYYY-MM-DD'), d.beam_id, l.lname, d.bstatus, m.trans_no
 FROM dostex.wprdtm m JOIN dostex.wprdtd d ON d.trans_no = m.trans_no LEFT JOIN dostex.looms l ON l.lcode = d.lcode
 WHERE m.tt IN ('BEAMS_TO_LOOM','BEAMS_FROM_LOOM') AND d.beam_id IS NOT NULL"""
ATLOOM_SQL = """
 SELECT v.beam_id, v.beam_code, l.lname, TO_CHAR(v.td,'YYYY-MM-DD'), v.set_no, v.bwidth, v.actual_length, v.beam_prod, v.blength,
        v.cont_id, v.quality
 FROM dostex.wv_beam_atloom_v v LEFT JOIN dostex.looms l ON l.lcode = v.lcode"""
BEAMS_SQL = "SELECT beam_id, bcode, bwidth FROM dostex.beams"


def f(v):
    return float(v or 0)


def build(cur):
    today = date.today()
    days_since = lambda d: (today - date.fromisoformat(d)).days if d else None  # noqa: E731

    # ---- contracts + production
    con = {}
    for cid, no, party, cdate, qty, q, wy, fy, conv, typ in rows(cur, CONTRACTS_SQL):
        con[cid] = dict(no=int(no), party=(party or "").strip(), date=cdate, qty=f(qty), quality=(q or "").strip(),
                        warp=wy, weft=fy, conv=f(conv), type=typ, woven=0.0, first=None, last=None, prod_days=[])
    for cid, d, m in rows(cur, PROD_SQL):
        c = con.get(cid)
        if not c:
            continue
        c["woven"] += f(m); c["prod_days"].append((d, f(m)))
        c["first"] = min(c["first"] or d, d); c["last"] = max(c["last"] or d, d)

    # ---- yarn lines
    ynames = {}
    reg, looms_issue = [], defaultdict(float)             # register (gate passes) and issue-to-looms per (cont, ycode, day)
    stock = defaultdict(lambda: defaultdict(float))       # (cont, ycode) -> movement kg by type
    bags = defaultdict(lambda: defaultdict(float))
    sizer = defaultdict(lambda: {"sent": 0.0, "back": 0.0, "last_sent": None, "sets_out": 0, "beams_back": 0, "yc": None})
    months = defaultdict(lambda: defaultdict(float))
    for cid, tt, tno, gp, d, an, yc, yd, bg, cn, kg, desc, nb in rows(cur, YARN_SQL):
        kg, bg = f(kg), f(bg)
        if yc is not None:
            yc = SAME_YARN.get(int(yc), int(yc))
            ynames[int(yc)] = (yd or f"code {yc}").strip()
        k = (cid, int(yc) if yc is not None else None)
        stock[k][tt] += kg
        bags[k][tt] += bg
        months[d[:7]][tt] += kg
        if tt in ("YARN_TO_LOOMS", "YARN_FROM_LOOMS"):
            looms_issue[(cid, k[1], d)] += kg if tt == "YARN_TO_LOOMS" else -kg
            continue
        if tt in ("TO_SIZING", "FROM_SIZING", "IGP_SIZED_BEAMS") and an:
            # owner counts bags: yarn at a sizer = bags + cones as part-bags (40 a bag above count 40, else 24), x 45.36 kg;
            # typed weights on sized-beam gate passes are sometimes off (e.g. 15-Sep-2026 set 260265: +9 kg)
            cnt_ = f(((yd or "").split("/")[0].strip() or "0").split()[0]) if (yd or "")[:1].isdigit() else 0
            bkg = (bg + f(cn) / (40 if cnt_ > 40 else 24)) * 45.36 if (bg or f(cn)) else kg
            s = sizer[(an.strip(), cid)]
            s["yc"] = k[1] if k[1] is not None else s["yc"]           # yarn count of the contract's warp (same-yarn codes merged)
            if tt == "TO_SIZING":
                s["sent"] += bkg; s["last_sent"] = max(s["last_sent"] or d, d)
            elif tt == "FROM_SIZING":
                s["sent"] -= bkg
            else:
                s["back"] += bkg; s["beams_back"] += int(f(nb))
        reg.append([d, tt, gp or tno, (an or "").strip(), cid, k[1], round(bg, 2), round(f(cn)), round(kg, 1), (desc or "")[:70]])

    ynames.update(SAME_NAME)
    # yarn stock per contract/yarn (godown) = in - returned - to sizing + back from sizing - to looms + from looms
    ystock = []
    for (cid, yc), mv in stock.items():
        g = (mv["IGP_YARN"] - mv["OGP_YARN"] - mv["TO_SIZING"] + mv["FROM_SIZING"] - mv["YARN_TO_LOOMS"] + mv["YARN_FROM_LOOMS"])
        rec = mv["IGP_YARN"] - mv["OGP_YARN"]
        if abs(g) < 0.5 and rec == 0 and not mv["YARN_TO_LOOMS"]:
            continue
        ystock.append([cid, yc, round(rec, 1), round(mv["TO_SIZING"] - mv["FROM_SIZING"], 1), round(mv["YARN_TO_LOOMS"] - mv["YARN_FROM_LOOMS"], 1),
                       round(g, 1), round(mv["OGP_SALE"], 1), round(bags[(cid, yc)]["IGP_YARN"] - bags[(cid, yc)]["OGP_YARN"], 2)])

    # ---- weft cover: yarn actually issued to looms in the last 14 days, against the PARTY's stock of that yarn
    # (yarn is often booked to another contract of the same party, so contract-level stock is not reliable)
    # physical counts (yarn_physical_counts.csv: DATE, PARTY, YARN_CODE, BAGS) anchor the party stock:
    # stock now = counted bags x 100 lb + ERP movements dated after the count
    sign = {"IGP_YARN": 1, "OGP_YARN": -1, "TO_SIZING": -1, "FROM_SIZING": 1, "YARN_TO_LOOMS": -1, "YARN_FROM_LOOMS": 1}
    anchors = []
    cpath = HERE / "yarn_physical_counts.csv"
    if cpath.exists():
        for ln in cpath.read_text(encoding="utf-8-sig").splitlines()[1:]:
            p = [x.strip() for x in ln.split(",")]
            if len(p) >= 4 and p[0]:
                yc0 = SAME_YARN.get(int(p[2]), int(p[2]))
                after = sum(sign.get(r[1], 0) * r[8] for r in reg if r[0] > p[0] and r[5] == yc0 and con.get(r[4], {}).get("party") == p[1])
                after += sum(-v for (ci, yc, d), v in looms_issue.items() if d > p[0] and yc == yc0 and con.get(ci, {}).get("party") == p[1])
                anchors.append([p[1], yc0, p[0], float(p[3]), round(float(p[3]) * 45.36 + after, 1)])
    pstock = defaultdict(float)
    for (cid, yc), mv in stock.items():
        party = con.get(cid, {}).get("party", "?")
        pstock[(party, yc)] += (mv["IGP_YARN"] - mv["OGP_YARN"] - mv["TO_SIZING"] + mv["FROM_SIZING"] - mv["YARN_TO_LOOMS"] + mv["YARN_FROM_LOOMS"])
    erp_pstock = dict(pstock)
    for party, yc0, d0, bags0, now in anchors:
        pstock[(party, yc0)] = now
    use = defaultdict(float); used_by = defaultdict(set)
    for (cid, yc, d), v in looms_issue.items():
        if days_since(d) <= 14 and v > 0:
            party = con.get(cid, {}).get("party", "?")
            use[(party, yc)] += v; used_by[(party, yc)].add(con.get(cid, {}).get("no"))
    cover = []
    for (party, yc), u in use.items():
        g, per_day = pstock.get((party, yc), 0.0), u / 14
        cover.append([party, yc, round(g, 1), round(per_day, 1), round(g / per_day, 1) if per_day else None, sorted(x for x in used_by[(party, yc)] if x)])
    cover.sort(key=lambda r: (r[4] if r[4] is not None else 9e9))
    # ---- sizers
    sizers = []
    for (an, cid), s in sizer.items():
        pend = s["sent"] - s["back"]
        sizers.append([an, cid, round(s["sent"], 1), round(s["back"], 1), round(pend, 1), s["last_sent"], s["beams_back"],
                       bool(s["last_sent"] and days_since(s["last_sent"]) <= 90), s["yc"]])

    # ---- beams: one row per physical beam, from the audited beam register (60_beam_register.py): last ERP movement of each
    # beam, second beams sharing a number included, a sizer gate pass wins over a take-off keyed a day or two later.
    # Beams with no movement in the last 90 days are left out (old / renumbered / idle numbers).
    sized_beams, sized_sets, sized_len = defaultdict(int), defaultdict(set), defaultdict(float)
    for d, bid, bcode, setno, blen, cid, an, gp, tt in rows(cur, SIZED_SQL):
        sized_beams[cid] += 1; sized_sets[cid].add(setno); sized_len[cid] += f(blen)
    _r = importlib.util.spec_from_file_location("beamreg", HERE / "60_beam_register.py")
    beamreg = importlib.util.module_from_spec(_r); _r.loader.exec_module(beamreg)
    breg, _, _ = beamreg.build_register(cur, stale_days=90)
    state = {"On loom": "On loom", "Filled on floor": "Sized, waiting for loom", "Empty on floor": "Empty in mill",
             "Cut beam on floor": "Empty in mill", "At sizer": "At sizer (empty)", "Sent out filled": "Sent out filled"}
    # per loom: fabric meters a day - ERP (last 30 days of production entries) and scoreboard (last 14 daily files) - and the
    # date its production was last checked (entered) for the contract on the loom
    erp_rate = {(ln or "").strip(): f(m) / 30 for ln, m in rows(cur, """SELECT l.lname, SUM(p.total_mtr) FROM dostex.wv_prod_v p
        JOIN dostex.looms l ON l.lcode=p.lcode WHERE p.td > TRUNC(SYSDATE)-30 GROUP BY l.lname""")}
    last_chk = {((ln or "").strip(), str(cn)): d for ln, cn, d in rows(cur, """SELECT l.lname, p.cont_no, MAX(p.td) FROM dostex.wv_prod_v p
        JOIN dostex.looms l ON l.lcode=p.lcode GROUP BY l.lname, p.cont_no""")}
    scb, scb_days = defaultdict(float), 0
    import csv
    scb_files = sorted(Path(dash.load_config()["output"]["folder"]).glob("scb_efficiency_loomwise_*.csv"))[-14:]
    for p_ in scb_files:
        scb_days += 1
        for r_ in csv.DictReader(open(p_, encoding="utf-8-sig")):
            try:
                scb[int(float(r_["LOOM_NO"]))] += float(r_["TOTAL_METERS"] or 0)
            except (ValueError, KeyError):
                pass
    scb_rate = {n: v / scb_days for n, v in scb.items()} if scb_days else {}
    scb_last = scb_files[-1].stem[-10:] if scb_files else None
    # shrinkage beam -> fabric per contract, from its beams taken off COMPLETE (fallback: all contracts)
    shr = {}
    tot_a = tot_w = 0.0
    for cn, a_, w_ in rows(cur, """SELECT c.cont_no, SUM(b.blength*NVL(mt.w,1)), SUM(pr.m) FROM dostex.wybeams b
        JOIN dostex.wytd d ON d.trans_no=b.trans_no AND d.sr_no=b.ref_sr JOIN dostex.contm c ON c.cont_id=d.cont_id
        JOIN (SELECT d2.ref_tno, d2.ref_sr FROM dostex.wprdtm m2 JOIN dostex.wprdtd d2 ON d2.trans_no=m2.trans_no WHERE m2.tt='BEAMS_FROM_LOOM' AND d2.bstatus='COMPLETE'
              GROUP BY d2.ref_tno, d2.ref_sr) o ON o.ref_tno=b.trans_no AND o.ref_sr=b.sr_no
        LEFT JOIN (SELECT d3.ref_tno, d3.ref_sr, MAX(NVL(d3.bwidth,1)) w FROM dostex.wprdtm m3 JOIN dostex.wprdtd d3 ON d3.trans_no=m3.trans_no WHERE m3.tt='BEAMS_TO_LOOM'
              GROUP BY d3.ref_tno, d3.ref_sr) mt ON mt.ref_tno=b.trans_no AND mt.ref_sr=b.sr_no
        JOIN (SELECT d4.ref_tno, d4.ref_sr, SUM(NVL(d4.a,0)+NVL(d4.a2,0)+NVL(d4.b,0)+NVL(d4.c,0)+NVL(d4.cp,0)) m FROM dostex.wprdtm m4 JOIN dostex.wprdtd d4
              ON d4.trans_no=m4.trans_no WHERE m4.tt='PRODUCTION' GROUP BY d4.ref_tno, d4.ref_sr) pr ON pr.ref_tno=b.trans_no AND pr.ref_sr=b.sr_no
        GROUP BY c.cont_no"""):
        if f(a_) > 0:
            shr[str(cn)] = 1 - f(w_) / f(a_); tot_a += f(a_); tot_w += f(w_)
    shr_all = 1 - tot_w / tot_a if tot_a else 0.07
    # contracts of the same quality share their shrinkage when a new contract has no finished beam yet
    qual = {}
    for cid_, c_ in con.items():
        qual.setdefault((c_.get("quality") or "").split("(")[0].strip(), []).append(str(c_.get("no")))
    def shrink_for(cno):
        if str(cno) in shr:
            return shr[str(cno)]
        for k, nos in qual.items():
            if str(cno) in nos:
                v = [shr[n] for n in nos if n in shr]
                if v:
                    return sum(v) / len(v)
        return shr_all
    beams, ignored = [], []
    for x in breg:
        if x["cat"] == "Not used since":
            ignored.append(x["code"]); continue
        extra = x["detail"] + (" · same number painted on another beam" if "same number" in x["flags"] or "more than one beam" in x["flags"] else "")
        row = [x["bid"], x["code"], state[x["cat"]], x["loc"], x["last"].date(), extra, x["left"], x["width"],
               x.get("set") or "", (x.get("sizer") or "").strip()]
        if x["cat"] == "On loom":
            ln = (x["loc"] or "").strip(); lno = int("".join(ch for ch in ln if ch.isdigit()) or 0)
            cno = con.get(x.get("cid"), {}).get("no")
            lc = last_chk.get((ln, str(cno)))
            s_ = shrink_for(cno)
            to_come = max(0.0, (x.get("alen") or 0) * (1 - s_) - ((x.get("alen") or 0) - (x["left"] or 0)))   # fabric still to come as of the last check
            row += [lc.date() if lc else None, round(erp_rate.get(ln, 0), 1), round(scb_rate.get(lno, 0), 1), round(to_come), round(s_, 4)]
        beams.append(row)
    beams.sort(key=lambda b: (b[1] or "", b[0]))
    beams_ignored = sorted(ignored)
    # ends of every set (from the sized-beam gate pass) - to match a finishing loom beam with a filled backup beam
    set_ends = {str(st): int(f(en)) for st, en in rows(cur, """SELECT b.setno, MAX(d.ends) FROM dostex.wybeams b
        JOIN dostex.wytd d ON d.trans_no=b.trans_no AND d.sr_no=b.ref_sr GROUP BY b.setno""") if st}
    # ---- fabric
    fstock = {cid: [round(f(a)), round(f(b)), round(f(c)), round(f(t))] for cid, a, b, c, t in rows(cur, FSTOCK_SQL)}
    freg, disp = [], defaultdict(lambda: [0.0, 0.0])
    for cid, tt, gp, d, inv, party, dto, a, b, c, t, rate, amt in rows(cur, FAB_SQL):
        sign = -1 if tt == "GPIN" else 1
        freg.append([d, tt, gp, inv, (party or "").strip(), (dto or "").strip()[:60], cid, round(f(a)), round(f(b)), round(f(c)), round(f(t)), f(rate) or None, round(f(amt))])
        disp[cid][0 if tt == "GPOUT" else 1] += f(t)
        months[d[:7]]["FAB_OUT" if tt == "GPOUT" else "FAB_IN"] += f(t)
    for cid, c in con.items():
        for d, m in c["prod_days"]:
            months[d[:7]]["WOVEN"] += m
    # ageing: FIFO against production (newest production is what is still in stock)
    ageing = {}
    for cid, s in fstock.items():
        left, buckets = max(0, s[3]), [0, 0, 0, 0]
        for d, m in sorted(con.get(cid, {}).get("prod_days", []), reverse=True):
            if left <= 0:
                break
            take = min(left, m); left -= take
            age = days_since(d)
            buckets[0 if age <= 7 else 1 if age <= 15 else 2 if age <= 30 else 3] += take
        if left > 0:
            buckets[3] += left
        ageing[cid] = [round(x) for x in buckets]

    # ---- contract statement
    contracts = []
    for cid, c in con.items():
        ys = [y for y in ystock if y[0] == cid]
        if not (c["woven"] or ys or disp.get(cid)):
            continue
        contracts.append(dict(id=cid, no=c["no"], party=c["party"], date=c["date"], qty=round(c["qty"]), quality=c["quality"],
                              warp=ynames.get(SAME_YARN.get(int(c["warp"]), int(c["warp"]))) if c["warp"] is not None else None,
                              weft=ynames.get(SAME_YARN.get(int(c["weft"]), int(c["weft"]))) if c["weft"] is not None else None,
                              first=c["first"], last=c["last"], running=bool(c["last"] and days_since(c["last"]) <= RUNNING_DAYS),
                              woven=round(c["woven"]), out=round(disp[cid][0]), back=round(disp[cid][1]),
                              stock=fstock.get(cid, [0, 0, 0, 0]), age=ageing.get(cid, [0, 0, 0, 0]),
                              yarn_rec=round(sum(y[2] for y in ys), 1), yarn_bags=round(sum(y[7] for y in ys), 1),
                              yarn_godown=round(sum(y[5] for y in ys), 1), sets=len(sized_sets[cid]), beams_in=sized_beams[cid], beam_len=round(sized_len[cid])))
    contracts.sort(key=lambda x: -x["no"])

    mlist = sorted(months)[-24:]
    trend = {m: {k: round(v) for k, v in months[m].items()} for m in mlist}
    counts = [a + [round(erp_pstock.get((a[0], a[1]), 0.0), 1)] for a in anchors]   # [party, yc, date, bags, stock now, ERP stock now]
    return dict(yf_version=1, generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"), today=today.isoformat(), counts=counts,
                yarn_names={str(k): v for k, v in ynames.items()},
                contracts=contracts, ystock=ystock, cover=cover, sizers=sizers, beams=beams, beams_ignored=beams_ignored, scb_last=scb_last, set_ends=set_ends, fabric_reg=freg,
                yarn_reg=reg, issues=[[cid, yc, d, round(v, 1)] for (cid, yc, d), v in looms_issue.items() if abs(v) >= 0.5],
                trend=trend, running_days=RUNNING_DAYS)


def main():
    cfg = dash.load_config()
    out_dir = Path(cfg["output"]["folder"])
    con = dash.connect(cfg)
    data = build(con.cursor())
    con.close()
    payload = json.dumps(data, separators=(",", ":"), default=str)
    local = HERE / "business_dashboard" / OUT_NAME
    local.parent.mkdir(exist_ok=True)
    local.write_text(payload, encoding="utf-8")
    shutil.copyfile(local, out_dir / OUT_NAME)
    print(f"OK bytes={len(payload)} contracts={len(data['contracts'])} beams={len(data['beams'])} yarn_reg={len(data['yarn_reg'])} fab_reg={len(data['fabric_reg'])}")


if __name__ == "__main__":
    sys.exit(main())
