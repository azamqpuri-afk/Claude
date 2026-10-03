"""
65_beam_correction_list.py - correction list for the data-entry person, so the ERP's empty-beam stock report
(WV_EMPTY_BEAM_STOCK_V) becomes right. ERP read only - every correction is made by staff in the ERP forms.
Matching, per sizing party, in date order (new ERP, from 1-Oct-2023):
  a beam number sent empty (OGP_EMPTY_BEAMS) is matched by the next return of the same number from the same party
  (IGP_SIZED_BEAMS filled or IGP_EMPTY_BEAMS empty). Left over:
  - same number came back from ANOTHER party within 10 days            -> wrong party on the gate pass
  - an unmatched return near an unmatched send of the same party        -> beam number typed wrong on one of them
  - a return with nothing sent                                           -> send gate pass never entered
  - a send with nothing back (older than 30 days)                        -> check with the party
  duplicates on one gate pass, and the report's own sign error, are listed too. Hiba Fabrics brings its own beams - skipped.
python 65_beam_correction_list.py --upload <google sheet id>
"""
import importlib.util
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent
_s = importlib.util.spec_from_file_location("d", HERE / "40_business_dashboard.py")
dash = importlib.util.module_from_spec(_s); _s.loader.exec_module(dash)
OUT = HERE / "financial_statements" / "beam_entry_corrections.xlsx"
NAVY = PatternFill("solid", fgColor="1F3B57"); W = Font(bold=True, color="FFFFFF"); B = Font(bold=True)
T = Font(bold=True, size=15, color="1F3B57"); NOTE = Font(italic=True, color="4B4F5A")
TYPE_FILL = {"Report formula": "F8C9C9", "Wrong party": "FFE3B3", "Wrong beam number": "FFF1CC", "Duplicate line": "FFF1CC",
             "Send not entered": "DDEBFF", "Not back - confirm": "E8DDF5"}


def head(ws, r, labels, widths):
    for i, (h, w) in enumerate(zip(labels, widths), 1):
        c = ws.cell(row=r, column=i, value=h); c.font, c.fill = W, NAVY
        c.alignment = Alignment(wrap_text=True, vertical="center"); ws.column_dimensions[c.column_letter].width = w


