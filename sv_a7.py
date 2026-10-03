import pickle, collections, statistics as st, re
D = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_data.pkl","rb"))
L, _ = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","rb"))
# A. tight duplicates <=7 days, same sup/item/qty/rate
k = collections.defaultdict(list)
for r in L: k[(r['SUP'], r['ERP'], r['IC'], r['QTY'], r['RATE'])].append(r)
d7 = []
for key, rows in k.items():
    rows.sort(key=lambda x: x['DD'])
    for a, b in zip(rows, rows[1:]):
        if a['GRN'] != b['GRN'] and (b['DD']-a['DD']).days <= 7 and b['AMT'] >= 5000: d7.append((b['AMT'], a, b))
d7.sort(key=lambda x: -x[0])
print("== dup <=7d, >=Rs5k:", len(d7), round(sum(x[0] for x in d7)))
for amt, a, b in d7[:30]:
    print(f" {a['SUP'][:22]} | {a['IDESC']} | {a['QTY']}@{a['RATE']}=Rs {round(amt)} | {a['GRN']} {a['DD'].date()} bill {a['BNO']} || {b['GRN']} {b['DD'].date()} bill {b['BNO']}")
# B. whole-GRN duplicates: same supplier, same total and same line count, different GRN, <=30d
g = collections.defaultdict(lambda: [0.0, 0, None, None])
for r in L:
    x = g[r['GRN']]; x[0] += r['AMT']; x[1] += 1; x[2] = r['SUP']; x[3] = r['DD']
gk = collections.defaultdict(list)
for grn, x in g.items(): gk[(x[2], round(x[0]), x[1])].append((x[3], grn))
print("== identical GRN totals same supplier <=30d (total>=20k)")
for key, v in gk.items():
    v.sort()
    for a, b in zip(v, v[1:]):
        if (b[0]-a[0]).days <= 30 and key[1] >= 20000: print(" ", key, a[1], a[0].date(), b[1], b[0].date())
# C. price trends for fuel/oil items: by year-quarter, supplier, avg rate, qty
def trend(pat):
    rows = [r for r in L if re.search(pat, r['NK'][0])]
    t = collections.defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        key = (r['NK'][0], r['DD'].year, r['SUP'][:20]); t[key][0] += r['AMT']; t[key][1] += r['QTY']
    for key in sorted(t): print("  ", key, "qty", round(t[key][1]), "avg", round(t[key][0]/t[key][1], 1), "Rs", round(t[key][0]))
print("== OIL MOBILE HD-100 / SAE / CIRCLE / DELO"); trend(r'^OIL MOB')
print("== DIESEL by year/supplier"); trend(r'^DIESEL$')
print("== SOLAR"); trend(r'SOLAR (PANEL|INVERTER)')
