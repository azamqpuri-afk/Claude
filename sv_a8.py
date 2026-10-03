import pickle, collections, re
D = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_data.pkl","rb"))
L, _ = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L2.pkl","rb"))
iss = {('OLD', r['IC']): r for r in D['iss_old']}; iss.update({('NEW', r['IC']): r for r in D['iss_new']})
p = collections.defaultdict(lambda: [0.0, 0.0, None, set(), None])
for r in L:
    x = p[(r['ERP'], r['IC'])]; x[0] += r['QTY']; x[1] += r['AMT']; x[2] = r['IDESC']; x[3].add(r['SUP'][:18]); x[4] = max(x[4], r['DD']) if x[4] else r['DD']
rows = []
for k, (q, a, n, s, last) in p.items():
    iq = (iss.get(k) or {}).get('Q') or 0
    if q > 0 and iq < 0.5*q:
        rows.append((a*(q-iq)/q, k, n, q, iq, a, s, last, (iss.get(k) or {}).get('LAST_DD')))
rows.sort(key=lambda x: -x[0])
print("== purchased but <50% issued (same ERP), value of un-issued share:", len(rows), round(sum(r[0] for r in rows)))
for r in rows[:35]:
    print(f" {r[1][0]} {r[1][1]} {r[2]} | bought {round(r[3],1)} issued {round(r[4],1)} | purch Rs {round(r[5])} unissued Rs {round(r[0])} | {sorted(r[6])[:3]} last buy {r[7].date()} last iss {r[8].date() if r[8] else None}")
# key consumables purchased vs issued per year (new ERP)
print("== HD-100/SAE/diesel/perchlor bought vs issued, by ERP")
for k, x in p.items():
    if re.search(r'HD-100|SAE-30/40|FOCUCH|DIESEL|PERCHLOR|INTELU|CIRCLE', x[2] or ''):
        iq = (iss.get(k) or {}).get('Q') or 0
        print(" ", k, x[2], "bought", round(x[0]), "issued", round(iq), "Rs", round(x[1]))
