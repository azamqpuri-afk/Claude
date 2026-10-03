"""
60_beam_register.py - where every beam is today, and when beam numbers were changed. ERP read only.
Every beam movement in the ERP, per beam (BEAMS.BEAM_ID):
  OGP_EMPTY_BEAMS / IGP_EMPTY_BEAMS  (WV_GP_BEAMSM/D)  empty beam sent to / back from a sizer
  OPN / IGP / OGP_SIZED_BEAMS        (WYBEAMS)         filled (sized) beam received / sent out
  BEAMS_TO_LOOM / BEAMS_FROM_LOOM    (WPRDTD)          mounted / taken off (COMPLETE = empty, CUT = part left)
The last movement gives today's location. Renumbering: old numbers that stop being used while new numbers start -
matched old -> new on beam size and on the movements (e.g. old number sent empty to a sizer, new number comes back filled).
Writes a Google Sheet:  python 60_beam_register.py --upload <sheet id>
"""
import importlib.util
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("d", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)
OUT = HERE / "financial_statements" / "beam_register.xlsx"
TODAY = date.today()
NAVY = PatternFill("solid", fgColor="1F3B57"); W = Font(bold=True, color="FFFFFF"); B = Font(bold=True)
T = Font(bold=True, size=15, color="1F3B57"); NOTE = Font(italic=True, color="4B4F5A")
FILL = {"On loom": "D9F2E3", "Filled on floor": "DDEBFF", "Empty on floor": "F2F2F2", "Cut beam on floor": "FFF1CC",
        "At sizer": "FFE3B3", "Sent out filled": "E8DDF5", "Not used since": "F8DCDC"}
RESERVED = {"00540", "00541", "00542", "00543", "00544", "00545"}   # correction-only numbers, never on a physical beam
PRI = {"BEAMS_FROM_LOOM": 0, "OGP_EMPTY_BEAMS": 1, "IGP_EMPTY_BEAMS": 2, "OPN_SIZED_BEAMS": 3, "IGP_SIZED_BEAMS": 3, "OGP_SIZED_BEAMS": 4, "BEAMS_TO_LOOM": 5}
LABEL = {"OGP_EMPTY_BEAMS": "empty beam sent to sizer", "IGP_EMPTY_BEAMS": "empty beam came back", "OPN_SIZED_BEAMS": "filled beam (opening stock)",
         "IGP_SIZED_BEAMS": "filled beam received from sizer", "OGP_SIZED_BEAMS": "filled beam sent out", "BEAMS_TO_LOOM": "mounted on loom",
         "BEAMS_FROM_LOOM": "taken off loom"}


def head(ws, r, labels, widths=None):
    for i, h in enumerate(labels, 1):
        c = ws.cell(row=r, column=i, value=h); c.font, c.fill = W, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center")
        if widths:
            ws.column_dimensions[c.column_letter].width = widths[i - 1]