def main():
    cur = dash.connect(dash.load_config()).cursor()
    q = lambda s: cur.execute(s).fetchall()  # noqa: E731
    ev = []   # (party, date, kind, gate pass no, entry no, beam)
    for an, td, tt, gp, tno, code in q("""SELECT a.an, TRUNC(m.td), m.tt, m.gp_no, m.tno, bm.bcode FROM dostex.wv_gp_beamsm m
            JOIN dostex.wv_gp_beamsd d ON d.trans_no=m.trans_no JOIN dostex.beams bm ON bm.beam_id=d.beam_id LEFT JOIN dostex.ach a ON a.ac=m.ac1"""):
        ev.append(((an or "").strip(), td.date(), "out" if tt == "OGP_EMPTY_BEAMS" else "in_empty", gp, tno, code))
    for an, td, gp, tno, code in q("""SELECT a.an, TRUNC(m.td), m.gp_no, m.tno, b.bcode FROM dostex.wytm m JOIN dostex.wybeams b ON b.trans_no=m.trans_no
            LEFT JOIN dostex.ach a ON a.ac=NVL(m.ac1,m.ac2) WHERE m.tt='IGP_SIZED_BEAMS'"""):
        ev.append(((an or "").strip(), td.date(), "in_filled", gp, tno, code))
    kind_name = {"out": "Empty beams sent (OGP)", "in_filled": "Sized beams received (IGP)", "in_empty": "Empty beams received back (IGP)"}
    items = []

    def add(typ, party, d_, gp, tno, kind, beams, problem, action):
        items.append(dict(typ=typ, party=party, date=d_, gp=gp, tno=tno, kind=kind_name.get(kind, kind), beams=beams, problem=problem, action=action))

    # 0. the report's own formula (fixed by the owner 1-Oct-2026 - listed only if the view still has the old sign)
    vt = q("SELECT text FROM all_views WHERE owner='DOSTEX' AND view_name='WV_EMPTY_BEAM_STOCK_V'")[0][0]
    i_ = vt.find("M.TT IN ('IGP_EMPTY_BEAMS')")
    if "-(1) as beams" not in vt[vt.rfind("SELECT", 0, i_):i_]:
      add("Report formula", "ALL SIZERS", None, "", "", "View WV_EMPTY_BEAM_STOCK_V", "",
        "Empty beams RETURNED by a sizer (IGP_EMPTY_BEAMS) are counted as +1, the same as beams sent - the report shows 2 extra beams at the sizer for every empty beam returned.",
        "Software vendor: in the IGP_EMPTY_BEAMS part of the view change '(1) as beams' to '-(1) as beams'. No data entry needed.")
    # 1. duplicates on one gate pass
    seen = defaultdict(int)
    for p, d_, k, gp, tno, code in ev:
        if k == "out":
            seen[(p, d_, gp, tno, code)] += 1
    for (p, d_, gp, tno, code), n in seen.items():
        if n > 1 and "HIBA" not in p.upper():
            add("Duplicate line", p, d_, gp, tno, "out", code, f"Beam {code} is listed {n} times on this gate pass.",
                f"Open the gate pass and delete the extra line of {code} (or correct it to the beam that was really sent).")
        elif n > 1:
            add("Duplicate line", p, d_, gp, tno, "out", code, f"Beam {code} is listed {n} times on this gate pass (Hiba's own beams).",
                "Check with the gate pass: if only one beam 00002 went, delete the extra line; if it was another beam, correct the number.")
    # 2. match sends and returns per party
    ev_ = sorted({(p, d_, k, gp, tno, code) for p, d_, k, gp, tno, code in ev}, key=lambda e: (e[1], 0 if e[2] == "out" else 1))
    open_ = defaultdict(list)          # (party, beam) -> open sends
    un_ret = []
    for e in ev_:
        p, d_, k, gp, tno, code = e
        if "HIBA" in p.upper():
            continue
        if k == "out":
            open_[(p, code)].append(e)
        elif open_[(p, code)]:
            open_[(p, code)].pop(0)
        else:
            un_ret.append(e)
    un_out = [e for L in open_.values() for e in L]
    used_o, used_r = set(), set()
    # 2a. wrong party: same beam back from another party within 10 days
    for r in un_ret:
        for o in un_out:
            if o in used_o or r in used_r:
                continue
            if o[5] == r[5] and o[0] != r[0] and 0 <= (r[1] - o[1]).days <= 10:
                used_o.add(o); used_r.add(r)
                grp = [x for x in un_out if x[0] == o[0] and x[3] == o[3] and x[1] == o[1]]
                add("Wrong party", o[0], o[1], o[3], o[4], "out", r[5],
                    f"Beam {r[5]} was entered as sent to {o[0]}, but it came back from {r[0]} on {r[1]:%d-%b-%y} (gate pass {r[3]}).",
                    f"Change the party on this empty-beam gate pass to {r[0]} (if the whole gate pass went there), or correct the beam.")
    # 2b. wrong beam number: unmatched return near an unmatched send of the same party (send before return, within 45 days)
    for r in sorted(un_ret, key=lambda x: x[1]):
        if r in used_r:
            continue
        cands = [o for o in un_out if o not in used_o and o[0] == r[0] and 0 <= (r[1] - o[1]).days <= 45]
        if cands:
            o = max(cands, key=lambda x: x[1])
            used_o.add(o); used_r.add(r)
            add("Wrong beam number", r[0], o[1], o[3], o[4], "out", f"{o[5]} → {r[5]}",
                f"Beam {o[5]} was sent on {o[1]:%d-%b-%y} but never came back as {o[5]}; beam {r[5]} came back on {r[1]:%d-%b-%y} "
                f"(gate pass {r[3]}) without being sent. One number was typed wrong.",
                f"Check the beam: correct {o[5]} to {r[5]} on this empty-beam gate pass (or {r[5]} to {o[5]} on the return gate pass {r[3]}).")
    # 2c. returns with no send
    for r in un_ret:
        if r in used_r:
            continue
        if r[1] <= date(2023, 10, 10):
            add("Send not entered", r[0], r[1], r[3], r[4], r[2], r[5],
                f"Beam {r[5]} came back at the start of the new ERP ({r[1]:%d-%b-%y}); it was sent while the old ERP was in use.",
                "No action needed (sent in the old ERP) - or enter an opening empty-beam gate pass dated 30-Sep-2023 if you want the report exact.")
        else:
            add("Send not entered", r[0], r[1], r[3], r[4], r[2], r[5],
                f"Beam {r[5]} came back from {r[0]} on {r[1]:%d-%b-%y}, but no empty-beam gate pass sending it was entered.",
                f"Enter the missing 'Empty beams sent' gate pass for beam {r[5]} to {r[0]} (date it was sent; remark 'entered late').")
    # 2d. sends with nothing back
    for o in un_out:
        if o in used_o:
            continue
        age = (date.today() - o[1]).days
        if age <= 30:
            continue       # still at the sizer, normal
        add("Not back - confirm", o[0], o[1], o[3], o[4], "out", o[5],
            f"Beam {o[5]} was sent to {o[0]} {age} days ago and has not come back (under this number) from anyone.",
            "Check the beam in the mill; if it is back, find the gate pass it came back on and correct the number; if not, ask the party.")
    items.sort(key=lambda x: (list(TYPE_FILL).index(x["typ"]), x["party"], x["date"] or date.min))

    wb = Workbook(); ws = wb.active; ws.title = "Corrections"
    ws["A1"] = f"EMPTY BEAM REPORT - ERP ENTRY CORRECTIONS · {date.today():%d-%b-%Y}"; ws["A1"].font = T
    ws["A2"] = ("For the data-entry person: make each correction in the ERP forms (not directly in the database) and tick 'Done'. "
                "Tick the item here when done so we can re-check. Prepared read-only from the ERP."); ws["A2"].font = NOTE
    head(ws, 4, ["#", "Type", "Party", "Date", "Gate pass", "Entry no.", "Entry type", "Beam(s)", "What is wrong", "What to do", "Done"],
         [5, 16, 28, 11, 9, 8, 22, 12, 60, 60, 8])
    for i, x in enumerate(items, 1):
        ws.append([i, x["typ"], x["party"], x["date"], x["gp"], x["tno"], x["kind"], x["beams"], x["problem"], x["action"], ""])
        for col in range(1, 12):
            ws.cell(row=ws.max_row, column=col).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=ws.max_row, column=2).fill = PatternFill("solid", fgColor=TYPE_FILL[x["typ"]])
        ws.cell(row=ws.max_row, column=4).number_format = "dd-mmm-yy"
    ws.freeze_panes = "A5"; ws.auto_filter.ref = f"A4:K{ws.max_row}"
    sm = wb.create_sheet("Summary", 0)
    sm["A1"] = "What these corrections do"; sm["A1"].font = T
    cnt = defaultdict(int)
    for x in items:
        cnt[x["typ"]] += 1
    head(sm, 3, ["Type", "Items", "Who", "Effect on the empty-beam report"], [22, 8, 22, 100])
    who = {"Report formula": "ERP person (one-line change to the view, file ready in oracle_changes)", "Wrong party": "Data entry", "Wrong beam number": "Data entry", "Duplicate line": "Data entry",
           "Send not entered": "Data entry", "Not back - confirm": "Storekeeper, then data entry"}
    now = dict(q("SELECT an, SUM(beams) FROM dostex.wv_empty_beam_stock_v GROUP BY an"))
    fixed = dict(q("""SELECT an, SUM(CASE WHEN tt = 'IGP_EMPTY_BEAMS' THEN -1 ELSE beams END) FROM dostex.wv_empty_beam_stock_v GROUP BY an"""))
    chg = ", ".join(f"{k.split()[0].title()} {now.get(k, 0)} -> {fixed.get(k, 0)}" for k in sorted(set(now) | set(fixed)) if now.get(k, 0) != fixed.get(k, 0))
    eff = {"Report formula": f"Returned empty beams stop being added: {chg or 'already fixed'} (total {sum(now.values())} -> {sum(fixed.values())}).",
           "Wrong party": "Beams move from the wrong sizer to the right one.",
           "Wrong beam number": "Totals do not change, but each beam's history becomes right (the report per beam stops showing ghost beams).",
           "Duplicate line": "Removes a beam counted twice.",
           "Send not entered": "Removes negative balances (a sizer returning more beams than it was sent).",
           "Not back - confirm": "Either the beam is found (fix its number) or it is really at the party."}
    for i, k in enumerate(TYPE_FILL, 4):
        sm.cell(row=i, column=1, value=k).fill = PatternFill("solid", fgColor=TYPE_FILL[k])
        for j, v in enumerate([cnt.get(k, 0), who[k], eff[k]], 2):
            sm.cell(row=i, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
    r = 4 + len(TYPE_FILL) + 1
    for t in ["Old ERP (before 1-Oct-2023) - not in this report, but to recover from the parties (count by the combined ledger):",
              "FM Textile Mills 36 beams · Umar Sizing 5 · Mian Sizing 4 · Sadaqat Ltd 4 · AR (Store) 3 · Haq Textile 2 · Fairdeal Mills 1 "
              "(FM and Chenab were both active then: some of FM's 36 may have come back booked to Chenab - ask FM for a written count).",
              "After the view fix and these entries, the report should show only beams really at sizers. Beam-by-beam check of each sizer's "
              "balance: sheet 'Empty beams at sizers - ledger tally'."]:
        sm.cell(row=r, column=1, value=t).font = NOTE; r += 1
    # owner, 1-Oct-2026: only Haji Taj Babu holds our beams. The count corrections that make the report right - do these first.
    fs = wb.create_sheet("Do first - counts", 0)
    fs["A1"] = "COUNT CORRECTIONS - DO FIRST (agreed 1-Oct-2026)"; fs["A1"].font = T
    fs["A2"] = ("Only Haji Taj Babu has our beams (owner). These 5 steps make the ERP empty-beam report show exactly that. "
                "The long list on the 'Corrections' tab only fixes individual beam histories - it changes no totals."); fs["A2"].font = NOTE
    head(fs, 4, ["Step", "Who", "Entry", "What to do", "Ali Hajwery", "Manzoor", "Haji Taj Babu", "Done"], [6, 16, 34, 80, 12, 10, 14, 8])
    for row in [
        ["", "", "Now", "Report as it stands", 3, 7, 12, ""],
        [1, "ERP person", "View WV_EMPTY_BEAM_STOCK_V", "Sign fix: in the IGP_EMPTY_BEAMS part change '(1) as beams' to '-(1) as beams' "
                                                         "(file oracle_changes\\WV_EMPTY_BEAM_STOCK_V_original_2026-10-01.sql, edit one line, run as DOSTEX).", 3, -1, 4, ""],
        [2, "Data entry", "Empty beams sent - GP 13, 10-Nov-23, entry 8 (Ali Hajwery)", "Beam 00336 is on it twice: delete one line.", 2, -1, 4, ""],
        [3, "Data entry", "Empty beams sent - GP 27, 31-Dec-23, entry 17 (Ali Hajwery)",
         "Remove ONLY beam 00022 from it, and enter beam 00022 on a new 'empty beams sent' gate pass to MANZOOR dated 31-Dec-23. "
         "Do NOT change the party of the whole gate pass (its other 3 beams were Ali Hajwery's).", 1, 0, 4, ""],
        [4, "Data entry", "New 'empty beams received back' - Ali Hajwery",
         "Beam 00038, dated 13-Sep-24, remark 'return entered late'. It was sent 01-May-24 (GP 1, entry 39) and is in our mill now.", 0, 0, 4, ""],
        [5, "Data entry", "New 'empty beams sent' - Haji Taj Babu",
         "Beam 00240, dated just before 13-Nov-24, remark 'send entered late'. It came back 13-Nov-24 (GP 33, entry 94) with no send entered.", 0, 0, 5, ""],
    ]:
        fs.append(row)
        for col in range(1, 9):
            fs.cell(row=fs.max_row, column=col).alignment = Alignment(wrap_text=True, vertical="top")
    fs.cell(row=fs.max_row + 2, column=1, value="Result: only Haji Taj Babu, 5 beams (00001, 00012, 00023, 00029, 00214 - sent 25-Sep-26). "
                                                "Steps 4-5: if the paper gate passes show a different beam, use that one - the counts come out the same.").font = NOTE
    OUT.parent.mkdir(exist_ok=True)
    wb.save(OUT)
    for x in items:
        print(x["typ"], "|", x["party"][:22], "|", x["date"], "| GP", x["gp"], "|", x["beams"], "|", x["problem"][:110])
    print(dict(cnt))


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
