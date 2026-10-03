"""
Builds demo/demo_erp.db: a fictional mill ("Sitara Weaving") on a fictional ERP
("MillPro") whose tables look nothing like the standard model - to show that a
mapping file is all it takes to connect a new ERP (mappings/demo_sqlite.toml).

About six months of data ending yesterday, with known mistakes planted for the
audit to find (see PLANTED below). Fully deterministic for a given end date.

    python demo/build_demo_erp.py [--end 2026-10-02] [--out demo/demo_erp.db]
"""

import argparse
import random
from collections import defaultdict
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

ITEMS = [  # code, name, unit, group, typical rate, used per month, issued per loom?
    ("SP-001", "Picker (leather)", "pcs", "SPARES", 450, 120, True),
    ("SP-002", "Picking stick", "pcs", "SPARES", 1800, 25, True),
    ("SP-003", "Heald wire 280 mm", "pcs", "SPARES", 9, 4000, True),
    ("SP-004", "Drop wire", "pcs", "SPARES", 6, 3000, True),
    ("SP-005", "Reed 72s", "pcs", "SPARES", 14500, 2, True),
    ("SP-006", "Temple roller", "pcs", "SPARES", 2600, 10, True),
    ("SP-007", "Weft fork", "pcs", "SPARES", 950, 8, True),
    ("SP-008", "Shuttle", "pcs", "SPARES", 3200, 6, True),
    ("SP-009", "V-belt B-52", "pcs", "SPARES", 780, 14, True),
    ("SP-010", "Bearing 6205", "pcs", "SPARES", 650, 30, True),
    ("SP-011", "Bearing 6305", "pcs", "SPARES", 820, 12, True),
    ("SP-012", "Let-off worm gear", "pcs", "SPARES", 5200, 2, True),
    ("SP-013", "Take-up sand roller cover", "m", "SPARES", 1350, 6, True),
    ("SP-014", "Sley sword bush", "pcs", "SPARES", 240, 40, True),
    ("SP-015", "Crank arm pin", "pcs", "SPARES", 380, 20, True),
    ("CH-001", "PVA sizing chemical", "kg", "CHEMICAL", 610, 900, False),
    ("CH-002", "Maize starch", "kg", "CHEMICAL", 140, 2500, False),
    ("CH-003", "Sizing wax", "kg", "CHEMICAL", 520, 200, False),
    ("CH-004", "Softener", "kg", "CHEMICAL", 390, 150, False),
    ("LU-001", "Lube oil 68", "ltr", "LUBRICANT", 950, 120, False),
    ("LU-002", "Grease EP-2", "kg", "LUBRICANT", 1150, 40, False),
    ("PK-001", "Polybag 60x90", "pcs", "PACKING", 18, 3000, False),
    ("PK-002", "Carton 5-ply", "pcs", "PACKING", 210, 400, False),
    ("PK-003", "Packing strap", "kg", "PACKING", 330, 60, False),
    ("PK-004", "Fabric roll core", "pcs", "PACKING", 95, 600, False),
    ("EL-001", "Contactor 32A", "pcs", "ELECTRICAL", 4200, 3, True),
    ("EL-002", "Proximity sensor", "pcs", "ELECTRICAL", 2900, 5, True),
    ("EL-003", "LED tube light", "pcs", "ELECTRICAL", 650, 15, False),
    ("GN-001", "Cotton waste (cleaning)", "kg", "GENERAL", 120, 80, False),
    ("GN-002", "Gloves", "pair", "GENERAL", 150, 60, False),
]
SUPPLIERS = [("V01", "Faisal Mill Stores"), ("V02", "Al-Noor Textile Spares"), ("V03", "Chenab Chemicals"),
             ("V04", "Punjab Bearings House"), ("V05", "Madina Packages"), ("V06", "Star Electric Co")]
SUPPLIER_FOR = {"SPARES": ["V01", "V02", "V04"], "CHEMICAL": ["V03"], "LUBRICANT": ["V03", "V04"],
                "PACKING": ["V05"], "ELECTRICAL": ["V06", "V01"], "GENERAL": ["V01", "V05"]}
