import pickle, collections, statistics as st, re
L, _ = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","rb"))
by = collections.defaultdict(list)
for r in L: by[r['NK']].append(r)
# A. 90-day window outliers
flags = []
for k, rows in by.items():
    for r in rows:
        comp = [x['RATE'] for x in rows if x['GRN'] != r['GRN'] and abs((x['DD']-r['DD']).days) <= 90]
        if len(comp) < 2: continue
        ref = st.median(comp)
        if r['RATE'] > 1.25*ref: flags.append(((r['RATE']-ref)*r['QTY'], r, ref))
flags.sort(key=lambda x: -x[0])
print("== 90d outliers", len(flags), round(sum(f[0] for f in flags)), ">=5k:", sum(1 for f in flags if f[0]>=5000), round(sum(f[0] for f in flags if f[0]>=5000)))
fs = collections.defaultdict(lambda: [0.0, 0])
for ex, r, ref in flags: fs[r['SUP']][0] += ex; fs[r['SUP']][1] += 1
print([(s, round(v[0]), v[1]) for s, v in sorted(fs.items(), key=lambda x: -x[1][0])[:15]])
for ex, r, ref in flags[:25]:
    print(f" {r['GRN']} {r['DD'].date()} {r['SUP'][:24]} | {r['IDESC']} | {r['QTY']}@{r['RATE']} vs {round(ref,2)} (+{round(100*(r['RATE']/ref-1))}%) Rs {round(ex)}")
# B. same item same month different suppliers, spread
print("== same item, same month, 2+ suppliers, max/min rate >= 1.5 (excess = (max-min)*qty of max line)")
pairs = []
g = collections.defaultdict(list)
for r in L: g[(r['NK'], r['DD'].strftime('%Y-%m'))].append(r)
for (nk, m), rows in g.items():
    sups = {x['SUP'] for x in rows}
    if len(sups) < 2: continue
    lo = min(rows, key=lambda x: x['RATE']); hi = max(rows, key=lambda x: x['RATE'])
    if hi['SUP'] != lo['SUP'] and hi['RATE'] >= 1.5*lo['RATE']:
        pairs.append(((hi['RATE']-lo['RATE'])*hi['QTY'], m, nk[0], hi, lo))
pairs.sort(key=lambda x: -x[0])
print(len(pairs), "cases, total excess", round(sum(p[0] for p in pairs)))
hs = collections.Counter(p[3]['SUP'] for p in pairs); print("high-side supplier counts", hs.most_common(10))
for ex, m, n, hi, lo in pairs[:25]:
    print(f" {m} {n} | HIGH {hi['GRN']} {hi['SUP'][:22]} {hi['QTY']}@{hi['RATE']} | LOW {lo['GRN']} {lo['SUP'][:22]} {lo['QTY']}@{lo['RATE']} | Rs {round(ex)}")
# C. owner-linked vs trade suppliers paired: same item within 60 days
print("== paired: owner-linked account rate vs trade-supplier rate, same item within 60d")
OW = {'AR(STORE)','MIAN AZAM(STORE)','AR STORE','PURCHASE ON CASH','PURCHASES ON CASH'}
for acct in OW:
    ex = 0.0; base = 0.0; n = 0
    for r in L:
        if r['SUP'] != acct: continue
        comp = [x['RATE'] for x in by[r['NK']] if x['SUP'] not in OW and abs((x['DD']-r['DD']).days) <= 60]
        if not comp: continue
        ref = st.median(comp); ex += (r['RATE']-ref)*r['QTY']; base += ref*r['QTY']; n += 1
    print(acct, "lines", n, "premium Rs", round(ex), "on", round(base), f"{round(100*ex/base,1) if base else 0}%")
