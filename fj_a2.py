import pickle, re, collections, datetime
d = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad"
rows, ach = pickle.load(open(d+r"\fj_lines.pkl","rb"))
def cat(e, ac):
    ac = ac or ''
    if e=='NEW':
        for p,c in [('200105006','MAZ'),('300306013','DF'),('100101','CAP'),('1003','DRAW'),('1004','DRAW'),('15','LTLOAN'),('2001','CRED'),('2002','ACCR'),('25','FA'),('3001','STOCK'),('3002','STOCK'),('3003','DEBT'),('300401','EMPADV'),('300402','LOANOTH'),('3005','ADV'),('3006','ADV'),('3008','CASH'),('35','REV'),('40','EXP'),('45','EXP'),('55','EXP')]:
            if ac.startswith(p): return c
    else:
        for p,c in [('301200102','MAZ'),('11','CAP'),('12','CAP'),('15120','CAP'),('15125','DRAW'),('15135','DRAW'),('15160','DRAW'),('15','LTLOAN'),('20','LTLOAN'),('25','LTLOAN'),('30','CRED'),('35','ACCR'),('40','FA'),('42','FA'),('45','FA'),('46','FA'),('50','DEBT'),('551','EMPADV'),('55','ADV'),('60','STOCK'),('65','CASH'),('70','REV'),('75','EXP'),('80','EXP'),('85','EXP'),('90','EXP'),('95','EXP')]:
            if ac.startswith(p): return c
    return 'OTHER'
V = collections.OrderedDict()
for r in rows:
    k=(r['e'],r['vt'],r['vno'],r['vd'])
    r['cat']=cat(r['e'],r['ac'])
    V.setdefault(k,[]).append(r)
out=open(d+r"\fj_a2_out.txt","w",encoding="utf-8")
def P(*a): print(*a, file=out)
def fmt(v):
    return f"{v:,.0f}"
def vsum(ls): return sum(l['dr'] for l in ls)
def desc(k, ls, n=6):
    h=ls[0]; lag=(h['feedd'].date()-h['vd'].date()).days if h['feedd'] else None
    parts=[]
    for l in sorted(ls,key=lambda x:-(x['dr']+x['cr']))[:n]:
        parts.append(f"{'Dr' if l['dr'] else 'Cr'} {l['ac']} {(l['an'] or '')[:28]} [{l['cat']}] {fmt(l['dr'] or l['cr'])}")
    nar = '; '.join(sorted(set((l['naration'] or '').strip()[:70] for l in ls if l['naration'])))[:200]
    return f"{k[0]} {k[1]}-{k[2]} {k[3]:%Y-%m-%d} tot={fmt(vsum(ls))} user={h['usern']} pc={h['pcn']} fed={h['feedd']:%Y-%m-%d %H:%M} lag={lag} lines={len(ls)}\n     " + " | ".join(parts) + f"\n     NAR: {nar}"
JV = {k:v for k,v in V.items() if k[1]=='JV'}
def fy(dt): return dt.year+1 if dt.month>=7 else dt.year
P("### A. JV volume by fiscal year (FY ends 30 Jun)")
agg=collections.defaultdict(lambda:[0,0,0,0])
for k,ls in JV.items():
    a=agg[(k[0],fy(k[3]))]; a[0]+=1; a[1]+=vsum(ls); a[2]+= (k[3].day==(k[3]+datetime.timedelta(days=1)).day-0 and 0); a[3]+=1 if (k[3]+datetime.timedelta(days=1)).day==1 else 0
for kk in sorted(agg): P(kk, 'n',agg[kk][0],'sum',fmt(agg[kk][1]),'monthend-dated',agg[kk][3])
P("\n### A2. Top 3 JVs per FY")
byfy=collections.defaultdict(list)
for k,ls in JV.items(): byfy[fy(k[3])].append((vsum(ls),k))
for y in sorted(byfy):
    for s,k in sorted(byfy[y],reverse=True)[:3]: P(f"FY{y}: "+desc(k,JV[k]))
P("\n### B. JV patterns: Dr categories -> Cr categories (count, amount)")
pat=collections.defaultdict(lambda:[0,0])
for k,ls in JV.items():
    dc='+'.join(sorted(set(l['cat'] for l in ls if l['dr']))); cc='+'.join(sorted(set(l['cat'] for l in ls if l['cr'])))
    p=pat[(k[0],dc,cc)]; p[0]+=1; p[1]+=vsum(ls)
