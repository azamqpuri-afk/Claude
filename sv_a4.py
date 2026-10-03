import pickle, collections, statistics as st, re
L = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L.pkl","rb"))
L = [r for r in L if r['RATE'] and r['QTY'] and r['RATE']>0 and r['QTY']>0]
norm = lambda s: re.sub(r'\s+', ' ', (s or '').upper().strip())
by = collections.defaultdict(list)
for r in L:
    r['NK'] = (norm(r['IDESC']), (r['UOM'] or '').upper()); by[r['NK']].append(r)
flags = []; supnet = collections.defaultdict(lambda: [0.0, 0.0, 0])
for k, rows in by.items():
    if len(rows) < 3: continue
    for r in rows:
        comp = [x['RATE'] for x in rows if x['GRN'] != r['GRN'] and abs((x['DD']-r['DD']).days) <= 365]
        if len(comp) < 2: continue
        ref = st.median(comp)
        r['REF'] = ref
        s = supnet[r['SUP']]; s[0] += (r['RATE']-ref)*r['QTY']; s[1] += ref*r['QTY']; s[2] += 1
        if r['RATE'] > 1.25*ref:
            flags.append(((r['RATE']-ref)*r['QTY'], r, ref, len(comp)))
flags.sort(key=lambda x: -x[0])
print("== outliers >25% above +-12m median of item's other GRNs: count", len(flags), "total excess", round(sum(f[0] for f in flags)))
print("excess >=5000:", sum(1 for f in flags if f[0]>=5000), round(sum(f[0] for f in flags if f[0]>=5000)))
for ex, r, ref, n in flags[:60]:
    others = sorted([(x['DD'].strftime('%y-%m'), x['SUP'][:14], x['RATE']) for x in by[r['NK']] if x['GRN']!=r['GRN'] and abs((x['DD']-r['DD']).days)<=365], key=lambda z: z[0])
    print(f"{r['GRN']} {r['DD'].date()} {r['SUP'][:26]} | {r['IDESC']} | {r['QTY']} {r['UOM']} @ {r['RATE']} vs med {round(ref,2)} (+{round(100*(r['RATE']/ref-1))}%) excess {round(ex)} | cmp:{others[:6]}")
print("== outlier excess by supplier")
fs = collections.defaultdict(lambda: [0.0, 0])
for ex, r, ref, n in flags: fs[r['SUP']][0] += ex; fs[r['SUP']][1] += 1
for s, v in sorted(fs.items(), key=lambda x: -x[1][0])[:20]: print(s, round(v[0]), v[1])
print("== supplier net premium vs item median (all comparable lines), min ref value 100k")
for s, v in sorted(supnet.items(), key=lambda x: -x[1][0]/max(x[1][1],1)):
    if v[1] >= 100000: print(s, "net", round(v[0]), "on ref value", round(v[1]), f"{round(100*v[0]/v[1],1)}%", v[2], "lines")
pickle.dump((L, flags), open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","wb"))
