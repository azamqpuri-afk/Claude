import csv, statistics as st, sys
rows = list(csv.DictReader(open(sys.argv[1])))
def f(x):
    try: return float(x)
    except: return None
for r in rows:
    for k in ('wt_theo','sk_wt','blen_set','chg_per_kg','yarn_wt','sized_kg','theo','chg','set_length','blen','ends','desc_kg','rate','waste','gully','wrate','wqty','nbeams'):
        r[k] = f(r[k])
print("rows", len(rows), "IGP", sum(1 for r in rows if r['tt']=='IGP_SIZED_BEAMS'))
vals = [r['wt_theo'] for r in rows if r['wt_theo']]
print("yarn_wt/theoretical median %.3f  p10 %.3f p90 %.3f n=%d" % (st.median(vals), sorted(vals)[len(vals)//10], sorted(vals)[9*len(vals)//10], len(vals)))
# per sizer
from collections import defaultdict
g = defaultdict(list)
for r in rows:
    if r['tt']=='IGP_SIZED_BEAMS': g[r['sizer']].append(r)
print("\nSIZER | sets | yarn_wt | sized_kg | theo | med wt/theo | charge | chg/kg(yarn_wt) | sets no charge | waste kg")
for k, L in sorted(g.items(), key=lambda x: -len(x[1])):
    yw = sum(r['yarn_wt'] or 0 for r in L); sk = sum(r['sized_kg'] or 0 for r in L); th = sum(r['theo'] or 0 for r in L)
    ch = sum(r['chg'] or 0 for r in L); wt = [r['wt_theo'] for r in L if r['wt_theo']]
    print(k, '|', len(L), '|', round(yw), '|', round(sk), '|', round(th), '|', round(st.median(wt),3) if wt else None, '|', round(ch), '|', round(ch/yw,2) if yw else None, '|', sum(1 for r in L if not r['chg']), '|', round(sum(r['waste'] or 0 for r in L)))
print("\nOUTLIERS yarn_wt/theo <0.85 or >1.25")
for r in rows:
    if r['wt_theo'] and (r['wt_theo'] < 0.85 or r['wt_theo'] > 1.25):
        print(r['trans_no'], r['tno'], r['td'][:10], r['gp_no'], r['sizer'][:22], 'C', r['cont_no'], r['yarn_desc'], 'ends', r['ends'], 'len', r['set_length'], 'ywt', r['yarn_wt'], 'skg', r['sized_kg'], 'theo', round(r['theo']), 'ratio', round(r['wt_theo'],2), '|', r['tdesc'])
print("\nSET LENGTH vs sum beam length mismatches >2%")
for r in rows:
    if r['blen_set'] and abs(r['blen_set']-1) > 0.02:
        print(r['trans_no'], r['tno'], r['td'][:10], r['sizer'][:22], 'C', r['cont_no'], 'setlen', r['set_length'], 'beams', r['nbeams'], 'sumlen', r['blen'], round(r['blen_set'],3))
print("\nNo beams rows:", [(r['trans_no'], r['td'][:10], r['cont_no'], r['set_length']) for r in rows if not r['nbeams']])
print("\nDESC kg vs yarn_wt vs sized_kg mismatch >3% (desc_kg present)")
for r in rows:
    if r['desc_kg'] and r['yarn_wt'] and abs(r['desc_kg']/r['yarn_wt']-1) > 0.03:
        print(r['trans_no'], r['td'][:10], r['sizer'][:22], 'C', r['cont_no'], 'desc', r['desc_kg'], 'ywt', r['yarn_wt'], 'skg', r['sized_kg'], 'chg', r['chg'], 'wqty', r['wqty'], 'wrate', r['wrate'], '|', r['tdesc'])
print("\nSIZED_KG / YARN_WT distribution")
for r in rows:
    if r['sk_wt'] and abs(r['sk_wt']-1) > 0.1:
        print(r['trans_no'], r['td'][:10], r['sizer'][:22], 'C', r['cont_no'], r['yarn_desc'], 'ywt', r['yarn_wt'], 'skg', r['sized_kg'], round(r['sk_wt'],2), 'chg', r['chg'], 'rate', r['wrate'], '|', r['tdesc'])
print("\nCharge rate per kg by year/sizer (chg / wqty)")
g2 = defaultdict(list)
for r in rows:
    if r['chg'] and r['wqty']:
        g2[(r['td'][:4], r['sizer'][:25])].append(r['chg']/r['wqty'])
for k in sorted(g2): print(k, len(g2[k]), round(min(g2[k]),2), round(st.median(g2[k]),2), round(max(g2[k]),2))
print("\nCharge anomalies: chg vs wqty*wrate mismatch or chg per actual kg outliers (>1.5x median of sizer-year)")
for r in rows:
    if r['chg'] and r['wqty'] and r['wrate'] and abs(r['chg'] - r['wqty']*r['wrate']) > 50:
        print('AMTMISMATCH', r['trans_no'], r['td'][:10], r['chg'], r['wqty'], r['wrate'], r['tdesc'])
cpk = [r['chg_per_kg'] for r in rows if r['chg_per_kg']]
med = st.median(cpk)
print('median charge per actual yarn kg', round(med,2))
for r in rows:
    if r['chg_per_kg'] and (r['chg_per_kg'] > 2*med or r['chg_per_kg'] < 0.4*med):
        print('CPK', r['trans_no'], r['td'][:10], r['sizer'][:22], 'C', r['cont_no'], r['yarn_desc'], 'ywt', r['yarn_wt'], 'chg', r['chg'], 'per kg', round(r['chg_per_kg'],1), '|', r['tdesc'])
