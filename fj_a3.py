import pickle, re, collections, datetime, sys
sys.path.insert(0, r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad")
d = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad"
exec(open(d+r"\fj_a2.py").read().split("out=open")[0])  # reuse cat() and V
out=open(d+r"\fj_a3_out.txt","w",encoding="utf-8")
def P(*a): print(*a, file=out)
def fmt(v): return f"{v:,.0f}"
def fy(dt): return dt.year+1 if dt.month>=7 else dt.year
# pair lines into flows
flows=[]; unpaired=0; tot=0
for k,ls in V.items():
    ls=sorted(ls,key=lambda l:(l['sr_no'] or 0))
    i=0
    while i<len(ls):
        a=ls[i]; tot+=1
        if i+1<len(ls):
            b=ls[i+1]; aa=a['dr'] or a['cr']; bb=b['dr'] or b['cr']
            if abs(aa-bb)<0.01 and bool(a['dr'])!=bool(b['dr']):
                dr,cr=(a,b) if a['dr'] else (b,a)
                flows.append(dict(k=k,e=k[0],vt=k[1],vd=k[3],amt=aa,dac=dr['ac'],dan=dr['an'],dcat=dr['cat'],cac=cr['ac'],can=cr['an'],ccat=cr['cat'],nar=(dr['naration'] or cr['naration'] or '')))
                i+=2; continue
        unpaired+=1; i+=1
P('lines',tot,'unpaired',unpaired,'flows',len(flows))
JVF=[f for f in flows if f['vt']=='JV']
jvlines=sum(len(v) for k,v in V.items() if k[1]=='JV')
P('JV flows',len(JVF),'covering lines',2*len(JVF),'of',jvlines)
def agg(filt, key, title, n=25):
    a=collections.defaultdict(lambda:[0,0.0])
    for f in JVF:
        if filt(f): x=a[key(f)]; x[0]+=1; x[1]+=f['amt']
    P(f"\n## {title}: n={sum(v[0] for v in a.values())} amt={fmt(sum(v[1] for v in a.values()))}")
    for kk,v in sorted(a.items(), key=lambda x:-x[1][1])[:n]: P('  ',kk,v[0],fmt(v[1]))
ART=('501400001','300306004','300306009')
agg(lambda f:f['dac'] in ART and f['ccat']=='DEBT' and f['cac'] not in ART, lambda f:(f['e'],fy(f['vd'])), 'AR TEXTILES debited, other customer credited (by FY)', 20)
agg(lambda f:f['dac'] in ART and f['ccat']=='DEBT' and f['cac'] not in ART, lambda f:(f['e'],f['can']), 'AR TEXTILES debited, other customer credited (by customer)', 25)
agg(lambda f:f['dac'] in ART and f['ccat']=='DEBT' and re.search(r'C\s*/\s*O|TO AR',f['nar'] or '',re.I), lambda f:(f['e'],f['can'],fy(f['vd'])), 'C/O receipts: customer credited, AR TEXTILES debited (no cash)', 30)
agg(lambda f:f['cac'] in ART, lambda f:(f['e'],f['dcat']), 'AR TEXTILES credited (AR funds company) by Dr category', 20)
agg(lambda f:f['cac'] in ART and f['dcat'] in ('DRAW','CAP','MAZ'), lambda f:(f['e'],fy(f['vd']),f['dan']), 'AR TEXTILES credited -> drawings/capital/MAZ', 30)
agg(lambda f:'MAZ' in (f['dcat'],f['ccat']), lambda f:(f['e'],'MAZ Dr' if f['dcat']=='MAZ' else 'MAZ Cr', f['can'] if f['dcat']=='MAZ' else f['dan']), 'MIAN AZAM(STORE) JV flows by counterparty', 40)
agg(lambda f:'MAZ' in (f['dcat'],f['ccat']), lambda f:(f['e'],fy(f['vd']),'MAZ Dr' if f['dcat']=='MAZ' else 'MAZ Cr'), 'MIAN AZAM(STORE) JV flows by FY', 40)
agg(lambda f:f['ccat']=='REV', lambda f:(f['e'],f['can'],f['dcat']), 'Revenue credited by JV (sales booked without invoice)', 25)
agg(lambda f:f['dcat']=='DRAW', lambda f:(f['e'],f['ccat']), 'Drawings debited by JV, funded from', 20)
agg(lambda f:f['ccat']=='DEBT' and f['dcat'] in ('DRAW','CAP','MAZ','CRED','LTLOAN','LOANOTH','EMPADV','FA','EXP'), lambda f:(f['e'],f['can'],f['dcat']), 'Customer credited against non-cash Dr (receivable cleared w/o cash)', 30)
agg(lambda f:f['dcat']=='CRED' and f['ccat'] in ('DEBT','DF','MAZ','DRAW','CAP'), lambda f:(f['e'],f['dan'],f['ccat'],f['can']), 'Supplier debited against customer/owner Cr (payable cleared w/o cash)', 25)
agg(lambda f:f['dcat']=='DF' or f['ccat']=='DF', lambda f:(f['e'],f['k'][2],f['vd'].strftime('%Y-%m-%d'),f['dan'],f['can'],f['nar'][:60]), 'DF flows', 20)
# DF all voucher lines
P('\n## DF all TM/TD lines')
for k,ls in V.items():
    for l in ls:
        if l['cat']=='DF': P('  ',k[0],k[1],k[2],k[3].strftime('%Y-%m-%d'),l['dr'],l['cr'],l['naration'])
# large single flows Dr drawings or cap by JV
P('\n## Largest single JV flows (top 30)')
for f in sorted(JVF,key=lambda f:-f['amt'])[:30]: P('  ',f['e'],f['k'][1],f['k'][2],f['vd'].strftime('%Y-%m-%d'),fmt(f['amt']),'Dr',f['dac'],f['dan'],'| Cr',f['cac'],f['can'],'|',f['nar'][:70])
out.close()
