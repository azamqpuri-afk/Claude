import pickle, collections, datetime as dt, re, difflib
D = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\\"
d = pickle.load(open(D+"fr_data.pkl","rb"))
led, vouch, ach = d['led'], d['vouch'], d['ach']
nm = {('OLD',a['ac']):a['an'] for a in d['allo']}; nm.update({('NEW',a['ac']):a['an'] for a in d['alln']})
V = collections.defaultdict(list)
for r in vouch: V[(r['src'],r['vt'],r['vno'],r['vd'])].append(r)
TODAY=dt.datetime(2026,9,26)
def cust(src,ac):
    ac=ac or ''
    return ac.startswith('501') if src=='OLD' else ac.startswith('3003')
def supp(src,ac):
    ac=ac or ''
    return ac.startswith('30') if src=='OLD' else ac.startswith('2001')
AR={'501400001','300306004','300306009'}
# 1 robust customer->AR transfers: per voucher min(AR debit, other-customer credit)
tot=collections.Counter(); n=collections.Counter()
for k,lines in V.items():
    ard=sum(l['amt'] for l in lines if l['ac'] in AR and (l['amt'] or 0)>0)
    cc=-sum(l['amt'] for l in lines if cust(l['src'],l['ac']) and l['ac'] not in AR and (l['amt'] or 0)<0)
    if ard>0 and cc>0 and k[1]=='JV':
        tot[k[0]]+=min(ard,cc); n[k[0]]+=1
print("robust customer->AR TEXTILE JV transfers:",{k:round(v) for k,v in tot.items()},dict(n))
# AR TEXTILE receipts from PURI FABRICS cheques
pf=[r for r in led if r['ac'] in AR and 'PURI' in (r['naration'] or '').upper() and r['amt']<0]
print("AR TEXTILE credits narrated PURI:",len(pf),round(sum(r['amt'] for r in pf)),min(r['vd'] for r in pf).date(),max(r['vd'] for r in pf).date())
# 2 FIFO ageing for open balances (new ERP incl OPN treated as dated 30-Sep-2023)
def fifo(rows, sign):
    q=[]  # open items (date, amt) on the side of sign
    for r in sorted(rows,key=lambda r:(r['vd'] if r['vt']!='OPN' else dt.datetime(2023,9,30), 0 if r['amt']*sign>0 else 1)):
        a=r['amt']*sign; vd=r['vd'] if r['vt']!='OPN' else dt.datetime(2023,9,30)
        if a>0: q.append([vd,a,r['vt'],r['vno']])
        else:
            a=-a
            while a>0 and q:
                if q[0][1]<=a: a-=q[0][1]; q.pop(0)
                else: q[0][1]-=a; a=0
            if a>0: q=[] ; # overpaid -> negative, ignore
    return q
print("\n=== FIFO ageing of open DEBIT balances (customers & suppliers, new ERP) as of 26-Sep-2026")
byac=collections.defaultdict(list)
for r in led:
    if r['src']=='NEW': byac[r['ac']].append(r)
for ac,rows in byac.items():
    b=sum(r['amt'] for r in rows)
    if b>1000:
        q=fifo(rows,1)
        bk=collections.Counter()
        for vd,a,vt,vno in q:
            age=(TODAY-vd).days; bk['0-90' if age<=90 else '91-180' if age<=180 else '181-365' if age<=365 else '>365']+=a
        old=q[0] if q else None
        print(ac,nm.get(('NEW',ac)),'bal',round(b),{k:round(v) for k,v in bk.items()},'oldest open:',old and (old[0].date(),old[2],old[3],round(old[1])))