for kk,v in sorted(pat.items(), key=lambda x:-x[1][1])[:60]: P(kk, v[0], fmt(v[1]))
PARTY={'DEBT','CRED','MAZ','DF','DRAW','CAP','LOANOTH','LTLOAN'}
P("\n### C. Party-to-party JVs: customer (DEBT/DF) credited while partner/drawings/supplier/MAZ debited, and reverse")
def amt_side(ls, cats, side): return sum(l[side] for l in ls if l['cat'] in cats)
c1=[];c2=[];c3=[]
for k,ls in JV.items():
    if amt_side(ls,{'DEBT','DF'},'cr')>0 and amt_side(ls,{'DRAW','CAP','CRED','MAZ','LTLOAN','LOANOTH'},'dr')>0: c1.append((min(amt_side(ls,{'DEBT','DF'},'cr'),amt_side(ls,{'DRAW','CAP','CRED','MAZ','LTLOAN','LOANOTH'},'dr')),k))
    if amt_side(ls,{'DEBT','DF'},'dr')>0 and amt_side(ls,{'DRAW','CAP','CRED','MAZ','LTLOAN','LOANOTH'},'cr')>0: c2.append((min(amt_side(ls,{'DEBT','DF'},'dr'),amt_side(ls,{'DRAW','CAP','CRED','MAZ','LTLOAN','LOANOTH'},'cr')),k))
    if amt_side(ls,{'DEBT','DF'},'dr')>0 and amt_side(ls,{'DEBT','DF'},'cr')>0: c3.append((min(amt_side(ls,{'DEBT','DF'},'dr'),amt_side(ls,{'DEBT','DF'},'cr')),k))
for name,c in [('C1 customer Cr vs owner/supplier Dr',c1),('C2 customer Dr vs owner/supplier Cr',c2),('C3 customer-to-customer',c3)]:
    for e in ('OLD','NEW'):
        cc=[x for x in c if x[1][0]==e]; P(f"{name} {e}: n={len(cc)} matched-amt={fmt(sum(x[0] for x in cc))}")
    for s,k in sorted(c,reverse=True)[:12]: P("  "+desc(k,JV[k]))
P("\n### D. JVs touching MIAN AZAM(STORE) / DF")
for tgt in ('MAZ','DF'):
    L=[(sum(l['dr']+l['cr'] for l in ls if l['cat']==tgt),k) for k,ls in JV.items() if any(l['cat']==tgt for l in ls)]
    for e in ('OLD','NEW'):
        cc=[x for x in L if x[1][0]==e]
        dr=sum(l['dr'] for x in cc for l in JV[x[1]] if l['cat']==tgt); cr=sum(l['cr'] for x in cc for l in JV[x[1]] if l['cat']==tgt)
        P(f"{tgt} {e}: JVs={len(cc)} Dr={fmt(dr)} Cr={fmt(cr)}")
    # counterparties
    cp=collections.defaultdict(lambda:[0,0])
    for s,k in L:
        for l in JV[k]:
            if l['cat']!=tgt: cp[(k[0],l['ac'],l['an'],'Dr' if l['dr'] else 'Cr')][0]+=1; cp[(k[0],l['ac'],l['an'],'Dr' if l['dr'] else 'Cr')][1]+=l['dr']+l['cr']
    for kk,v in sorted(cp.items(),key=lambda x:-x[1][1])[:15]: P('   cp',kk,v[0],fmt(v[1]))
    for s,k in sorted(L,reverse=True)[:12]: P("  "+desc(k,JV[k]))
    # also all voucher types touching it
    tv=collections.defaultdict(lambda:[0,0,0])
    for k,ls in V.items():
        for l in ls:
            if l['cat']==tgt: t=tv[(k[0],k[1])]; t[0]+=1; t[1]+=l['dr']; t[2]+=l['cr']
    for kk,v in sorted(tv.items()): P('   by vt (TM/TD only)',kk,v[0],'Dr',fmt(v[1]),'Cr',fmt(v[2]))
P("\n### E. C/O / on-behalf narration")
rx=re.compile(r"\bC\s*[/\.]\s*O\b|ON BEHALF|\bB\s*/\s*O\b|CARRIED|CARRY|\bTRANSFER|\bT/F\b|\bTRF\b|ADJUST|\bADJ\b|CONTRA|SET\s*OFF|SETOFF|INTER", re.I)
hits=collections.defaultdict(list)
for k,ls in V.items():
    for l in ls:
        m=rx.search(l['naration'] or '') or rx.search(l['remarks'] or '')
        if m: hits[m.group(0).upper().replace(' ','')].append(k)
for w,ks in sorted(hits.items(), key=lambda x:-len(x[1])):
    ks=list(dict.fromkeys(ks)); P(w, 'vouchers', len(ks), 'by vt', collections.Counter(k[1] for k in ks).most_common(), 'amt', fmt(sum(vsum(V[k]) for k in ks)))
co=list(dict.fromkeys(hits.get('C/O',[])+hits.get('C.O',[])))
for k in sorted(co,key=lambda k:-vsum(V[k]))[:15]: P("  "+desc(k,V[k]))
P("\n### F. Revenue debited / expense credited inside JVs")
for side,cats in (('dr',{'REV'}),('cr',{'EXP'})):
    L=[(sum(l[side] for l in ls if l['cat'] in cats),k) for k,ls in JV.items()]
    L=[x for x in L if x[0]>0]
    for e in ('OLD','NEW'):
        cc=[x for x in L if x[1][0]==e]; P(f"{side} {cats} {e}: JVs={len(cc)} amt={fmt(sum(x[0] for x in cc))}")
    for s,k in sorted(L,reverse=True)[:12]: P(f"  [{fmt(s)}] "+desc(k,JV[k]))
