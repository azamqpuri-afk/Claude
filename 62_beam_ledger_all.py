"""
62_beam_ledger_all.py - party-wise beam ledger across BOTH ERPs (old DOSTT 2014-Sep-2023, new DOSTEX Oct-2023 on), by COUNT
(beam numbers ignored - numbers are duplicated/reused): empty beams sent to each sizing party vs beams that came back.
  old ERP: sent = DOSTT.WPRDTM/WPRDTD 'OGP_EMPTY_BEAMS' (party = AC1, from Oct-2015); back = DOSTT.WYTM/WYBEAMS 'IGP_SIZED_BEAMS'
  new ERP: sent = WV_GP_BEAMSM/D 'OGP_EMPTY_BEAMS'; back = WYBEAMS 'IGP_SIZED_BEAMS' + WV_GP_BEAMSD 'IGP_EMPTY_BEAMS'
  filled beams sent OUT (OGP_SIZED_BEAMS) = a party's own beams going back.
Empty beams were not entered in the old ERP before 8-Oct-2015, so beams received before that are listed but not in the balance.
Parties that bring filled beams on their OWN beams (owner: e.g. Hiba) are judged on beams received vs beams sent back.
ERP read only.  python 62_beam_ledger_all.py --upload <google sheet id>
"""
import importlib.util
import re
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("d", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)
OUT = HERE / "financial_statements" / "beam_ledger_all.xlsx"
START = datetime(2015, 10, 8)            # first empty-beam gate pass in the old ERP
NEW_ERP = datetime(2023, 10, 1)          # new ERP (DOSTEX) starts; old ERP (DOSTT) runs to 30-Sep-2023
NAVY = PatternFill("solid", fgColor="1F3B57"); W = Font(bold=True, color="FFFFFF"); B = Font(bold=True)
T = Font(bold=True, size=15, color="1F3B57"); NOTE = Font(italic=True, color="4B4F5A")
RED = PatternFill("solid", fgColor="F8C9C9"); AMB = PatternFill("solid", fgColor="FFE3B3"); GRN = PatternFill("solid", fgColor="D9F2E3")
OWN = ("HIBA", "BABAR", "UMER FABRICS", "NIAZ", "IBRAHEEM", "CHOHAN", "AKEEL", "ADEEL", "NEW QADRI")   # parties' own beams
MOVE = {("new", "OGP_EMPTY_BEAMS", 17, "2023-12-31"): "MANZOOR SIZING INDUSTRIES"}  # booked to Ali Hajwery, same 4 beams back from Manzoor next day


def norm(an):
    return re.sub(r"\s+", " ", (an or "UNKNOWN").strip().rstrip(".")).upper()


def head(ws, r, labels, widths):
    for i, (h, w) in enumerate(zip(labels, widths), 1):
        c = ws.cell(row=r, column=i, value=h); c.font, c.fill = W, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center"); ws.column_dimensions[c.column_letter].width = w