print("\n=== CREDIT balances on customers / DEBIT balances on suppliers: days spent in that state (whole history, per account)")
for src in ('OLD','NEW'):
    acs=collections.defaultdict(list)
    for r in led:
        if r['src']==src: acs[r['ac']].append(r)
    for ac,rows in acs.items():
        c=cust(src,ac); s=supp(src,ac)
        rows.sort(key=lambda r:r['vd']); bal=0; days=0; prev=None; wrong_amt_days=0
        per=collections.OrderedDict()
        for r in rows:
            if prev is not None:
                dd=(r['vd']-prev).days
                if (c and bal< -1000) or (s and bal>1000): days+=dd; wrong_amt_days+=abs(bal)*dd
            bal+=r['amt']; prev=r['vd']
        end=dt.datetime(2023,9,30) if src=='OLD' else TODAY
        dd=(end-prev).days
        if (c and bal< -1000) or (s and bal>1000): days+=dd; wrong_amt_days+=abs(bal)*dd
        if days>=90: print(src,ac,nm.get((src,ac)),'days',days,'avg wrong-side bal',round(wrong_amt_days/days),'end bal',round(bal))
# 3 duplicates / near-identical names across party masters
print("\n=== near-duplicate party names")
names=[(a['src'],a['ac'],a['an']) for a in ach]
def norm(s): return re.sub(r'\(.*?\)|[^A-Z]','',(s or '').upper().replace('ENGG','ENG').replace('TRADERS','TRD').replace('TRADING','TRD'))
seen=set()
for i,(s1,a1,n1) in enumerate(names):
    for s2,a2,n2 in names[i+1:]:
        if s1==s2 and a1[:4]==a2[:4] or True:
            if s1!=s2 and norm(n1)==norm(n2): continue  # same party across ERPs is expected
            r=difflib.SequenceMatcher(None,norm(n1),norm(n2)).ratio()
            if r>=0.86 and (a1,a2) not in seen:
                seen.add((a1,a2)); print(f"{s1} {a1} {n1}  ~  {s2} {a2} {n2}  ({r:.2f})")
# 4 dormant accounts suddenly used: gap >= 540 days between consecutive entries (combine old+new by name)
print("\n=== dormant >=18 months then reactivated (by normalized name, both ERPs)")
g=collections.defaultdict(list)
for r in led:
    if r['vt']=='OPN': continue
    g[norm(nm.get((r['src'],r['ac'])))].append(r)
for k,rows in g.items():
    ds=sorted(set(r['vd'] for r in rows))
    for a,b in zip(ds,ds[1:]):
        if (b-a).days>=540:
            after=[r for r in rows if r['vd']==b]
            print(k, a.date(),'->',b.date(), (b-a).days,'days; first new entry:',[(r['src'],r['vt'],r['vno'],round(r['amt']),(r['naration'] or '')[:40]) for r in after][:2])
# 5 customers credits by type share
print("\n=== customers: share of credits settled by BR/CR vs JV (both ERPs)")
cs=collections.defaultdict(collections.Counter)
for r in led:
    if cust(r['src'],r['ac']) and r['amt']<0 and r['vt']!='OPN':
        cs[(r['src'],r['ac'])]['cash/bank' if r['vt'] in('BR','CR') else ('return' if r['vt'] in('GPIN','IGP_GREY','IGP_SIZED_BEAMS') else r['vt'])]+=-r['amt']
for k,c in sorted(cs.items(),key=lambda x:-sum(x[1].values())):
    t=sum(c.values())
    if t>1e6: print(k[0],k[1],nm.get(k),round(t),'cash/bank %.0f%%'%(100*c['cash/bank']/t),'JV %.0f%%'%(100*c['JV']/t),{kk:round(v) for kk,v in c.items() if kk not in('cash/bank','JV')})
# 6 MIAN AZAM(STORE) & AR STORE: sales invoices (GPOUT/SAL/OGP) to supplier accounts
print("\n=== sales-type entries on supplier accounts (SAL, GPOUT, OGP)")
sc=collections.Counter()
for r in led:
    if supp(r['src'],r['ac']) and r['vt'] in('SAL','GPOUT','GPIN','OGP_YARN'):
        sc[(r['src'],r['ac'],nm.get((r['src'],r['ac'])),r['vt'])]+=r['amt']
for k,v in sorted(sc.items(),key=lambda x:-abs(x[1])): print(k,round(v))
