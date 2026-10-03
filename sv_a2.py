import pickle, collections, statistics as st, datetime as dt
D = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_data.pkl","rb"))
L = D['old'] + D['new']
def fy(d): return 'FY%02d' % ((d.year + (1 if d.month >= 7 else 0)) % 100)
for r in L:
    r['AMT'] = (r['QTY'] or 0)*(r['RATE'] or 0); r['FY'] = fy(r['DD']); r['HEAD'] = r['IC'][:2]
    r['GRN'] = f"{r['ERP']}-{r['DNO']}"
    r['KEY'] = (r['ERP'], r['IC'])
    r['SUP'] = (r['AN'] or r['AC']).strip()
# 1. spend by FY
print("== spend by FY"); fyt = collections.defaultdict(float); fyg = collections.defaultdict(set)
for r in L: fyt[r['FY']] += r['AMT']; fyg[r['FY']].add(r['GRN'])
for k in sorted(fyt): print(k, round(fyt[k]), len(fyg[k]))
# 2. supplier by FY (supplier name as key across ERPs)
sf = collections.defaultdict(lambda: collections.defaultdict(float))
for r in L: sf[r['SUP']][r['FY']] += r['AMT']
fys = sorted(fyt)
print("== top 25 suppliers by FY (Rs 000)")
print("SUP | " + " | ".join(fys) + " | TOTAL")
for s, d in sorted(sf.items(), key=lambda x: -sum(x[1].values()))[:25]:
    print(s, "|", " | ".join(str(round(d.get(f,0)/1000)) for f in fys), "|", round(sum(d.values())/1000))
print("== top 3 per FY with share")
for f in fys:
    tops = sorted(((d.get(f,0), s) for s, d in sf.items()), reverse=True)[:4]
    print(f, [(s, round(v/1000), round(100*v/fyt[f],1)) for v, s in tops])
# 3. sudden appearance: first FY spend >= 300k
print("== suppliers whose first FY spend >= Rs 3 lac")
for s, d in sf.items():
    first = min(f for f in d if d[f] != 0) if any(d.values()) else None
    if first and d[first] >= 300000:
        print(s, first, round(d[first]), "total", round(sum(d.values())), "fys", len([f for f in d if d[f]]))
# 4. generalist vs specialist
print("== supplier breadth: distinct heads/items, spend")
br = collections.defaultdict(lambda: [set(), set(), 0.0, set()])
for r in L:
    b = br[r['SUP']]; b[0].add(r['ERP']+r['HEAD']); b[1].add(r['KEY']); b[2] += r['AMT']; b[3].add(r['GRN'])
for s, b in sorted(br.items(), key=lambda x: -x[1][2])[:30]:
    print(s, "heads", len(b[0]), "items", len(b[1]), "grns", len(b[3]), "spend", round(b[2]))
# 5. owner/staff/cash accounts by FY
print("== owner / cash / AR accounts by FY")
for s in sf:
    if any(k in s.upper() for k in ('CASH','AZAM','AR(','AR ','PURI','SOLAR','SHEHZAD','PURCHASER','MB TEXTILE','IQ','CREDIT CARD')):
        d = sf[s]; print(s, {f: round(v) for f, v in sorted(d.items()) if v}, "total", round(sum(d.values())))
# 6. per-FY share of owner/cash-linked
print("== share of cash+owner-linked per FY")
grp = lambda s: any(k in s.upper() for k in ('CASH','AZAM','AR(STORE)','AR STORE','AR TEXTILE','PURI'))
for f in fys:
    v = sum(r['AMT'] for r in L if r['FY']==f and grp(r['SUP']))
    print(f, round(v), round(100*v/fyt[f],1))
# 7. GRN-level round totals and user
print("== GRN totals")
g = collections.defaultdict(lambda: [0.0, 0, None])
for r in L: x = g[r['GRN']]; x[0] += r['AMT']; x[1] += 1; x[2] = r
rt = [(k, v) for k, v in g.items() if v[0] >= 50000 and v[0] % 10000 == 0]
print("GRNs >=50k:", sum(1 for v in g.values() if v[0]>=50000), "of which round 10,000:", len(rt))
rs = collections.Counter(v[2]['SUP'] for k, v in rt)
print(rs.most_common(15))
for k, v in sorted(rt, key=lambda x: -x[1][0])[:30]:
    r = v[2]; print(k, r['DD'].date(), r['SUP'], round(v[0]), v[1], "lines;", r['IDESC'], r['QTY'], r['RATE'])
pickle.dump(L, open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L.pkl","wb"))
