import pickle, collections, datetime as dt, re
D = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\\"
d = pickle.load(open(D+"fr_data.pkl","rb"))
led, ach = d['led'], d['ach']
name = {(a['src'],a['ac']):a for a in ach}
by = collections.defaultdict(list)
for r in led: by[(r['src'], r['ac'])].append(r)
print("old ERP first dates by vt:", min(r['vd'] for r in led if r['src']=='OLD'))
print("OPN lines:")
for r in led:
    if r['vt']=='OPN': print(" ", r['ac'], name.get(('NEW',r['ac']),{}).get('an'), r['amt'])
rows=[]
for k, L in by.items():
    L.sort(key=lambda r:(r['vd'], r['vno'] or 0))
    bal=0; mx=0; mn=0; mxd=mnd=None
    for r in L:
        bal += r['amt'] or 0
        if bal>mx: mx, mxd = bal, r['vd']
        if bal<mn: mn, mnd = bal, r['vd']
    dr=sum(r['amt'] for r in L if (r['amt'] or 0)>0); cr=-sum(r['amt'] for r in L if (r['amt'] or 0)<0)
    vt=collections.Counter(r['vt'] for r in L)
    h=name.get(k,{})
    rows.append((k[0],k[1],h.get('an'),L[0]['vd'].date(),L[-1]['vd'].date(),len(L),round(dr),round(cr),round(bal),round(mx),mxd and mxd.date(),round(mn),mnd and mnd.date(),dict(vt)))
for grp,pref in [('OLD customers','501'),('NEW customers','3003'),('OLD suppliers','30'),('NEW suppliers','2001')]:
    print("\n=====",grp)
    print("src|ac|name|first|last|n|dr|cr|bal|maxDrBal|at|maxCrBal|at|vt")
    sel=[r for r in rows if r[1].startswith(pref) and r[0]==grp[:3]]
    for r in sorted(sel,key=lambda r:-(abs(r[6])+abs(r[7]))): print("|".join(map(str,r)))
# party master without activity
print("\n===== accounts with no ledger lines")
for a in ach:
    if (a['src'],a['ac']) not in by: print(a['src'],a['ac'],a['an'])
