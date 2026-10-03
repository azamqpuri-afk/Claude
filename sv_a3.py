import pickle, collections
L = pickle.load(open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_L.pkl","rb"))
def show(cond, title, n=40, sortamt=True):
    rows = [r for r in L if cond(r)]
    print(f"== {title}: {len(rows)} lines, Rs {round(sum(r['AMT'] for r in rows))}")
    rows.sort(key=lambda r: -r['AMT'] if sortamt else r['DD'])
    for r in rows[:n]:
        print(" ", r['GRN'], r['DD'].date(), r['SUP'][:28], '|', r['IDESC'], '|', r['QTY'], r['UOM'], '@', r['RATE'], '=', round(r['AMT']), '| bill', r['BNO'], r['BD'].date() if r['BD'] else None, '| gst', r['GST_PER'], '| user', r['USERN'], r['FEEDD'])
show(lambda r: r['SUP']=='MIAN AZAM(STORE)' and r['FY']=='FY26', 'MIAN AZAM FY26', 30)
# monthly MIAN AZAM FY26
m = collections.defaultdict(float)
for r in L:
    if r['SUP']=='MIAN AZAM(STORE)' and r['FY'] in ('FY26',): m[r['DD'].strftime('%Y-%m')] += r['AMT']
print(dict(sorted(m.items())))
show(lambda r: r['SUP']=='AR TEXTILE(SOLAR PLANT)', 'AR TEXTILE SOLAR', 10)
show(lambda r: r['SUP']=='AZAM PURI FABRICS', 'AZAM PURI FABRICS', 10)
show(lambda r: r['SUP']=='MB TEXTILE(STORE)', 'MB TEXTILE', 10)
show(lambda r: r['SUP']=='MUDASSAR MULTI TECH', 'MUDASSAR', 10)
show(lambda r: r['SUP']=='SHERAZ NAEEM OIL TRADERS', 'SHERAZ NAEEM', 15, False)
show(lambda r: r['SUP']=='IQ', 'IQ', 15)
show(lambda r: r['SUP']=='AR(STORE)', 'AR(STORE) top', 25)
show(lambda r: r['SUP']=='MIAN AZAM(STORE)' and r['FY'] in ('FY20','FY21'), 'MIAN AZAM FY20-21', 15)
show(lambda r: r['SUP']=='PURCHASE ON CASH', 'PURCHASE ON CASH top', 20)
# AR(STORE) by head
h = collections.defaultdict(float)
for r in L:
    if r['SUP'] in ('AR(STORE)','MIAN AZAM(STORE)','PURCHASE ON CASH','PURCHASES ON CASH','AR STORE'): h[(r['SUP'], r['ERP'], r['HEAD'])] += r['AMT']
for k, v in sorted(h.items(), key=lambda x: -x[1])[:25]: print(k, round(v))