CUSTOMERS = [("C01", "Gul Fabrics (Pvt) Ltd"), ("C02", "Lahore Denim Exports"), ("C03", "Kohinoor Home Textiles")]
LOOMS = 24
QUALITIES = [("Q-6060", "60x60 / 92x88 63\"", 88), ("Q-4040", "40x40 / 110x80 67\"", 80), ("Q-2020", "20x20 / 60x56 98\"", 56)]
USERS = ["store1", "store2", "acc1", "acc2", "prod1"]

COA = [  # account, title, class, party
    ("1101", "Cash in hand - main", "CASH", None), ("1102", "Petty cash - mill", "CASH", None),
    ("1201", "Meezan Bank current a/c", "BANK", None), ("1202", "HBL current a/c", "BANK", None),
    ("1501", "Store and spares stock", "STOCK", None), ("1601", "Plant and machinery", "FA", None),
    ("3001", "Share capital", "CAP", None), ("4101", "Conversion income", "INC", None), ("4102", "Fabric sales", "INC", None),
    ("5101", "Wages", "EXP", None), ("5102", "Electricity", "EXP", None), ("5103", "Store consumption", "EXP", None),
    ("5104", "Freight", "EXP", None), ("5105", "Repairs", "EXP", None),
]
COA += [(f"2{c[1:]}", n, "AP", c) for c, n in SUPPLIERS] + [(f"13{c[1:]}", n, "AR", c) for c, n in CUSTOMERS]

PLANTED = """Planted for the audit (all dated "yesterday" unless noted):
  finance  - double payment to Punjab Bearings House (same amount, 2 days apart)
           - a cash payment keyed yesterday but dated 60 days earlier (backdated)
           - an unbalanced journal voucher (so the trial balance is out)
           - a large bank payment (Rs 750,000)
  store    - issue slip with quantity 0
           - Bearing 6205 issued twice to LOOM-03 within 2 days (qty 1 each)
           - Reed 72s bought at ~3x the last rate
           - Lube oil 68 bought at ~1.6x what another supplier charged recently
           - the same supplier bill number on two GRNs, same amount
           - Proximity sensor stock goes negative
  weaving  - LOOM-07 has no production for the last 9 days
           - LOOM-11 efficiency over 100% on a full shift (RPM keyed wrong)
           - LOOM-15 rejection about 20% on its contract"""