def build_register(cur, stale_days=120):
    """One row per physical beam: where it is by its last ERP movement. Also used by the Yarn & Fabric dashboard (52_)."""
    q = lambda s: cur.execute(s).fetchall()  # noqa: E731
    master = {bid: (code, w) for code, w, bid in q("SELECT bcode, bwidth, beam_id FROM dostex.beams")}
    cont = {cid: (no, (p or "").strip(), (ql or "").strip(), (yd or "").strip()) for cid, no, p, ql, yd in q(
        """SELECT c.cont_id, c.cont_no, a.an, qq.qdesc, y.yarn_desc FROM dostex.contm c LEFT JOIN dostex.ach a ON a.ac=c.party_code
           LEFT JOIN dostex.qualities qq ON qq.qcode=c.qcode LEFT JOIN dostex.yarn y ON y.ycode=c.warp_yarn_code""")}
    loom = dict(q("SELECT lcode, lname FROM dostex.looms"))
    ev = defaultdict(list)          # beam_id -> [(td, feed, pri, tt, info dict)]
    for bid, td, feed, tt, setno, blen, cid, who, tr, sr in q(
            """SELECT b.beam_id, m.td, m.feedd, m.tt, b.setno, b.blength, d.cont_id, a.an, b.trans_no, b.sr_no FROM dostex.wybeams b
               JOIN dostex.wytm m ON m.trans_no=b.trans_no JOIN dostex.wytd d ON d.trans_no=b.trans_no AND d.sr_no=b.ref_sr
               LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)"""):
        ev[bid].append((td, feed or td, PRI[tt], tt, dict(set=setno, len=float(blen or 0), cid=cid, who=(who or "").strip(), ref=(tr, sr))))
    for bid, td, feed, tt, who, cid in q(
            """SELECT d.beam_id, m.td, m.feedd, m.tt, a.an, d.cont_id FROM dostex.wv_gp_beamsd d JOIN dostex.wv_gp_beamsm m ON m.trans_no=d.trans_no
               LEFT JOIN dostex.ach a ON a.ac=m.ac1"""):
        ev[bid].append((td, feed or td, PRI[tt], tt, dict(who=(who or "").strip(), cid=cid)))
    for bid, td, feed, tt, lc, st, cid, rt, rs, w in q(
            """SELECT d.beam_id, m.td, m.feedd, m.tt, d.lcode, d.bstatus, d.cont_id, d.ref_tno, d.ref_sr, d.bwidth FROM dostex.wprdtd d
               JOIN dostex.wprdtm m ON m.trans_no=d.trans_no WHERE m.tt IN ('BEAMS_TO_LOOM','BEAMS_FROM_LOOM')"""):
        ev[bid].append((td, feed or td, PRI[tt], tt, dict(loom=loom.get(lc, lc), st=st, cid=cid, ref=(rt, rs), w=w)))
    prod = {(rt, rs): float(m or 0) for rt, rs, m in q(
        """SELECT d.ref_tno, d.ref_sr, SUM(NVL(d.a,0)+NVL(d.a2,0)+NVL(d.b,0)+NVL(d.c,0)+NVL(d.cp,0)) FROM dostex.wprdtd d
           JOIN dostex.wprdtm m ON m.trans_no=d.trans_no WHERE m.tt='PRODUCTION' GROUP BY d.ref_tno, d.ref_sr""")}
    sizedlen = {(tr, sr): float(bl or 0) for tr, sr, bl in q("SELECT trans_no, sr_no, blength FROM dostex.wybeams")}
    sizedinfo, set_sizer = {}, {}
    for tr, sr, st_, who_ in q("""SELECT b.trans_no, b.sr_no, b.setno, a.an FROM dostex.wybeams b JOIN dostex.wytm m ON m.trans_no=b.trans_no
                                  LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2)"""):
        sizedinfo[(tr, sr)] = (st_, (who_ or "").strip()); set_sizer[st_] = (who_ or "").strip()
    atloom = {code: [] for code, _ in master.values()}
    atl_full = defaultdict(list)
    for code, lc, cn, left_, td_, sn_, al_ in q("SELECT beam_code, lcode, cont_no, blength, td, set_no, actual_length FROM dostex.wv_beam_atloom_v"):
        atloom.setdefault(code, []).append((loom.get(lc, lc), cn))
        atl_full[code].append((loom.get(lc, lc), cn, float(left_ or 0), td_, sn_, float(al_ or 0)))
    cid_of = {str(no): cid for cid, (no, *_r) in cont.items()}
    wait_full = defaultdict(list)
    for code, cn, bl, td_, st in q("SELECT DISTINCT beam_code, cont_no, blength, td, setno FROM dostex.beams_stock_v"):
        wait_full[code].append((cn, float(bl or 0), td_, st))
    waiting = defaultdict(list)
    for code, cn in q("SELECT DISTINCT beam_code, cont_no FROM dostex.beams_stock_v"):
        waiting[code].append(cn)

    def cdesc(cid):
        if cid not in cont:
            return ""
        no, p, ql, yd = cont[cid]
        return f"{ql} · {yd} · {p} · contract {no}"

    reg = []
    # beam numbers 00540-00545 are reserved for empty-beam report correction gate passes (owner, 2026-09-29) - not physical beams
    for bid in [b for b in ev if master[b][0] in RESERVED]:
        ev.pop(bid)
    for bid in sorted(ev, key=lambda b: master[b][0]):
        lset, lsizer, alen_ = "", "", None
        e = sorted(ev[bid], key=lambda x: (x[0], x[2], x[1]))
        # sent empty to a sizer, and its take-off from the loom keyed a day or two AFTER the gate pass: it is at the sizer
        if len(e) > 1 and e[-1][3] == "BEAMS_FROM_LOOM" and e[-2][3] == "OGP_EMPTY_BEAMS" and (e[-1][0] - e[-2][0]).days <= 3:
            e[-1], e[-2] = e[-2], e[-1]
        code, width = master[bid]
        td, _, _, tt, inf = e[-1]
        days = (TODAY - td.date()).days
        if tt == "BEAMS_TO_LOOM":
            left = sizedlen.get(inf["ref"], 0) * float(inf["w"] or 1) - prod.get(inf["ref"], 0)
            cat = "On loom"; where = f"{inf['loom']} - {cdesc(inf['cid'])} - about {max(left, 0):,.0f} m left to weave"
            loc, detail, mleft = inf["loom"], cdesc(inf["cid"]), round(max(left, 0))
            alen_ = sizedlen.get(inf["ref"], 0) * float(inf["w"] or 1)
            lset, lsizer = sizedinfo.get(inf["ref"], ("", ""))
        elif tt == "BEAMS_FROM_LOOM" and inf["st"] == "CUT":
            cat = "Cut beam on floor"; where = f"taken off {inf['loom']} as CUT on {td:%d-%b-%y} - part filled - {cdesc(inf['cid'])}"
            loc, detail, mleft = f"off {inf['loom']} (cut, part filled)", cdesc(inf["cid"]), None
        elif tt in ("BEAMS_FROM_LOOM", "IGP_EMPTY_BEAMS"):
            cat = "Empty on floor"
            where = f"empty - off {inf['loom']} on {td:%d-%b-%y}" if tt == "BEAMS_FROM_LOOM" else f"empty - came back from {inf['who']} on {td:%d-%b-%y}"
            loc, detail, mleft = (f"off {inf['loom']}" if tt == "BEAMS_FROM_LOOM" else f"back from {inf['who']}"), "", None
        elif tt in ("IGP_SIZED_BEAMS", "OPN_SIZED_BEAMS"):
            cat = "Filled on floor"; where = f"filled {inf['len'] * 1:,.0f} m - {cdesc(inf['cid'])} - set {inf['set']} from {inf['who']} on {td:%d-%b-%y}"
            loc, detail, mleft = cdesc(inf["cid"]), f"set {inf['set']} · {inf['len']:,.0f} m · from {inf['who']}", round(inf["len"])
        elif tt == "OGP_EMPTY_BEAMS":
            cat = "At sizer"; where = f"empty at {inf['who']} - sent {td:%d-%b-%y}"
            loc, detail, mleft = inf["who"], "", None
        else:
            cat = "Sent out filled"; where = f"filled beam sent to {inf['who']} on {td:%d-%b-%y}"
            loc, detail, mleft = inf["who"], "", None
        stale = cat != "On loom" and days > stale_days
        if stale:
            where = f"last movement {days} days ago ({LABEL[tt]}, {td:%d-%b-%y}) - {where}"
        # cross-checks with the ERP's own stock views
        flags = []
        on = atloom.get(code, [])
        if cat == "On loom" and not on:
            flags.append("not in the ERP 'beams at loom' view")
        if on and (cat != "On loom" or len(on) > 1):
            flags.append("number painted on more than one beam - see the extra row(s)")
        if cat == "Filled on floor" and code not in waiting:
            flags.append("not in the ERP 'sized beams in stock' view")

        first = min(x[0] for x in e)
        reg.append(dict(code=code, bid=bid, width=width, cat=("Not used since" if stale else cat), cat0=cat, where=where, last=td, first=first,
                        n=len(e), flags="; ".join(flags), ev=e, loc=loc, detail=detail, left=mleft,
                        set=lset if cat == "On loom" else inf.get("set"), sizer=lsizer if cat == "On loom" else inf.get("who"), cid=inf.get("cid"), alen=alen_))
        # extra physical beams carrying the same number (the ERP tracks one history per number)
        own_loom = inf.get("loom") if cat == "On loom" else None
        for ln, cn, left_, td_, sn_, al_ in atl_full.get(code, []):
            if ln == own_loom:
                continue
            reg.append(dict(code=code + " (2nd beam)", bid=bid, width=width, cat="On loom", cat0="On loom",
                            where=f"{ln} - {cdesc(cid_of.get(str(cn)))} - about {left_:,.0f} m left to weave (mounted {td_:%d-%b-%y})",
                            last=td_, first=td_, n=0, flags="same number as another beam - ERP beams-at-loom view", ev=e,
                            loc=ln, detail=cdesc(cid_of.get(str(cn))), left=round(left_), set=sn_, sizer=set_sizer.get(sn_, ""), cid=cid_of.get(str(cn)), alen=al_))
        own_set = inf.get("set") if cat in ("Filled on floor",) else None
        for cn, bl, td_, st in wait_full.get(code, []):
            if st == own_set:
                continue
            reg.append(dict(code=code + " (2nd beam)", bid=bid, width=width, cat="Filled on floor", cat0="Filled on floor",
                            where=f"filled {bl:,.0f} m - {cdesc(cid_of.get(str(cn)))} - set {st}, received {td_:%d-%b-%y}",
                            last=td_, first=td_, n=0, flags="same number as another beam - ERP sized-beams-in-stock view", ev=e,
                            loc=cdesc(cid_of.get(str(cn))), detail=f"set {st} · {bl:,.0f} m", left=round(bl), set=st, sizer=set_sizer.get(st, ""), cid=cid_of.get(str(cn))))
    return reg, master, cdesc


