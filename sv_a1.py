import pickle, collections, statistics as st
D = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_data.pkl","rb"))
print("IH new", D['ih_new']); print("IH old", D['ih_old'])
# item code overlap
old_names = {}; new_names = {}
for r in D['old']: old_names[r['IC']] = r['IDESC']
for r in D['new']: new_names[r['IC']] = r['IDESC']
common = set(old_names) & set(new_names)
same = sum(1 for c in common if (old_names[c] or '').strip().upper() == (new_names[c] or '').strip().upper())
print("codes old", len(old_names), "new", len(new_names), "common", len(common), "same desc", same)
for c in list(common)[:15]: print(c, old_names[c], '|', new_names[c])
# rate vs avg_rate
for erp in ('old','new'):
    L = D[erp]
    diff = [r for r in L if r['AVG_RATE'] is not None and r['RATE'] is not None and abs(r['AVG_RATE']-r['RATE'])>0.01]
    print(erp, "lines", len(L), "avg!=rate", len(diff), "null rate", sum(1 for r in L if not r['RATE']), "gst>0", sum(1 for r in L if (r['GST_PER'] or 0)>0), "disc", sum(1 for r in L if (r['DISCOUNT'] or 0)>0), "frt", sum(1 for r in L if (r['FREIGHT'] or 0)>0))
    for r in diff[:8]: print("  ", r['DNO'], r['DD'].date(), r['IDESC'], r['QTY'], r['RATE'], r['AVG_RATE'])
    # accounts
    acc = collections.Counter(); amt = collections.defaultdict(float)
    for r in L:
        acc[(r['AC'], r['AN'])] += 1; amt[(r['AC'], r['AN'])] += (r['QTY'] or 0)*(r['RATE'] or 0)
    tot = sum(amt.values())
    print(erp, "total spend", round(tot), "suppliers", len(amt))
    for k,v in sorted(amt.items(), key=lambda x:-x[1])[:30]: print("  ", k, round(v), round(100*v/tot,1), acc[k])
