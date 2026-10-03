import pickle, collections, datetime as dt
D = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\\"
d = pickle.load(open(D+"fr_data.pkl","rb"))
led, vouch = d['led'], d['vouch']
nm = {('OLD',a['ac']):a['an'] for a in d['allo']}; nm.update({('NEW',a['ac']):a['an'] for a in d['alln']})
V = collections.defaultdict(list)
for r in vouch: V[(r['src'],r['vt'],r['vno'],r['vd'])].append(r)
def cust(src,ac): return ac.startswith('501') if src=='OLD' else ac.startswith('3003')
# unified key: old ledger + new excluding OPN
KEYS = {'AR TEXTILE':[('OLD','501400001'),('NEW','300306004')], 'AR TEXTILE(SOLAR)':[('NEW','300306009')],
 'QADRI(all codes)':[('OLD','501100025'),('OLD','501100029'),('OLD','501100031'),('NEW','300306002')],
 'MIAN AZAM(STORE)':[('OLD','301200102'),('NEW','200105006')], 'AR(STORE)':[('OLD','301200010'),('NEW','200105001')],
 'HAROON':[('OLD','501100034'),('NEW','300306003')], 'DF':[('NEW','300306013')],
 'MANZOOR SIZING':[('OLD','301300016'),('NEW','200106003')]}
L = [r for r in led if r['vt']!='OPN']
print("=== 30-Jun balances (old ledger + new ledger ex-OPN)")
yrs=[dt.datetime(y,6,30) for y in range(2014,2027)]+[dt.datetime(2023,9,30),dt.datetime(2026,9,26)]
yrs.sort()
print("key|"+"|".join(y.strftime('%y-%m') for y in yrs))
for k,acs in KEYS.items():
    s=set(acs); rs=[r for r in L if (r['src'],r['ac']) in s]
    print(k+"|"+"|".join(str(round(sum(r['amt'] for r in rs if r['vd']<=y)/1e5,1)) for y in yrs))
print("(Rs lac; +=debit)")
# AR TEXTILE flow classification
def cls(src,ac):
    n=(nm.get((src,ac)) or '')
    if cust(src,ac): return 'CUSTOMER:'+n
    if src=='OLD':
        h=ac[:2]
        m={'15':'EQUITY/DRAWINGS','40':'FIXED ASSETS','42':'FIXED ASSETS','75':'MFG EXPENSE','85':'ADMIN EXP','35':'ACCRUED/PAYABLE','30':'SUPPLIER','60':'STOCK','55':'ADV/OTHER RECV','65':'CASH/BANK','70':'REVENUE','95':'FIN'}
        return m.get(h,h)+ (':'+n if h in('55','15','30') else '')
    h=ac[:2]
    m={'10':'EQUITY/DRAWINGS','25':'FIXED ASSETS','40':'MFG EXPENSE','45':'ADMIN EXP','20':'PAYABLE','30':'CURRENT ASSET','35':'REVENUE','55':'FIN'}
    c=m.get(h,h)
    if ac.startswith('3008'): c='CASH/BANK'
    if ac.startswith('2001'): c='SUPPLIER'
    return c+(':'+n if h in('10',) or ac.startswith('2001') or ac.startswith('3004') or ac.startswith('3006') else '')
for keyname in ['AR TEXTILE','AR TEXTILE(SOLAR)','DF']:
    print("\n===",keyname,"flows by counter class (DR=money/value TO party, CR=from party)")
    fl=collections.Counter()
    for r in L:
        if (r['src'],r['ac']) in set(KEYS[keyname]):
            lines=V[(r['src'],r['vt'],r['vno'],r['vd'])]
            opp=[l for l in lines if (l['amt'] or 0)*(r['amt'] or 0)<0]
            tot=sum(abs(l['amt']) for l in opp) or 1
            for l in opp:
                c=cls(l['src'],l['ac'])
                if c.startswith('CUSTOMER'): c='CUSTOMERS (transfer)'
                fl[('DR' if r['amt']>0 else 'CR', r['vt'] if r['vt'] in('BR','CR','BP','CP') else 'JV/other', c)] += abs(r['amt'])*abs(l['amt'])/tot
    for k,v in sorted(fl.items(), key=lambda x:-x[1]):
        if v>=200000: print(k, round(v/1e5,1),'lac')
# customer transfers to AR TEXTILE by year
print("\n=== customer balances moved to AR TEXTILE(S) by JV, by fiscal year (Rs lac), and to drawings/expenses")
by=collections.Counter()
for r in L:
    if cust(r['src'],r['ac']) and r['amt']<0 and r['vt']=='JV' and r['ac'] not in('501400001','300306004','300306009'):
        lines=V[(r['src'],r['vt'],r['vno'],r['vd'])]
        dr=[l for l in lines if l['amt']>0 and l['ac']!=r['ac']]; tot=sum(l['amt'] for l in dr) or 1
        fy=r['vd'].year+(1 if r['vd'].month>6 else 0)
        for l in dr:
            c=cls(l['src'],l['ac'])
            c='AR TEXTILE' if l['ac'] in('501400001','300306004','300306009') else ('DRAWINGS' if 'DRAWING' in c else ('OTHER CUSTOMER' if c.startswith('CUSTOMER') else c.split(':')[0]))
            by[(fy,c)] += -r['amt']*l['amt']/tot
cats=sorted(set(c for _,c in by)); print("FY|"+"|".join(cats))
for fy in sorted(set(f for f,_ in by)): print(str(fy)+"|"+"|".join(str(round(by[(fy,c)]/1e5,1)) for c in cats))
print("TOTAL|"+"|".join(str(round(sum(v for (f,c),v in by.items() if c==cc)/1e5,1)) for cc in cats))
# sample narrations
print("\n=== sample narrations: customer->AR TEXTILE JVs")
k=0
for r in sorted(L,key=lambda r:r['vd']):
    if cust(r['src'],r['ac']) and r['amt']<0 and r['vt']=='JV' and r['ac'] not in('501400001','300306004'):
        lines=V[(r['src'],r['vt'],r['vno'],r['vd'])]
        if any(l['ac'] in('501400001','300306004') and l['amt']>0 for l in lines):
            k+=1
            if k%25==1: print(r['src'],r['vd'].date(),'JV',r['vno'],nm.get((r['src'],r['ac'])),round(r['amt']),'|',r['naration'])
print("\n=== AR TEXTILE own narrations sample (large)")
for r in sorted([r for r in L if r['ac'] in('501400001','300306004','300306009') and abs(r['amt'])>=1500000],key=lambda r:r['vd']):
    print(r['src'],r['vd'].date(),r['vt'],r['vno'],r['ac'],round(r['amt']),'|',(r['naration'] or '')[:110])