def main():
    cur = dash.connect(dash.load_config()).cursor()
    reg, master, cdesc = build_register(cur)
    # ---------- renumbering: old numbers that went quiet vs new numbers that started (after the first year of the ERP)
    start = datetime(2024, 12, 1)
    olds = [r for r in reg if r["cat"] == "Not used since" and r["last"] >= start]
    news = [r for r in reg if r["first"] >= datetime(2025, 7, 1) and r["n"]]
    pairs, used = [], set()
    for o in sorted(olds, key=lambda r: r["last"]):
        otd, _, _, ott, oinf = sorted(o["ev"], key=lambda x: (x[0], x[2], x[1]))[-1]
        best = None
        for nw in news:
            if nw["code"] in used or nw["first"] < otd:
                continue
            ntd, _, _, ntt, ninf = sorted(nw["ev"], key=lambda x: (x[0], x[2], x[1]))[0]
            gap = (ntd - otd).days
            if gap > 120 or (o["width"] and nw["width"] and o["width"] != nw["width"]):
                continue
            score = gap
            if ott == "OGP_EMPTY_BEAMS" and ntt == "IGP_SIZED_BEAMS" and oinf.get("who") and oinf.get("who") == ninf.get("who"):
                score -= 1000                                         # sent empty to a sizer, came back filled from the same sizer
            elif ott == "BEAMS_FROM_LOOM" and ntt in ("OGP_EMPTY_BEAMS", "BEAMS_TO_LOOM"):
                score -= 500
            if best is None or score < best[0]:
                best = (score, nw, ntt, ntd, ninf)
        if best:
            used.add(best[1]["code"])
            pairs.append((o, otd, ott, oinf, best[1], best[3], best[2], best[4], "strong" if best[0] < -500 else "possible"))
    unmatched_new = [r for r in news if r["code"] not in used]

    # ---------- workbook
    wb = Workbook()
    sm = wb.active; sm.title = "Summary"
    sm["A1"] = f"BEAM REGISTER · where every beam is · {TODAY:%d-%b-%Y}"; sm["A1"].font = T
    sm["A2"] = "From every beam movement in the ERP (empty out/in, filled in, mounted, taken off). ERP read only."; sm["A2"].font = NOTE
    head(sm, 4, ["Location", "Beams"], [40, 10])
    order = ["On loom", "Filled on floor", "Cut beam on floor", "Empty on floor", "At sizer", "Sent out filled", "Not used since"]
    names = {"Not used since": "Not used for 120+ days (old / renumbered / retired)"}
    r = 5
    for k in order:
        sm.cell(row=r, column=1, value=names.get(k, k)); sm.cell(row=r, column=2, value=sum(1 for x in reg if x["cat"] == k))
        sm.cell(row=r, column=1).fill = PatternFill("solid", fgColor=FILL[k]); r += 1
    sm.cell(row=r, column=1, value="BEAM NUMBERS EVER USED (plus " + str(sum(1 for x in reg if not x["n"])) + " extra beams sharing a number)").font = B
    sm.cell(row=r, column=2, value=sum(1 for x in reg if x["n"])).font = B
    sm.cell(row=r + 1, column=1, value="Numbers in the ERP beam master (never used: " + str(len(master) - len(reg)) + ")")
    sm.cell(row=r + 1, column=2, value=len(master))
    sm.cell(row=r + 2, column=1, value="PHYSICAL BEAMS IN USE NOW (incl. beams sharing a number; not counting 'not used 120+ days')").font = B
    sm.cell(row=r + 2, column=2, value=sum(1 for x in reg if x["cat"] != "Not used since")).font = B
    sm.cell(row=r + 4, column=1, value="Beam number changes (renumbering)").font = Font(bold=True, size=12, color="1F3B57")
    waves = defaultdict(int)
    for x in news:
        waves[x["first"].strftime("%b-%Y")] += 1
    sm.cell(row=r + 5, column=1, value="New numbers first used, by month: " + ", ".join(f"{k}: {v}" for k, v in sorted(waves.items(), key=lambda kv: datetime.strptime(kv[0], "%b-%Y"))))
    qw = defaultdict(int)
    for x in olds:
        qw[x["last"].strftime("%b-%Y")] += 1
    sm.cell(row=r + 6, column=1, value="Old numbers last used, by month: " + ", ".join(f"{k}: {v}" for k, v in sorted(qw.items(), key=lambda kv: datetime.strptime(kv[0], "%b-%Y"))))
    sm.column_dimensions["A"].width = 120

    bw = wb.create_sheet("Beams")
    head(bw, 1, ["Beam", "Location", "Where exactly", "Beam size (in)", "Last movement", "First used", "Movements", "ERP check"], [9, 20, 95, 9, 13, 12, 10, 60])
    for x in reg:
        bw.append([x["code"], x["cat"] if x["cat"] != "Not used since" else "Not used 120+ days", x["where"], x["width"], x["last"].date(), x["first"].date(), x["n"], x["flags"]])
        bw.cell(row=bw.max_row, column=2).fill = PatternFill("solid", fgColor=FILL[x["cat"]])
        for col in (5, 6):
            bw.cell(row=bw.max_row, column=col).number_format = "dd-mmm-yy"
    bw.freeze_panes = "B2"; bw.auto_filter.ref = f"A1:H{bw.max_row}"

    rn = wb.create_sheet("Renumbering")
    rn["A1"] = "Old beam numbers that stopped, matched to new numbers that started (same beam size, following movement)"; rn["A1"].font = T
    head(rn, 3, ["Old number", "Old last movement", "Date", "New number", "New first movement", "Date", "Days between", "Match"], [11, 42, 12, 11, 42, 12, 10, 10])
    for o, otd, ott, oinf, nw, ntd, ntt, ninf, how in pairs:
        rn.append([o["code"], f"{LABEL[ott]} {oinf.get('who') or oinf.get('loom') or ''}", otd.date(), nw["code"], f"{LABEL[ntt]} {ninf.get('who') or ninf.get('loom') or ''}",
                   ntd.date(), (ntd - otd).days, how])
        for col in (3, 6):
            rn.cell(row=rn.max_row, column=col).number_format = "dd-mmm-yy"
    rn.append([])
    rn.append(["Old numbers that stopped with no new number to match: " + ", ".join(o["code"] for o in olds if o["code"] not in {p[0]["code"] for p in pairs})])
    rn.append(["New numbers with no old number to match (new beams?): " + ", ".join(f"{x['code']} ({x['first']:%d-%b-%y})" for x in unmatched_new)])

    hs = wb.create_sheet("History")
    head(hs, 1, ["Beam", "Date", "Movement", "Loom / party", "Contract / quality", "Set", "Length (m)", "Status"], [9, 12, 30, 34, 60, 10, 10, 10])
    for x in reg:
        for td, _, _, tt, inf in sorted(x["ev"], key=lambda z: (z[0], z[2], z[1])):
            hs.append([x["code"], td.date(), LABEL[tt], inf.get("loom") or inf.get("who") or "", cdesc(inf.get("cid")), inf.get("set", ""),
                       inf.get("len") or None, inf.get("st") or ""])
            hs.cell(row=hs.max_row, column=2).number_format = "dd-mmm-yy"
    hs.freeze_panes = "A2"; hs.auto_filter.ref = f"A1:H{hs.max_row}"
    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    from collections import Counter
    print(Counter(x["cat"] for x in reg), "used", len(reg), "master", len(master))
    print("pairs", len(pairs), "strong", sum(1 for p in pairs if p[8] == "strong"), "unmatched new", [x["code"] for x in unmatched_new])
    print("waves new:", dict(waves)); print("old last:", dict(qw))
    for x in reg:
        print(x["code"], "|", x["cat"], "|", x["where"][:150], "|", x["flags"][:120])
    for p in pairs:
        print("RENUM", p[0]["code"], LABEL[p[2]], p[3].get("who") or p[3].get("loom"), p[1].date(), "->", p[4]["code"], LABEL[p[6]], p[7].get("who") or p[7].get("loom"), p[5].date(), p[8])


def upload(fid):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = service_account.Credentials.from_service_account_file(str(HERE / "service_account.json"), scopes=["https://www.googleapis.com/auth/drive"])
    build("drive", "v3", credentials=creds).files().update(fileId=fid, media_body=MediaFileUpload(
        str(OUT), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")).execute()
    print("uploaded", fid)


if __name__ == "__main__":
    main()
    if len(sys.argv) > 2 and sys.argv[1] == "--upload":
        upload(sys.argv[2])