def build(out, end):
    rnd = random.Random(7)
    start = end - timedelta(days=183)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    db = sqlite3.connect(out)
    # A marker so the product recognises its own fictional demo data and allows it without a licence; a real ERP
    # copy renamed to demo_erp.db will not have it, so it still needs a licence (smart_layer/licence.py is_demo).
    db.executescript("CREATE TABLE _smartlayer_demo (token TEXT);")
    db.execute("INSERT INTO _smartlayer_demo VALUES ('Sitara-Weaving-demo-v1')")
    db.executescript("""
        CREATE TABLE inv_item_master (itm_id TEXT PRIMARY KEY, itm_desc TEXT, uom_cd TEXT, grp_cd TEXT);
        CREATE TABLE inv_opening (itm_id TEXT, qty REAL);
        CREATE TABLE inv_txn_hdr (txn_id INTEGER PRIMARY KEY, txn_type TEXT, txn_no TEXT, txn_dt TEXT, created_on TEXT,
                                  created_by TEXT, vendor_id TEXT, vendor_bill TEXT, dept_cd TEXT);
        CREATE TABLE inv_txn_dtl (txn_id INTEGER, line_no INTEGER, itm_id TEXT, qty REAL, unit_price REAL);
        CREATE TABLE bp_partner (bp_id TEXT PRIMARY KEY, bp_name TEXT, bp_role TEXT);
        CREATE TABLE gl_coa (acct_no TEXT PRIMARY KEY, acct_title TEXT, acct_class TEXT, bp_id TEXT);
        CREATE TABLE gl_vch_hdr (vch_id INTEGER PRIMARY KEY, vch_type TEXT, vch_no TEXT, vch_dt TEXT, posted_on TEXT,
                                 posted_by TEXT, remarks TEXT);
        CREATE TABLE gl_vch_dtl (vch_id INTEGER, acct_no TEXT, dr_amt REAL, cr_amt REAL);
        CREATE TABLE prd_loom (loom_id TEXT PRIMARY KEY, loom_no TEXT, hall TEXT);
        CREATE TABLE prd_shift_log (log_id INTEGER PRIMARY KEY, log_dt TEXT, loom_id TEXT, shift_cd TEXT, sort_no TEXT,
                                    order_ref TEXT, beam_ref TEXT, rpm REAL, ppi REAL, shift_mins REAL, picks REAL,
                                    grade_a_mtr REAL, grade_b_mtr REAL, reject_mtr REAL, keyed_on TEXT);
    """)
    db.executemany("INSERT INTO inv_item_master VALUES (?,?,?,?)", [i[:4] for i in ITEMS])
    db.executemany("INSERT INTO bp_partner VALUES (?,?,?)",
                   [(c, n, "V") for c, n in SUPPLIERS] + [(c, n, "C") for c, n in CUSTOMERS])
    db.executemany("INSERT INTO gl_coa VALUES (?,?,?,?)", COA)
    db.executemany("INSERT INTO prd_loom VALUES (?,?,?)",
                   [(f"L{n:02d}", f"LOOM-{n:02d}", "Hall A" if n <= 12 else "Hall B") for n in range(1, LOOMS + 1)])

    cprod, cfresh = defaultdict(float), defaultdict(float)
    lsegs, lday = defaultdict(list), defaultdict(float)          # per loom: beam segments [ref, first, last, meters]
    stock = {}
    for code, *_rest in ITEMS:
        monthly = _rest[4]
        stock[code] = round(monthly * rnd.uniform(1.0, 2.5))
    stock["EL-002"] = 4      # proximity sensors run short (planted negative later)
    db.executemany("INSERT INTO inv_opening VALUES (?,?)", stock.items())

    ids = {"txn": 0, "vch": 0, "log": 0}
    nums = {"GRN": 1000, "ISS": 5000}
    last_rate = {}

    def keyed(d, lag_days=None):
        lag = lag_days if lag_days is not None else rnd.choice([0, 0, 0, 1, 1, 2])
        t = datetime.combine(d + timedelta(days=lag), datetime.min.time()) + timedelta(hours=rnd.randint(9, 18), minutes=rnd.randint(0, 59))
        return min(t, datetime.combine(end, datetime.min.time()) + timedelta(hours=19)).isoformat(sep=" ")

    def txn(kind, d, lines, vendor=None, bill=None, dept=None, lag=None, user=None):
        ids["txn"] += 1
        nums[kind] += 1
        db.execute("INSERT INTO inv_txn_hdr VALUES (?,?,?,?,?,?,?,?,?)",
                   (ids["txn"], kind, f"{kind}-{nums[kind]}", d.isoformat(), keyed(d, lag),
                    user or rnd.choice(USERS[:2]), vendor, bill, dept))
        for n, (code, qty, price) in enumerate(lines, 1):
            db.execute("INSERT INTO inv_txn_dtl VALUES (?,?,?,?,?)", (ids["txn"], n, code, qty, price))
            stock[code] += qty if kind == "GRN" else -qty
        return f"{kind}-{nums[kind]}"

    def voucher(vtype, d, lines, lag=None, remarks="", user=None):
        ids["vch"] += 1
        db.execute("INSERT INTO gl_vch_hdr VALUES (?,?,?,?,?,?,?)",
                   (ids["vch"], vtype, f"{vtype}-{ids['vch']:05d}", d.isoformat(), keyed(d, lag), user or rnd.choice(USERS[2:4]), remarks))
        for acct, amt in lines:
            db.execute("INSERT INTO gl_vch_dtl VALUES (?,?,?,?)", (ids["vch"], acct, max(amt, 0), max(-amt, 0)))

    yesterday = end
    voucher("OPN", start - timedelta(days=1), [("1101", 1_500_000), ("1102", 50_000), ("1201", 9_000_000), ("1202", 4_000_000),
                                               ("1501", 3_000_000), ("1601", 60_000_000), ("3001", -77_550_000)], lag=0)

    d = start
    while d <= end:
        # store issues: spares to looms, consumables to departments
        for code, name, unit, grp, rate, monthly, per_loom in ITEMS:
            daily = monthly / 30.0
            if code == "EL-002" and d >= end - timedelta(days=20):
                continue                                   # planted below instead
            if rnd.random() < min(1.0, daily if daily < 1 else 0.85):
                qty = 1 if daily < 1.5 else max(1, round(rnd.gauss(daily / 0.85, daily / 4)))
                dept = f"LOOM-{rnd.randint(1, LOOMS):02d}" if per_loom else {"CHEMICAL": "SIZING", "PACKING": "FOLDING"}.get(grp, "GENERAL")
                if code == "SP-010" and dept == "LOOM-03" and d >= end - timedelta(days=4):
                    dept = "LOOM-04"                        # keep the planted repeat clean
                txn("ISS", d, [(code, qty, rate)], dept=dept)
        # purchases when stock runs below a month of use
        for code, name, unit, grp, rate, monthly, per_loom in ITEMS:
            if code == "EL-002":
                continue
            if stock[code] < monthly and rnd.random() < 0.5 and d < end - timedelta(days=1):
                vendor = rnd.choice(SUPPLIER_FOR[grp])
                price = round(rate * rnd.uniform(0.95, 1.08), 2)
                last_rate[code] = price
                txn("GRN", d, [(code, round(monthly * rnd.uniform(1.2, 2.0)), price)], vendor=vendor,
                    bill=f"{vendor}-{rnd.randint(100, 9999)}", lag=rnd.choice([0, 1, 1, 2, 3]))
        # weaving: two 12-hour shifts per loom
        for n in range(1, LOOMS + 1):
            if n == 7 and d > end - timedelta(days=9):
                continue
            q = QUALITIES[(n - 1) % 3]
            for shift in ("A", "B"):
                if rnd.random() < 0.03:
                    continue
                rpm, mins = 600 if q[0] != "Q-2020" else 520, 720
                eff = rnd.uniform(0.80, 0.92)
                if n == 11 and d == yesterday and shift == "A":
                    eff = 1.12
                picks = rpm * mins * eff
                meters = picks / (q[2] * 39.37)
                rej = meters * (0.20 if n == 15 and d == yesterday else rnd.uniform(0.005, 0.03))
                b = meters * rnd.uniform(0.01, 0.04)
                ids["log"] += 1
                cprod[(f"CON-{40 + (n - 1) % 3}", d)] += meters
                beam_ref = f"BM-{n:02d}{'AB'[((d.toordinal() + 3 * n) // 25) % 2]}"   # two beams per loom take turns (~25 days each)
                seg = lsegs[n][-1] if lsegs[n] and lsegs[n][-1][0] == beam_ref else None
                if seg is None:
                    seg = [beam_ref, d, d, 0.0]
                    lsegs[n].append(seg)
                seg[2], seg[3] = d, seg[3] + meters
                lday[(n, d)] += meters
                cfresh[(f"CON-{40 + (n - 1) % 3}", d)] += meters - b - rej
                db.execute("INSERT INTO prd_shift_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (ids["log"], d.isoformat(), f"L{n:02d}", shift, q[0], f"CON-{40 + (n - 1) % 3}", beam_ref,
                            rpm, q[2], mins, round(picks), round(meters - b - rej, 1), round(b, 1), round(rej, 1), keyed(d, 1)))
        # accounts: sales, receipts, wages, power, supplier payments
        if d.weekday() != 6:
            c = rnd.choice(CUSTOMERS)[0]
            amt = round(rnd.uniform(0.8, 2.4) * 1_000_000, -3)
            voucher("SV", d, [(f"13{c[1:]}", amt), ("4101", -amt)])
            if rnd.random() < 0.9:
                c = rnd.choice(CUSTOMERS)[0]
                amt = round(rnd.uniform(0.5, 2.0) * 1_000_000, -3)
                voucher("BRV", d, [("1201", amt), (f"13{c[1:]}", -amt)])
            for v, _ in SUPPLIERS:
                if rnd.random() < 0.18:                       # supplier bill (purchase on credit)
                    amt = round(rnd.uniform(40_000, 160_000), -2)
                    voucher("PV", d, [("1501", amt), (f"2{v[1:]}", -amt)])
                if rnd.random() < 0.15:
                    amt = round(rnd.uniform(20_000, 180_000), -2)
                    voucher(rnd.choice(["CPV", "BPV"]), d, [(f"2{v[1:]}", amt), ("1101" if amt < 60_000 else "1202", -amt)])
            voucher("JV", d, [("5103", 85_000), ("1501", -85_000)], lag=1)
        if d.day in (1, 15):
            voucher("BPV", d, [("5101", 2_400_000), ("1201", -2_400_000)])
        if d.day == 10:
            voucher("BPV", d, [("5102", 3_100_000), ("1202", -3_100_000)])
        if d.weekday() == 0:
            voucher("CRV", d, [("1101", 300_000), ("1202", -300_000)], remarks="cash drawn from bank")
        d += timedelta(days=1)

    # ---- planted mistakes --------------------------------------------------------------------
    y, y1, y2 = end, end - timedelta(days=1), end - timedelta(days=2)
    voucher("CPV", y2, [("204", 48_500), ("1101", -48_500)], lag=0, remarks="bearings bill 7781")
    voucher("CPV", y, [("204", 48_500), ("1101", -48_500)], lag=0, remarks="bearings bill 7781")
    voucher("CPV", end - timedelta(days=60), [("5105", 96_000), ("1101", -96_000)], lag=60, remarks="loom repair", user="acc2")
    voucher("JV", y, [("5104", 37_250), ("201", -35_250)], lag=0, remarks="freight")
    voucher("BPV", y, [("203", 750_000), ("1201", -750_000)], lag=0, remarks="PVA advance")
    txn("ISS", y, [("SP-014", 0, 240)], dept="LOOM-09")
    txn("ISS", y1, [("SP-010", 1, 2650)], dept="LOOM-03")
    txn("ISS", y, [("SP-010", 1, 2650)], dept="LOOM-03")
    txn("GRN", y, [("SP-005", 2, round(last_rate.get("SP-005", 14500) * 3, 2))], vendor="V02", bill="V02-5150")
    txn("GRN", y2, [("LU-001", 100, 940)], vendor="V04", bill="V04-3301")
    txn("GRN", y, [("LU-001", 100, 1520)], vendor="V03", bill="V03-8812")
    txn("GRN", y1, [("PK-003", 50, 330)], vendor="V05", bill="V05-777")
    txn("GRN", y, [("PK-003", 50, 330)], vendor="V05", bill="V05-777")
    txn("ISS", y, [("EL-002", 6, 2900)], dept="LOOM-18")
    build_yarn(db, cprod, cfresh, start, end)
    build_beams(db, lsegs, lday, end)
    build_scoreboard(out.with_name(out.stem.replace("_erp", "") + "_scoreboard.db"), lday, end)
    db.commit()
    db.close()
    return out


