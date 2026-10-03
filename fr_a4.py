import pickle, collections, datetime as dt, re
D = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\\"
d = pickle.load(open(D+"fr_data.pkl","rb"))
led, vouch = d['led'], d['vouch']
nm = {('OLD',a['ac']):a['an'] for a in d['allo']}; nm.update({('NEW',a['ac']):a['an'] for a in d['alln']})
V = collections.defaultdict(list)
for r in vouch: V[(r['src'],r['vt'],r['vno'],r['vd'])].append(r)
def opp(r):
    return [(l['ac'],nm.get((l['src'],l['ac'])),round(l['amt'])) for l in V[(r['src'],r['vt'],r['vno'],r['vd'])] if (l['amt'] or 0)*(r['amt'] or 0)<0]
L=[r for r in led if r['vt']!='OPN']
# Salman Arts & 26% transfers
sa=[r for r in L if 'SALMAN ARTS' in (r['naration'] or '').upper() and r['amt']>0]
print("SALMAN ARTS receipts booked to AR TEXTILE: n=",len(sa),"total",round(sum(r['amt'] for r in sa)), "from",min(r['vd'] for r in sa).date(),"to",max(r['vd'] for r in sa).date())
t26=[r for r in L if '26%' in (r['naration'] or '') and r['ac']=='300306004']
print("26% of Qadri to AR TEXTILE: n=",len(t26),"total",round(sum(r['amt'] for r in t26)))
for r in t26: print("  ",r['vd'].date(),'JV',r['vno'],round(r['amt']),r['naration'])
print("\n=== QADRI (new) JV credits: narration/opp")
for r in sorted([r for r in L if r['ac']=='300306002' and r['vt']=='JV' and r['amt']<0 and r['amt']<-300000],key=lambda r:r['vd']):
    print(r['vd'].date(),'JV',r['vno'],round(r['amt']),(r['naration'] or '')[:70],opp(r)[:3])
print("\n=== Other new customers: all non-GPOUT/BR lines")
for ac in ['300306013','300306014','300306015','300302001','300306011','300306010','300306016','300302002','300306012','300306007','300308001','300306008']:
    print('--',ac,nm.get(('NEW',ac)))
    for r in sorted([r for r in L if r['ac']==ac],key=lambda r:r['vd']):
        if r['vt'] in('BR','CR') and abs(r['amt'])<500000: continue
        print('  ',r['vd'].date(),r['vt'],r['vno'],round(r['amt']),(r['naration'] or '')[:60],opp(r)[:3] if r['vt'] not in('GPOUT',) else '')
# supplier lines: sizing parties & suspicious suppliers
print("\n=== Sizing parties: charges vs payments vs receipts")
for src,pre in [('OLD','3013'),('NEW','200106')]:
    for ac in sorted(set(r['ac'] for r in L if r['src']==src and r['ac'].startswith(pre))):
        rs=[r for r in L if r['src']==src and r['ac']==ac]
        c=collections.Counter()
        for r in rs: c[r['vt']]+=r['amt']
        print(src,ac,nm.get((src,ac)),{k:round(v) for k,v in c.items()})
print("\n=== sizing/supplier BR/CR (money received FROM suppliers)")
for r in sorted([r for r in L if ((r['src']=='NEW' and r['ac'].startswith('2001')) or (r['src']=='OLD' and r['ac'].startswith('30'))) and r['vt'] in('BR','CR')],key=lambda r:-abs(r['amt']))[:45]:
    print(r['src'],r['vd'].date(),r['vt'],r['vno'],r['ac'],nm.get((r['src'],r['ac'])),round(r['amt']),(r['naration'] or '')[:70])
print("\n=== suspicious suppliers full lines")
for src,ac in [('NEW','200105013'),('NEW','200105061'),('NEW','200105054'),('NEW','200106006'),('NEW','200106013'),('NEW','200109013'),('NEW','200105063'),('OLD','301200056'),('OLD','301200058'),('OLD','301200084'),('NEW','200105039'),('OLD','301600006'),('NEW','200109007')]:
    print('--',src,ac,nm.get((src,ac)))
    for r in sorted([r for r in L if r['src']==src and r['ac']==ac],key=lambda r:r['vd']):
        print('  ',r['vd'].date(),r['vt'],r['vno'],round(r['amt']),(r['naration'] or '')[:70],opp(r)[:2])
