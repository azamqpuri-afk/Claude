import pickle, collections
D = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\\"
d = pickle.load(open(D+"fr_data.pkl","rb"))
led, vouch = d['led'], d['vouch']
nm = {('OLD',a['ac']):a['an'] for a in d['allo']}; nm.update({('NEW',a['ac']):a['an'] for a in d['alln']})
V = collections.defaultdict(list)
for r in vouch: V[(r['src'],r['vt'],r['vno'],r['vd'])].append(r)
def cust(src,ac): return ac.startswith('501') if src=='OLD' else ac.startswith('3003')
def supp(src,ac): return ac.startswith('30') if src=='OLD' else ac.startswith('2001')
# 1. How customer debits (invoices) are cleared: credits to customers by vt and by counter account
print("=== Customer CREDITS by src, vt (amount)")
agg=collections.Counter(); cnt=collections.Counter()
for r in led:
    if cust(r['src'],r['ac']) and (r['amt'] or 0)<0:
        agg[(r['src'],r['vt'])]+= -r['amt']; cnt[(r['src'],r['vt'])]+=1
for k,v in sorted(agg.items()): print(k, round(v), cnt[k])
print("\n=== Counter accounts (debit side) of JV credits to customers, by src/customer->counter")
cc=collections.Counter()
for r in led:
    if cust(r['src'],r['ac']) and (r['amt'] or 0)<0 and r['vt']=='JV':
        lines=V[(r['src'],r['vt'],r['vno'],r['vd'])]
        dr=[l for l in lines if (l['amt'] or 0)>0 and l['ac']!=r['ac']]
        tot=sum(l['amt'] for l in dr) or 1
        for l in dr: cc[(r['src'], nm.get((r['src'],r['ac'])), l['ac'], nm.get((r['src'],l['ac'])))] += -r['amt']*l['amt']/tot
for k,v in sorted(cc.items(), key=lambda x:-x[1])[:70]: print(k, round(v))
print("\n=== Counter accounts (credit side) of JV DEBITS to customers")
cc=collections.Counter()
for r in led:
    if cust(r['src'],r['ac']) and (r['amt'] or 0)>0 and r['vt']=='JV':
        lines=V[(r['src'],r['vt'],r['vno'],r['vd'])]
        cr=[l for l in lines if (l['amt'] or 0)<0 and l['ac']!=r['ac']]
        tot=-sum(l['amt'] for l in cr) or 1
        for l in cr: cc[(r['src'], nm.get((r['src'],r['ac'])), l['ac'], nm.get((r['src'],l['ac'])))] += r['amt']*(-l['amt'])/tot
for k,v in sorted(cc.items(), key=lambda x:-x[1])[:50]: print(k, round(v))
print("\n=== Supplier DEBITS by vt")
agg=collections.Counter(); cnt=collections.Counter()
for r in led:
    if supp(r['src'],r['ac']) and (r['amt'] or 0)>0:
        agg[(r['src'],r['vt'])]+= r['amt']; cnt[(r['src'],r['vt'])]+=1
for k,v in sorted(agg.items()): print(k, round(v), cnt[k])
print("\n=== Counter accounts of JV lines on suppliers (both sides), top")
cc=collections.Counter()
for r in led:
    if supp(r['src'],r['ac']) and r['vt']=='JV':
        lines=V[(r['src'],r['vt'],r['vno'],r['vd'])]
        opp=[l for l in lines if (l['amt'] or 0)*(r['amt'] or 0)<0]
        tot=sum(abs(l['amt']) for l in opp) or 1
        for l in opp: cc[(r['src'], nm.get((r['src'],r['ac'])), 'DR' if r['amt']>0 else 'CR', l['ac'], nm.get((r['src'],l['ac'])))] += abs(r['amt'])*abs(l['amt'])/tot
for k,v in sorted(cc.items(), key=lambda x:-x[1])[:70]: print(k, round(v))
