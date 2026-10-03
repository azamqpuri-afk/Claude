import pickle, collections, statistics as st
L, _ = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","rb"))
# entry lag: FEEDD - DD for GRNs, by supplier; big ones
g = {}
for r in L:
    x = g.setdefault(r['GRN'], [0.0, r]); x[0] += r['AMT']
lag = [(v[1]['FEEDD'] - v[1]['DD']).days for v in g.values() if v[1]['FEEDD']]
print("GRNs", len(g), "lag>30d", sum(1 for l in lag if l > 30), "lag<0", sum(1 for l in lag if l < 0))
big = sorted([(v[0], k, v[1]) for k, v in g.items() if v[1]['FEEDD'] and (v[1]['FEEDD']-v[1]['DD']).days > 30], key=lambda x: -x[0])[:15]
for a, k, r in big: print(" ", k, r['DD'].date(), "entered", r['FEEDD'].date(), r['SUP'], round(a), r['USERN'])
# bill date after GRN date / far before
bd = sorted([(v[0], k, v[1]) for k, v in g.items() if v[1]['BD'] and ((v[1]['BD']-v[1]['DD']).days > 3 or (v[1]['DD']-v[1]['BD']).days > 60)], key=lambda x: -x[0])[:12]
print("bill date >3d after GRN or >60d before:", len(bd))
for a, k, r in bd: print(" ", k, "GRN", r['DD'].date(), "bill", r['BNO'], r['BD'].date(), r['SUP'], round(a))
# Dacca: price creep on its top items, first vs last rate, and vs other suppliers
print("== DACCA top items rate history")
dc = collections.defaultdict(list)
for r in L:
    if r['SUP'] == 'DACCA TRADE AGENCY': dc[r['NK']].append(r)
tops = sorted(dc.items(), key=lambda x: -sum(r['AMT'] for r in x[1]))[:15]
for nk, rows in tops:
    rows.sort(key=lambda r: r['DD'])
    oth = [(r['DD'].strftime('%y-%m'), r['SUP'][:12], r['RATE']) for r in L if r['NK'] == nk and r['SUP'] != 'DACCA TRADE AGENCY' and r['DD'].year >= 2022]
    print(f" {nk[0]} | Rs {round(sum(r['AMT'] for r in rows))} qty {round(sum(r['QTY'] for r in rows))} | first {rows[0]['DD'].date()} @{rows[0]['RATE']} last {rows[-1]['DD'].date()} @{rows[-1]['RATE']} | others: {oth[-5:]}")
# Dacca share of weaving-store head spend by FY
print("== Dacca share of loom-spares heads (OLD 71, NEW 15) by FY")
t = collections.defaultdict(lambda: [0.0, 0.0])
for r in L:
    if (r['ERP'], r['HEAD']) in (('OLD', '71'), ('NEW', '15')):
        t[r['FY']][0] += r['AMT']; t[r['FY']][1] += r['AMT'] if r['SUP'] == 'DACCA TRADE AGENCY' else 0
for f in sorted(t): print(" ", f, round(t[f][0]), round(t[f][1]), f"{round(100*t[f][1]/t[f][0])}%")
