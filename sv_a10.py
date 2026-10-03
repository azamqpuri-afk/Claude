import pickle, collections, statistics as st
L, _ = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","rb"))
by = collections.defaultdict(list)
for r in L: by[r['NK']].append(r)
for S in ('DACCA TRADE AGENCY', 'SALAHEEN OIL TRADERS', 'WAQAS TRADERS', 'IMRAN TRADERS', 'NAEEM ENTERPRISES'):
    ex = base = 0.0; n = 0; items = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for r in L:
        if r['SUP'] != S: continue
        comp = [x['RATE'] for x in by[r['NK']] if x['SUP'] != S and abs((x['DD']-r['DD']).days) <= 90]
        if not comp: continue
        ref = st.median(comp); e = (r['RATE']-ref)*r['QTY']; ex += e; base += ref*r['QTY']; n += 1
        it = items[r['NK'][0]]; it[0] += e; it[1] += ref*r['QTY']; it[2] += 1
    tot = sum(r['AMT'] for r in L if r['SUP'] == S)
    print(f"== {S}: comparable lines {n}, premium Rs {round(ex)} on Rs {round(base)} ({round(100*ex/base,1) if base else 0}%); total spend {round(tot)}")
    for k, v in sorted(items.items(), key=lambda x: -x[1][0])[:6]: print("   +", k, round(v[0]), f"{round(100*v[0]/v[1])}%", v[2])
    for k, v in sorted(items.items(), key=lambda x: x[1][0])[:3]: print("   -", k, round(v[0]), f"{round(100*v[0]/v[1])}%", v[2])
