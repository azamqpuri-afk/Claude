import collections, re
d = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad"
exec(open(d+r"\fj_a2.py").read().split("out=open")[0])
def fmt(v): return f"{v:,.0f}"
def fy(dt): return dt.year+1 if dt.month>=7 else dt.year
ART=('501400001','300306004','300306009')
byfy=collections.defaultdict(float); bycust=collections.defaultdict(float); n=0
for k,ls in V.items():
    if k[1]!='JV': continue
    if not any(l['ac'] in ART and l['dr'] for l in ls): continue
    for l in ls:
        if l['cat']=='DEBT' and l['cr'] and l['ac'] not in ART and re.search(r'CONVERSION|BILL M/O|SIZING BILL',l['naration'] or '',re.I):
            byfy[(k[0],fy(k[3]))]+=l['cr']; bycust[(k[0],l['an'])]+=l['cr']; n+=1
print('monthly conversion-bill transfers to AR TEXTILES: lines',n,'amt',fmt(sum(byfy.values())))
for kk in sorted(byfy): print('  ',kk,fmt(byfy[kk]))
for kk,v in sorted(bycust.items(),key=lambda x:-x[1])[:15]: print('  ',kk,fmt(v))
# AR TEXTILES net JV movement and all-voucher movement
for e in ('OLD','NEW'):
    t=collections.defaultdict(lambda:[0,0])
    for k,ls in V.items():
        if k[0]!=e: continue
        for l in ls:
            if l['ac'] in ART: t[k[1]][0]+=l['dr']; t[k[1]][1]+=l['cr']
    print(e,'AR TEXTILES by vt', {a:(fmt(b[0]),fmt(b[1])) for a,b in t.items()})
# drawings total by vt
for e in ('OLD','NEW'):
    t=collections.defaultdict(float)
    for k,ls in V.items():
        if k[0]!=e: continue
        for l in ls:
            if l['cat']=='DRAW': t[k[1]]+=l['dr']-l['cr']
    print(e,'net drawings by vt',{a:fmt(b) for a,b in t.items()})
# JV amount share, month-end
for e in ('OLD','NEW'):
    J=[(k,ls) for k,ls in V.items() if k[0]==e and k[1]=='JV']
    me=[x for x in J if (x[0][3]+datetime.timedelta(days=1)).day==1]
    mid=[x for x in J if x[0][3].day==15]
    print(e,'JVs',len(J),'amt',fmt(sum(vsum(x[1]) for x in J)),'month-end',len(me),fmt(sum(vsum(x[1]) for x in me)),'15th',len(mid))
    lines=[len(x[1]) for x in J]; print('  avg lines/JV',round(sum(lines)/len(lines),1),'max',max(lines),'JVs >=20 lines',sum(1 for x in lines if x>=20))