YARNS = [("Y60", "60/1 CTN", 60), ("Y40", "40/1 CTN", 40), ("Y20", "20/1 CTN", 20)]
CONTRACTS = [  # order, party, sort, epi, ppi, width, yarn, ordered m, rate/m, sizer
    ("CON-39", "C02", "Q-2020", 60, 56, 98, "Y20", 60_000, 27, "S01"),
    ("CON-40", "C01", "Q-6060", 92, 88, 63, "Y60", 180_000, 38, "S01"),
    ("CON-41", "C02", "Q-4040", 110, 80, 67, "Y40", 160_000, 31, "S01"),
    ("CON-42", "C03", "Q-2020", 60, 56, 98, "Y20", 200_000, 27, "S02"),
]
BAG_KG = 45.36
PLANTED += """
  yarn     - CON-41: weft issued ~60% above need over the last 7 days, last issue keyed at 22:45
           - CON-39 (finished contract): 8 bags weft issued 12 days ago, keyed at 23:20, no production
           - CON-42: a weft issue keyed 11 days after its date
           - Rehman Sizing: ~9% sizing loss on CON-42 (Ali Sizing ~3.5%)"""


def build_yarn(db, cprod, cfresh, start, end):
    """Contracts, yarn movements and fabric gate passes in MillPro's own tables."""
    rnd = random.Random(11)
    db.executescript("""
        CREATE TABLE yrn_master (yarn_cd TEXT PRIMARY KEY, descr TEXT, cnt REAL);
        CREATE TABLE so_order (order_ref TEXT PRIMARY KEY, bp_id TEXT, ord_dt TEXT, sort_no TEXT, epi REAL, ppi REAL,
                               width_in REAL, warp_yarn TEXT, weft_yarn TEXT, ord_mtr REAL, rate_mtr REAL, supply_type TEXT);
        CREATE TABLE yrn_txn (txn_id INTEGER PRIMARY KEY, txn_dt TEXT, entry_ts TEXT, txn_kind TEXT, gp_no TEXT,
                              order_ref TEXT, bp_id TEXT, user_id TEXT);
        CREATE TABLE yrn_txn_line (txn_id INTEGER, yarn_cd TEXT, bag_qty REAL, net_kg REAL);
        CREATE TABLE fab_gatepass (gp_no TEXT, gp_dt TEXT, order_ref TEXT, io TEXT, fresh_mtr REAL, sec_mtr REAL, rate REAL);
    """)
    db.executemany("INSERT INTO yrn_master VALUES (?,?,?)", YARNS)
    db.executemany("INSERT INTO bp_partner VALUES (?,?,?)", [("S01", "Ali Sizing", "S"), ("S02", "Rehman Sizing", "S")])
    count = {y: c for y, _, c in YARNS}
    for o, bp, sort, epi, ppi, w, y, om, rate, _ in CONTRACTS:
        db.execute("INSERT INTO so_order VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (o, bp, (start - timedelta(days=200 if o == "CON-39" else 20)).isoformat(), sort, epi, ppi, w, y, y, om, rate, "CMT"))
    ids = {"t": 0, "gp": 7000}

    def mv(kind, d, order, bp, yarn, kg, hour=None, keyed_on=None):
        ids["t"] += 1
        ts = datetime.combine(keyed_on or d, datetime.min.time()) + timedelta(hours=hour if hour is not None else rnd.randint(9, 17),
                                                                              minutes=rnd.randint(0, 59))
        db.execute("INSERT INTO yrn_txn VALUES (?,?,?,?,?,?,?,?)", (ids["t"], d.isoformat(), ts.isoformat(sep=" "), kind,
                                                                   f"YG-{ids['t']:05d}", order, bp, rnd.choice(["yarn1", "yarn2"])))
        db.execute("INSERT INTO yrn_txn_line VALUES (?,?,?,?)", (ids["t"], yarn, round(kg / BAG_KG, 1), round(kg, 1)))

    def lb_per_m(threads, width, cnt):
        return threads * width * 1.0936 / 840 / cnt

    # CON-39: an old contract, closed and squared up before the demo period
    mv("OPN", start, "CON-39", "C02", "Y20", 12 * BAG_KG)
    mv("YRET", start + timedelta(days=5), "CON-39", "C02", "Y20", 12 * BAG_KG)

    for o, bp, sort, epi, ppi, w, y, om, rate, sizer in CONTRACTS[1:]:
        weft_kg = lb_per_m(ppi, w, count[y]) * 0.45359237
        warp_kg = lb_per_m(epi, w, count[y]) * 0.45359237
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        mv("OPN", start, o, bp, y, round(sum(cprod[(o, start + timedelta(days=k))] for k in range(5)) * (weft_kg + warp_kg)
                                         / BAG_KG) * BAG_KG)
        fresh_since = 0.0
        rec_cover = 0
        for i, d in enumerate(days):
            m = cprod[(o, d)]
            need = m * weft_kg
            over = 1.6 if o == "CON-41" and d > end - timedelta(days=7) else rnd.uniform(0.97, 1.05)
            bags = round(need * over / BAG_KG)
            if bags:
                late = o == "CON-42" and d == end - timedelta(days=20)
                hour = 22 if o == "CON-41" and d == end else None
                mv("WFI", d, o, None, y, bags * BAG_KG, hour=hour, keyed_on=d + timedelta(days=11) if late else None)
            if d.weekday() == 5 and m:
                mv("WFR", d, o, None, y, rnd.uniform(4, 15))
            if i % 10 == 0:                                   # warp to the sizer for the next 10 days
                sent = sum(cprod[(o, d + timedelta(days=k))] for k in range(10)) * warp_kg / 0.9 * 1.04
                mv("SZO", d, o, sizer, y, round(sent / BAG_KG) * BAG_KG)
                loss = 0.09 if sizer == "S02" else rnd.uniform(0.03, 0.04)
                if d + timedelta(days=4) <= end:
                    mv("BMR", d + timedelta(days=4), o, sizer, y, round(sent / BAG_KG) * BAG_KG * (1 - loss))
            if i >= rec_cover:                                # party sends yarn every ~15 days
                ahead = sum(cprod[(o, d + timedelta(days=k))] for k in range(15))
                mv("YRCV", d, o, bp, y, round(ahead * (weft_kg + warp_kg / 0.9) * rnd.uniform(1.0, 1.04) / BAG_KG) * BAG_KG)
                rec_cover = i + 15
            fresh_since += cfresh[(o, d)]
            if i % 3 == 2 and fresh_since > 0:                # fabric to the party every 3 days
                ids["gp"] += 1
                db.execute("INSERT INTO fab_gatepass VALUES (?,?,?,?,?,?,?)",
                           (f"FG-{ids['gp']}", d.isoformat(), o, "O", round(fresh_since * 0.97, 1), 0, rate))
                fresh_since *= 0.03
            if i % 45 == 30:
                ids["gp"] += 1
                db.execute("INSERT INTO fab_gatepass VALUES (?,?,?,?,?,?,?)",
                           (f"FG-{ids['gp']}", d.isoformat(), o, "I", round(rnd.uniform(150, 400), 1), 0, rate))
    mv("WFI", end - timedelta(days=12), "CON-39", None, "Y20", 8 * BAG_KG, hour=23)


SHRINK = {"CON-40": 0.07, "CON-41": 0.08, "CON-42": 0.09}
ENDS = {c[0]: c[3] * c[5] for c in CONTRACTS}                          # ends per inch x width
SIZER = {c[0]: c[9] for c in CONTRACTS}
PLANTED += """
  beams    - LOOM-05: beam finishes in ~3 days, no sized beam waiting for its contract
           - LOOM-12: beam finishes in ~4 days, a sized beam is waiting (no alert)
           - LOOM-20: more fabric woven than its beam holds
           - Rehman Sizing: empty beam BM-X9 sent 60 days ago, never returned"""


def build_beams(db, lsegs, lday, end, warn=6):
    """Beam register and movements in MillPro's own tables, consistent with the production log."""
    db.executescript("""
        CREATE TABLE bm_beam (beam_no TEXT, bstat TEXT, set_no TEXT, order_ref TEXT, loom_id TEXT, sizer_id TEXT, ends REAL,
                              set_mtr REAL, woven_mtr REAL, mount_dt TEXT, recv_dt TEXT);
        CREATE TABLE bm_move (mv_dt TEXT, gp_no TEXT, beam_no TEXT, mv_type TEXT, bp_id TEXT, set_no TEXT, order_ref TEXT,
                              loom_id TEXT, set_mtr REAL, woven_mtr REAL);
    """)
    gp = [9000]

    def move(d, beam, kind, sizer=None, set_no=None, order=None, loom=None, length=None, woven=None):
        gp[0] += 1
        db.execute("INSERT INTO bm_move VALUES (?,?,?,?,?,?,?,?,?,?)", (d.isoformat(), f"BG-{gp[0]}", beam, kind, sizer, set_no,
                                                                      order, loom, length, woven))
    set_seq = [500]
    for n, segs in sorted(lsegs.items()):
        order = f"CON-{40 + (n - 1) % 3}"
        s, loom = SHRINK[order], f"L{n:02d}"
        rate = sum(lday[(n, end - timedelta(days=k))] for k in range(30)) / 30
        for i, (ref, first, last, meters) in enumerate(segs):
            set_seq[0] += 1
            set_no = f"S{set_seq[0]}"
            current = i == len(segs) - 1
            if not current:
                length = meters / (1 - s)
            else:
                elapsed = (end - first).days + 1
                length = meters / elapsed * 25 / (1 - s)
                left_days = (length * (1 - s) - meters) / rate if rate else 99
                if n == 5:
                    length = (meters + 3 * rate) / (1 - s)
                elif n == 12:
                    length = (meters + 4 * rate) / (1 - s)
                elif n == 20:
                    length = meters * 0.95
                elif order == "CON-41" and left_days <= warn:
                    length = (meters + 10 * rate) / (1 - s)        # keep loom 5 the only CON-41 loom without a backup
            move(first - timedelta(days=3), ref, "SRC", SIZER[order], set_no, order, None, round(length))
            move(first, ref, "TLM", None, set_no, order, loom, round(length))
            if not current:
                move(last + timedelta(days=1), ref, "FLC", None, set_no, order, loom, round(length), round(meters, 1))
                if last + timedelta(days=2) <= end:
                    move(last + timedelta(days=2), ref, "ESO", SIZER[order], None, None, None)
                    if i == len(segs) - 2:                       # the loom's other beam: at the sizer being filled
                        db.execute("INSERT INTO bm_beam VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                   (ref, "S", None, None, None, SIZER[order], None, None, None, None, None))
                continue
            db.execute("INSERT INTO bm_beam VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (ref, "L", set_no, order, loom, SIZER[order], ENDS[order], round(length), round(meters, 1),
                        first.isoformat(), (first - timedelta(days=3)).isoformat()))
            needs_backup = n == 12 or (n != 5 and order != "CON-41" and rate and
                                        (length * (1 - s) - meters) / rate <= warn)
            if needs_backup:
                set_seq[0] += 1
                db.execute("INSERT INTO bm_beam VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (f"BM-W{n:02d}", "W", f"S{set_seq[0]}", order, None, SIZER[order], ENDS[order], round(length), None,
                            None, (end - timedelta(days=2)).isoformat()))
                move(end - timedelta(days=2), f"BM-W{n:02d}", "SRC", SIZER[order], f"S{set_seq[0]}", order, None, round(length))
    for b in ("BM-E1", "BM-E2", "BM-E3"):
        db.execute("INSERT INTO bm_beam VALUES (?,?,?,?,?,?,?,?,?,?,?)", (b, "E", None, None, None, None, None, None, None, None, None))
    move(end - timedelta(days=60), "BM-X9", "ESO", "S02")
    db.execute("INSERT INTO bm_beam VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("BM-X9", "S", None, None, None, "S02", None, None, None, None, None))


PLANTED += """
  scoreboard (separate database, a second source)
           - LOOM-15: the scoreboard counted ~35% more than the ERP over 14 days (rolls not entered)
           - LOOM-22: the ERP shows ~40% more than the scoreboard counted (over-entered)"""


def build_scoreboard(path, lday, end):
    """A loom monitoring system's own database (like an OLMS scoreboard), separate from the ERP."""
    rnd = random.Random(5)
    path = Path(path)
    if path.exists():
        path.unlink()
    sb = sqlite3.connect(path)
    sb.execute("CREATE TABLE olms_day (loom_no INTEGER, dt TEXT, mtrs REAL, eff REAL, rpm REAL, stops INTEGER)")
    for (n, d), m in sorted(lday.items()):
        if m <= 0:
            continue
        counted = m * rnd.uniform(0.97, 1.03)
        if d > end - timedelta(days=14):
            counted *= 1.35 if n == 15 else (1 / 1.4 if n == 22 else 1)
        sb.execute("INSERT INTO olms_day VALUES (?,?,?,?,?,?)", (n, d.isoformat(), round(counted, 1), round(rnd.uniform(80, 92), 1),
                                                                 rnd.choice([520, 600]), rnd.randint(20, 90)))
    sb.commit()
    sb.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", help="last day of data (default: yesterday)")
    ap.add_argument("--out", default=str(Path(__file__).with_name("demo_erp.db")))
    a = ap.parse_args()
    end = date.fromisoformat(a.end) if a.end else date.today() - timedelta(days=1)
    print(f"Built {build(a.out, end)} with data up to {end}\n\n{PLANTED}")