def main():
    cur = dash.connect(dash.load_config()).cursor()
    q = lambda s: cur.execute(s).fetchall()  # noqa: E731
    raw = []   # (src, party, date, kind, entry no, gate pass, beam number)   kind: out_empty / in_filled / in_empty / out_filled
    for an, td, tno, gp, code in q("""SELECT a.an, m.td, m.tno, m.gp_no, d.beam_code FROM dostt.wprdtm m JOIN dostt.wprdtd d ON d.tno=m.tno AND d.td=m.td AND d.tt=m.tt
                                       LEFT JOIN dostt.ach a ON a.ac=m.ac1 WHERE m.tt='OGP_EMPTY_BEAMS'"""):
        raw.append(("old", an, td, "out_empty", tno, gp, code, "OGP_EMPTY_BEAMS"))
    for an, td, tt, tno, gp, code in q("""SELECT a.an, m.td, m.tt, m.tno, m.gp_no, b.bcode FROM dostt.wytm m JOIN dostt.wybeams b ON b.tno=m.tno AND b.td=m.td AND b.tt=m.tt
                                           LEFT JOIN dostt.ach a ON a.ac=NVL(m.ac1,m.ac2) WHERE m.tt IN ('IGP_SIZED_BEAMS','OGP_SIZED_BEAMS')"""):
        raw.append(("old", an, td, "in_filled" if tt == "IGP_SIZED_BEAMS" else "out_filled", tno, gp, code, tt))
    for an, td, tt, tno, gp, code in q("""SELECT a.an, m.td, m.tt, m.tno, m.gp_no, bm.bcode FROM dostex.wv_gp_beamsm m JOIN dostex.wv_gp_beamsd d ON d.trans_no=m.trans_no
                                           JOIN dostex.beams bm ON bm.beam_id=d.beam_id LEFT JOIN dostex.ach a ON a.ac=m.ac1"""):
        raw.append(("new", an, td, "out_empty" if tt == "OGP_EMPTY_BEAMS" else "in_empty", tno, gp, code, tt))
    for an, td, tt, tno, gp, code in q("""SELECT a.an, m.td, m.tt, m.tno, m.gp_no, b.bcode FROM dostex.wytm m JOIN dostex.wybeams b ON b.trans_no=m.trans_no
                                           LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2) WHERE m.tt IN ('IGP_SIZED_BEAMS','OGP_SIZED_BEAMS')"""):
        raw.append(("new", an, td, "in_filled" if tt == "IGP_SIZED_BEAMS" else "out_filled", tno, gp, code, tt))

    rows, seen, issues, early = [], set(), [], defaultdict(int)
    for src, an, td, kind, tno, gp, code, tt in raw:
        an = norm(an)
        key = (src, tt, tno, td.date(), code)
        if kind == "out_empty" and key in seen:
            issues.append((td.date(), an, src, f"{'Old' if src == 'old' else 'New'} ERP empty-beam gate pass {tno} (GP {gp}) lists beam {code} twice - counted once"))
            continue
        seen.add(key)
        mv = MOVE.get((src, tt, tno, str(td.date())))
        if mv:
            issues.append((td.date(), an, src, f"New ERP empty-beam gate pass {tno} (GP {gp}) booked to {an}; the same beams came back from {mv} the next day - moved"))
            an = norm(mv)
        if td < START:
            early[an] += 1
            continue
        rows.append((an, td, kind, src, tno, gp, code))
    issues = list(dict.fromkeys(issues))

    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    order_k = {"out_empty": 0, "out_filled": 0, "in_filled": 1, "in_empty": 1}
    summ, ledger = [], []
    for an, lst in by.items():
        lst.sort(key=lambda r: (r[1], order_k[r[2]]))
        own = any(o in an for o in OWN)
        cnt = defaultdict(int); bal = mn = 0; mnd = None; part = {"old": defaultdict(int), "new": defaultdict(int)}
        old_end = None
        for r in lst:
            cnt[r[2]] += 1; part[r[3]][r[2]] += 1
            if own:      # their beams come in filled; going back = empty or filled beams sent out
                bal += 1 if r[2] in ("in_filled", "in_empty") else -1
            else:        # our empty beams go out; coming back = filled or empty
                bal += 1 if r[2] in ("out_empty", "out_filled") else -1
            if bal < mn:
                mn, mnd = bal, r[1]
            if r[3] == "old":
                old_end = bal
            ledger.append((an, r[1].date(), r[3].upper() + " ERP", {"out_empty": "empty beam sent", "in_filled": "filled beam received",
                           "in_empty": "empty beam returned", "out_filled": "filled beam sent out"}[r[2]], r[4], r[5], r[6], bal))
        last = lst[-1][1].date()
        should = bal - min(mn, 0)
        mn_old = min([0] + [x[7] for x in ledger if x[0] == an and x[2] == "OLD ERP"])
        recent_out = sum(1 for r in lst if r[2] == "out_empty" and (date.today() - r[1].date()).days <= 30)
        where = []
        if old_end:
            where.append(f"old ERP left {old_end:+d} at 30-Sep-2023")
        new_bal = bal - (old_end or 0)
        if new_bal:
            where.append(f"new ERP {new_bal:+d} since 1-Oct-2023")
        if own:
            txt = (f"OWN BEAMS: received {cnt['in_filled']} filled, sent back {cnt['out_empty'] + cnt['out_filled']} - "
                   + ("squared." if bal == 0 else (f"mill still holds {bal} of their beams." if bal > 0 else f"mill sent back {-bal} more than it received.")))
            col = GRN if bal == 0 else AMB
        elif should == 0:
            txt, col = "Squared - every empty beam sent came back.", GRN
        elif (old_end or 0) <= 0 and should > 0 and recent_out >= should:
            txt, col = f"Holds {should} beams sent in the last 30 days - being sized (normal).", AMB
        else:
            txt = (f"CHECK: by count it should still hold {should} of our beams (balance {bal:+d}"
                   + (f"; lowest {mn:+d} on {mnd:%d-%b-%y} ({'old' if mnd < NEW_ERP else 'new'} ERP) = beams returned with no send entered" if mn < 0 else "")
                   + f"). Comes from: {'; '.join(where) or '-'}. Last movement {last:%d-%b-%y}.")
            col = RED
        summ.append((an, own, cnt, bal, mn, mnd, should, old_end, last, txt, col, part))

    wb = Workbook(); sm = wb.active; sm.title = "Summary"
    sm["A1"] = f"BEAM LEDGER BY PARTY - old + new ERP, by count · {date.today():%d-%b-%Y}"; sm["A1"].font = T
    sm["A2"] = ("From 8-Oct-2015 (first empty-beam gate pass in the old ERP) to today. Beam numbers ignored. Booking errors corrected (see 'Booking errors'). "
                "ERP read only."); sm["A2"].font = NOTE
    sm["A3"] = "NEW ERP STARTS 1-OCT-2023. Old ERP (DOSTT) = 8-Oct-2015 to 30-Sep-2023; new ERP (DOSTEX) = 1-Oct-2023 to today."; sm["A3"].font = B
    head(sm, 4, ["Party", "OLD ERP sent (to 30-Sep-23)", "OLD ERP back", "OLD ERP balance at 30-Sep-2023", "NEW ERP sent (from 1-Oct-23)", "NEW ERP back",
                 "NEW ERP change", "BALANCE TODAY", "Lowest running balance (which ERP)", "Should hold now (if every entry is right)", "Last movement", "Verdict"],
         [36, 9, 8, 11, 9, 8, 9, 9, 18, 11, 11, 90])
    r = 5
    for an, own, cnt, bal, mn, mnd, should, old_end, last, txt, col, part in sorted(summ, key=lambda x: (x[10] != RED, x[10] != AMB, x[0])):
        po, pn = part["old"], part["new"]
        if own:
            so, bo, sn, bn = po["out_empty"] + po["out_filled"], po["in_filled"], pn["out_empty"] + pn["out_filled"], pn["in_filled"] + pn["in_empty"]
        else:
            so, bo, sn, bn = po["out_empty"] + po["out_filled"], po["in_filled"] + po["in_empty"], pn["out_empty"] + pn["out_filled"], pn["in_filled"] + pn["in_empty"]
        vals = [an + (" (own beams)" if own else ""), so, bo, old_end or 0, sn, bn, bal - (old_end or 0), bal,
                f"{mn:+d} ({mnd:%d-%b-%y}, {'old' if mnd < NEW_ERP else 'new'} ERP)" if mnd else "0", 0 if own else should, last, txt]
        for i, v in enumerate(vals, 1):
            c = sm.cell(row=r, column=i, value=v); c.alignment = Alignment(wrap_text=True, vertical="top")
        sm.cell(row=r, column=11).number_format = "dd-mmm-yy"; sm.cell(row=r, column=12).fill = col
        r += 1
    sm.cell(row=r, column=1, value="ALL PARTIES").font = B
    sz = [x for x in summ if not x[1]]
    for i, v in [(2, sum(x[11]["old"]["out_empty"] for x in sz)), (3, sum(x[11]["old"]["in_filled"] + x[11]["old"]["in_empty"] for x in sz)),
                 (4, sum(x[7] or 0 for x in sz)), (5, sum(x[11]["new"]["out_empty"] for x in sz)), (6, sum(x[11]["new"]["in_filled"] + x[11]["new"]["in_empty"] for x in sz)),
                 (7, sum(x[3] - (x[7] or 0) for x in sz)), (8, sum(x[3] for x in sz)), (10, sum(x[6] for x in sz))]:
        sm.cell(row=r, column=i, value=v).font = B
    sm.cell(row=r, column=1, value="ALL SIZING PARTIES (own-beam parties excluded)").font = B
    notes = [f"Filled beams received before 8-Oct-2015 (empty beams were not being entered yet), left out of the balance: "
             + ", ".join(f"{k}: {v}" for k, v in sorted(early.items())),
             "'Should hold now' = balance today less the lowest running balance: if a party returned more beams than were entered as sent, "
             "those sends were missed in the ERP (or the beams were with it before the records start), so it must still hold that many more.",
             "Own-beam parties (Hiba, Babar, Umer, Niaz, Ibraheem, Chohan, Akeel, Adeel) brought filled beams on their own beams - judged on received vs sent back.",
             "The new ERP's own empty-beam report counts empty beams RETURNED as sent (+1 instead of -1) - do not use it."]
    for i, n_ in enumerate(notes, r + 2):
        sm.cell(row=i, column=1, value="• " + n_).font = NOTE
    iss = wb.create_sheet("Booking errors")
    head(iss, 1, ["Date", "Party", "ERP", "Issue"], [12, 36, 6, 130])
    for x in sorted(issues):
        iss.append(list(x)); iss.cell(row=iss.max_row, column=1).number_format = "dd-mmm-yy"
    lg = wb.create_sheet("Ledger")
    head(lg, 1, ["Party", "Date", "ERP", "Movement", "Entry no.", "Gate pass", "Beam", "Running balance"], [36, 11, 9, 20, 8, 9, 8, 10])
    for x in sorted(ledger, key=lambda z: (z[0], z[1])):
        lg.append(list(x)); lg.cell(row=lg.max_row, column=2).number_format = "dd-mmm-yy"
    lg.freeze_panes = "A2"; lg.auto_filter.ref = f"A1:H{lg.max_row}"
    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    for x in sorted(summ, key=lambda x: x[0]):
        an, own, cnt, bal, mn, mnd, should, old_end, last, txt, col, part = x
        print(f"{an[:34]:34} out {cnt['out_empty']:4} in {cnt['in_filled']:4}+{cnt['in_empty']} outF {cnt['out_filled']} | old-end {old_end} | bal {bal:+d} low {mn:+d} should {should} | {txt[:110]}")
    print("early:", dict(early)); print("issues:", len(issues))


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