P("\n### G. Round-number JVs (total >=100k and multiple of 100k / 50k)")
for e in ('OLD','NEW'):
    cc=[k for k in JV if k[0]==e]; big=[k for k in cc if vsum(JV[k])>=100000]
    r1=[k for k in big if vsum(JV[k])%100000==0]; r2=[k for k in big if vsum(JV[k])%10000==0]
    P(e,'JVs',len(cc),'>=100k',len(big),'mult 100k',len(r1),fmt(sum(vsum(JV[k]) for k in r1)),'mult 10k',len(r2))
    for k in sorted(r1,key=lambda k:-vsum(JV[k]))[:10]: P("  "+desc(k,JV[k]))
P("\n### H. Blank/vague narration JVs")
vague=re.compile(r"^\s*(JV|ADJ\.?|ADJUSTMENT|TRANSFER|AMOUNT|AMT|BALANCE|OPENING|N/?A|-|\.)?\s*$", re.I)
for e in ('OLD','NEW'):
    cc=[k for k in JV if k[0]==e]; bl=[k for k in cc if all(vague.match(l['naration'] or '') for l in JV[k]) and vague.match(JV[k][0]['remarks'] or '')]
    P(e,'blank/vague',len(bl),'of',len(cc),'amt',fmt(sum(vsum(JV[k]) for k in bl)))
    for k in sorted(bl,key=lambda k:-vsum(JV[k]))[:8]: P("  "+desc(k,JV[k]))
P("\n### I. Year-end (30 Jun) JVs and late-entered JVs with amounts")
ye=[k for k in JV if k[3].month==6 and k[3].day==30]
P('30-Jun JVs',len(ye),'amt',fmt(sum(vsum(JV[k]) for k in ye)))
late=[k for k in JV if JV[k][0]['feedd'] and (JV[k][0]['feedd'].date()-k[3].date()).days>90]
P('JVs entered >90 days late',len(late),'amt',fmt(sum(vsum(JV[k]) for k in late)))
for k in sorted(late,key=lambda k:-vsum(JV[k]))[:15]: P("  "+desc(k,JV[k]))
P("\n### J. Users")
u=collections.defaultdict(lambda:collections.Counter())
ua=collections.defaultdict(float)
for k,ls in V.items():
    u[(k[0],ls[0]['usern'],ls[0]['pcn'])][k[1]]+=1; ua[(k[0],ls[0]['usern'],k[1])]+=vsum(ls)
for kk,c in sorted(u.items(), key=lambda x:str(x[0])): P(kk, dict(c))
for kk,v in sorted(ua.items(), key=lambda x:str(x[0])): P('amt',kk,fmt(v))
oh=[k for k,ls in V.items() if ls[0]['feedd'] and (ls[0]['feedd'].hour<8 or ls[0]['feedd'].hour>=20 or ls[0]['feedd'].weekday()==6)]
P('entered before 08:00 / after 20:00 / on Sunday:',len(oh), collections.Counter((k[0],k[1]) for k in oh))
for k in sorted(oh,key=lambda k:-vsum(V[k]))[:15]: P("  "+desc(k,V[k],4))
P("\n### K. Cash-type vouchers with no cash/bank line (non-cash clearing through CP/CR/BP/BR)")
nc=[k for k,ls in V.items() if k[1]!='JV' and not any(l['cat']=='CASH' for l in ls)]
P(len(nc), collections.Counter((k[0],k[1]) for k in nc), fmt(sum(vsum(V[k]) for k in nc)))
for k in sorted(nc,key=lambda k:-vsum(V[k]))[:12]: P("  "+desc(k,V[k]))
P("\n### L. JVs Dr and Cr the same account; duplicate JVs (same accounts+amount, different voucher)")
same=[k for k,ls in JV.items() if set(l['ac'] for l in ls if l['dr']) & set(l['ac'] for l in ls if l['cr'])]
P('same-account both sides',len(same),fmt(sum(vsum(JV[k]) for k in same)))
for k in sorted(same,key=lambda k:-vsum(JV[k]))[:6]: P("  "+desc(k,JV[k]))
sig=collections.defaultdict(list)
for k,ls in JV.items():
    s=tuple(sorted((l['ac'] or '',round(l['dr'],2),round(l['cr'],2)) for l in ls)); sig[(k[0],s)].append(k)
dups=[v for v in sig.values() if len(v)>1]
P('duplicate signature groups',len(dups),'extra vouchers',sum(len(v)-1 for v in dups))
for v in sorted(dups,key=lambda v:-vsum(JV[v[0]]))[:12]: P('  DUP', [f"{k[1]}-{k[2]} {k[3]:%Y-%m-%d}" for k in v], fmt(vsum(JV[v[0]])), JV[v[0]][0]['an'], '/', JV[v[0]][-1]['an'])
P("\n### M. zero / negative lines, empty vouchers")
for k,ls in V.items():
    for l in ls:
        if l['dr']<0 or l['cr']<0: P('NEG',k,l['ac'],l['an'],l['dr'],l['cr'],l['naration'])
z=collections.Counter((k[0],k[1]) for k,ls in V.items() for l in ls if l['dr']==0 and l['cr']==0)
P('zero lines',z)
out.close()
