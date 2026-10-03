import pickle, collections, statistics as st, re
D = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_data.pkl","rb"))
L, _ = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","rb"))
# A. duplicates: same supplier, same item, same qty, same rate, different GRN within 45 days
print("== possible duplicate lines (same sup/item/qty/rate, other GRN <=45d)")
k = collections.defaultdict(list)
for r in L: k[(r['SUP'], r['ERP'], r['IC'], r['QTY'], r['RATE'])].append(r)
dups = []
for key, rows in k.items():
    rows.sort(key=lambda x: x['DD'])
    for a, b in zip(rows, rows[1:]):
        if a['GRN'] != b['GRN'] and (b['DD']-a['DD']).days <= 45: dups.append((b['AMT'], a, b))
dups.sort(key=lambda x: -x[0])
print(len(dups), "pairs; Rs of 2nd line", round(sum(d[0] for d in dups)), "; same bill no:", sum(1 for d in dups if d[1]['BNO']==d[2]['BNO'] and d[1]['BNO'] not in (None,'NILL')))
for amt, a, b in dups[:30]:
    print(f" {a['SUP'][:22]} | {a['IDESC']} | {a['QTY']}@{a['RATE']}=Rs {round(amt)} | {a['GRN']} {a['DD'].date()} bill {a['BNO']} || {b['GRN']} {b['DD'].date()} bill {b['BNO']} ({(b['DD']-a['DD']).days}d)")
# B. same supplier + same bill number on different GRNs
print("== bill number reused across GRNs for same supplier (excl NILL/blank)")
bb = collections.defaultdict(set); ba = collections.defaultdict(float); bd = {}
for r in L:
    b = (r['BNO'] or '').strip().upper()
    if b in ('', 'NILL', 'NIL', '0', '-'): continue
    bb[(r['SUP'], b)].add((r['GRN'], r['DD'].date())); ba[(r['SUP'], b, r['GRN'])] += r['AMT']
multi = {k: v for k, v in bb.items() if len(v) > 1}
print(len(multi), "cases")
for (s, b), v in sorted(multi.items(), key=lambda x: -sum(ba[(x[0][0], x[0][1], g)] for g, d in x[1]))[:25]:
    print(f" {s[:24]} bill {b}: " + "; ".join(f"{g} {d} Rs {round(ba[(s,b,g)])}" for g, d in sorted(v, key=lambda z: z[1])))
# C. NILL bill share by supplier and total
print("== NILL / blank bill numbers")
nb = collections.defaultdict(lambda: [0.0, 0.0])
for r in L:
    b = (r['BNO'] or '').strip().upper(); nill = b in ('', 'NILL', 'NIL') or 'NILL' in b
    nb[r['SUP']][0] += r['AMT']; nb[r['SUP']][1] += r['AMT'] if nill else 0
tot = sum(v[1] for v in nb.values()); print("total Rs on NILL/blank bills", round(tot))
for s, v in sorted(nb.items(), key=lambda x: -x[1][1])[:12]: print(" ", s, round(v[1]), f"{round(100*v[1]/v[0])}% of its spend")
# D. GST
print("== GST lines")
ach = {a['AC']: a for a in D['ach_new']}
gl = [r for r in L if (r['GST_PER'] or 0) > 0]
gs = collections.defaultdict(lambda: [0.0, 0, set(), None, None])
for r in gl:
    x = gs[(r['ERP'], r['SUP'])]; x[0] += r['AMT']; x[1] += 1; x[2].add(r['GST_PER']); x[3] = r['GST_NO']; x[4] = r['NTN']
for k2, v in sorted(gs.items(), key=lambda x: -x[1][0]): print(" ", k2, "Rs", round(v[0]), "GST value", round(v[0]*max(v[2])/100), v[1], "lines rates", v[2], "GST_NO", v[3], "NTN", v[4])
print("GST by FY:", {f: round(sum(r['AMT']*r['GST_PER']/100 for r in gl if r['FY']==f)) for f in sorted({r['FY'] for r in gl})})
# supplier GST registration of top suppliers & whether GST ever charged
print("== top suppliers: registered? GST charged?")
sp = collections.defaultdict(float)
for r in L: sp[(r['ERP'], r['SUP'], r['GST_NO'], r['NTN'])] += r['AMT']
for k2, v in sorted(sp.items(), key=lambda x: -x[1])[:25]: print(" ", k2, round(v), "gst lines", gs[(k2[0], k2[1])][1])
# E. freight / header gst_per
print("freight lines", sum(1 for r in L if (r['FREIGHT'] or 0) > 0), "header gst_per", collections.Counter(r['M_GST_PER'] for r in L))
