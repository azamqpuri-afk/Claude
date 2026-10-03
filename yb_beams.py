import oracledb, datetime as dt, statistics as st
from collections import defaultdict
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
def q(sql):
    cur.execute(sql); cols = [c[0].lower() for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
TODAY = dt.datetime(2026, 9, 26)
beams = q("""select b.trans_no, b.sr_no, b.setno, b.bcode, b.beam_id, b.blength, b.bwidth, m.td, m.tt, m.tno, m.gp_no, c.cont_no, a.an sizer
 from dostex.wybeams b, dostex.wytm m, dostex.wytd d, dostex.contm c, dostex.ach a
 where b.trans_no=m.trans_no and d.trans_no=b.trans_no and d.sr_no=b.ref_sr and d.cont_id=c.cont_id(+) and m.ac1=a.ac(+)""")
B = {(r['trans_no'], r['sr_no']): r for r in beams}
iss = q("""select m.trans_no, m.tno, m.td, d.sr_no, d.lcode, l.lname, d.ref_tno, d.ref_sr, d.beam_id, d.blength, d.bwidth, d.cont_id from dostex.wprdtm m, dostex.wprdtd d, dostex.looms l where m.trans_no=d.trans_no and m.tt='BEAMS_TO_LOOM' and d.lcode=l.lcode(+)""")
ret = q("""select m.trans_no, m.tno, m.td, d.sr_no, d.lcode, d.ref_tno, d.ref_sr, d.ref_iss_id, d.ref_iss_sr, d.bstatus, d.blength, d.bwidth from dostex.wprdtm m, dostex.wprdtd d where m.trans_no=d.trans_no and m.tt='BEAMS_FROM_LOOM'""")
prod = q("""select d.ref_tno, d.ref_sr, d.ref_iss_id, d.ref_iss_sr, d.lcode, min(m.td) f, max(m.td) l, count(*) n, sum(nvl(a,0)+nvl(a2,0)+nvl(b,0)+nvl(c,0)+nvl(cp,0)) mtr from dostex.wprdtm m, dostex.wprdtd d where m.trans_no=d.trans_no and m.tt='PRODUCTION' group by d.ref_tno, d.ref_sr, d.ref_iss_id, d.ref_iss_sr, d.lcode""")
opn = q("select * from dostex.wybeamsopn")
print("beams", len(beams), "issues", len(iss), "returns", len(ret), "prod groups", len(prod), "wybeamsopn", len(opn))
if opn: print("WYBEAMSOPN cols", list(opn[0].keys())); print(opn[:3])
I = {(r['trans_no'], r['sr_no']): r for r in iss}
issues_by_beam = defaultdict(list)
for r in iss: issues_by_beam[(r['ref_tno'], r['ref_sr'])].append(r)
ret_by_iss = defaultdict(list)
for r in ret: ret_by_iss[(r['ref_iss_id'], r['ref_iss_sr'])].append(r)
prod_by_iss = defaultdict(list); prod_by_beam = defaultdict(float)
for r in prod:
    prod_by_iss[(r['ref_iss_id'], r['ref_iss_sr'])].append(r); prod_by_beam[(r['ref_tno'], r['ref_sr'])] += r['mtr'] or 0
# 1. integrity
print("\n== integrity")
print("issues whose ref beam missing in WYBEAMS:", sum(1 for r in iss if (r['ref_tno'], r['ref_sr']) not in B))
orph_ret = [r for r in ret if (r['ref_iss_id'], r['ref_iss_sr']) not in I]
print("returns whose ref issue missing:", len(orph_ret), [(r['trans_no'], r['td'].date(), r['ref_iss_id'], r['bstatus']) for r in orph_ret[:15]])
orph_p = [r for r in prod if (r['ref_iss_id'], r['ref_iss_sr']) not in I]
print("production groups with ref_iss not a BEAMS_TO_LOOM row:", len(orph_p), round(sum(r['mtr'] or 0 for r in orph_p)), [(r['ref_iss_id'], r['ref_iss_sr'], r['lcode'], r['f'].date(), round(r['mtr'])) for r in orph_p[:20]])
mism = []
for r in prod:
    i = I.get((r['ref_iss_id'], r['ref_iss_sr']))
    if i and (i['lcode'] != r['lcode'] or (i['ref_tno'], i['ref_sr']) != (r['ref_tno'], r['ref_sr'])):
        mism.append((r, i))
print("production rows where loom or beam differs from the referenced issue:", len(mism), round(sum(r['mtr'] or 0 for r, i in mism)))
for r, i in mism[:25]: print("  prod loom", r['lcode'], "beam", (r['ref_tno'], r['ref_sr']), "| issue", i['trans_no'], i['td'].date(), "loom", i['lcode'], "beam", (i['ref_tno'], i['ref_sr']), "mtr", round(r['mtr']), r['f'].date(), r['l'].date())
multi_ret = {k: v for k, v in ret_by_iss.items() if len(v) > 1}
print("issues returned more than once:", len(multi_ret), [(k, [(x['trans_no'], x['td'].date(), x['bstatus']) for x in v]) for k, v in list(multi_ret.items())[:10]])
# production outside mount window
early, late = [], []
for k, plist in prod_by_iss.items():
    i = I.get(k)
    if not i: continue
    rr = ret_by_iss.get(k)
    for p in plist:
        if p['f'] < i['td']: early.append((k, i['td'].date(), p['f'].date(), round(p['mtr'])))
        if rr and p['l'] > max(x['td'] for x in rr): late.append((k, i['lname'], max(x['td'] for x in rr).date(), p['l'].date(), round(p['mtr'])))
print("production dated before its beam's mount date:", len(early), early[:15])
print("production dated after its beam was taken off loom:", len(late), late[:15])
# 2. per-issue balance
print("\n== per mounted issue (completed/cut)")
rows = []
for k, i in I.items():
    bm = B.get((i['ref_tno'], i['ref_sr']))
    alen = (bm['blength'] or 0) * (i['bwidth'] or 1) if bm else None
    p = sum(x['mtr'] or 0 for x in prod_by_iss.get(k, []))
    rr = ret_by_iss.get(k)
    st_ = rr[-1]['bstatus'] if rr else 'AT_LOOM'
    lastp = max((x['l'] for x in prod_by_iss.get(k, [])), default=None)
    rows.append(dict(k=k, td=i['td'], loom=i['lname'], cont=bm['cont_no'] if bm else None, setno=bm['setno'] if bm else None, bcode=bm['bcode'] if bm else None, alen=alen, prod=p, status=st_, rtd=rr[-1]['td'] if rr else None, lastp=lastp, bw=i['bwidth'], sizer=bm['sizer'] if bm else None, beamkey=(i['ref_tno'], i['ref_sr'])))
comp = [r for r in rows if r['status'] == 'COMPLETE' and r['alen']]
# for complete beams use whole-beam production (a beam could be cut and remounted)
for r in comp:
    r['bprod'] = prod_by_beam[r['beamkey']]
    r['bal'] = (r['alen'] - r['bprod']) / r['alen'] * 100
bals = sorted(r['bal'] for r in comp)
print("COMPLETE beams:", len(comp), "median bal%% %.2f p10 %.2f p90 %.2f" % (st.median(bals), bals[len(bals)//10], bals[9*len(bals)//10]))
tot_alen = sum(r['alen'] for r in comp); tot_p = sum(r['bprod'] for r in comp)
print("  total length %d, total prod %d, shortage %.2f%%" % (tot_alen, tot_p, (tot_alen-tot_p)/tot_alen*100))
print("  bal<-2% (prod beyond length):", sum(1 for r in comp if r['bal'] < -2), " bal>15%:", sum(1 for r in comp if r['bal'] > 15), " zero prod:", sum(1 for r in comp if r['bprod'] == 0))
for r in sorted(comp, key=lambda r: r['bal'])[:15]:
    print("  OVER", r['k'], r['td'].date(), r['loom'], 'C', r['cont'], 'set', r['setno'], 'beam', r['bcode'], 'len', r['alen'], 'prod', round(r['bprod']), 'bal%', round(r['bal'], 1), 'off', r['rtd'].date())
for r in sorted(comp, key=lambda r: -r['bal'])[:30]:
    print("  SHORT", r['k'], r['td'].date(), r['loom'], 'C', r['cont'], 'set', r['setno'], 'beam', r['bcode'], 'len', r['alen'], 'prod', round(r['bprod']), 'bal%', round(r['bal'], 1), 'off', r['rtd'].date())
# by sizer and contract
g = defaultdict(lambda: [0, 0, 0])
for r in comp:
    g[r['sizer']][0] += r['alen']; g[r['sizer']][1] += r['bprod']; g[r['sizer']][2] += 1
print("  by sizer (complete beams):", {k: (v[2], round(v[0]), round(v[1]), round((v[0]-v[1])/v[0]*100, 2)) for k, v in g.items()})
g = defaultdict(lambda: [0, 0, 0])
for r in comp:
    g[r['cont']][0] += r['alen']; g[r['cont']][1] += r['bprod']; g[r['cont']][2] += 1
print("  by contract (complete beams) shortage%:", sorted([(k, v[2], round(v[0]), round(v[1]), round((v[0]-v[1])/v[0]*100, 2)) for k, v in g.items()], key=lambda x: -x[4]))
g = defaultdict(lambda: [0, 0, 0])
for r in comp:
    g[r['td'].year][0] += r['alen']; g[r['td'].year][1] += r['bprod']; g[r['td'].year][2] += 1
print("  by year:", {k: (v[2], round((v[0]-v[1])/v[0]*100, 2)) for k, v in sorted(g.items())})
cut = [r for r in rows if r['status'] == 'CUT']
print("\nCUT issues:", len(cut))
for r in cut:
    remounted = len(issues_by_beam[r['beamkey']]) > 1
    r['remounted'] = remounted
print("  CUT never remounted:", sum(1 for r in cut if not r['remounted']), " remaining metres (alen - beam prod) of never-remounted:", round(sum((r['alen'] or 0) - prod_by_beam[r['beamkey']] for r in cut if not r['remounted'])))
for r in cut:
    if not r['remounted']:
        rem = (r['alen'] or 0) - prod_by_beam[r['beamkey']]
        if rem > 500: print("   CUT-left", r['k'], r['td'].date(), r['loom'], 'C', r['cont'], 'set', r['setno'], 'beam', r['bcode'], 'len', r['alen'], 'prod', round(prod_by_beam[r['beamkey']]), 'left', round(rem), 'cut on', r['rtd'].date())
# 3. at loom
atl = [r for r in rows if r['status'] == 'AT_LOOM']
print("\nAT LOOM now:", len(atl))
for r in sorted(atl, key=lambda r: r['td']):
    idle = (TODAY - (r['lastp'] or r['td'])).days
    if idle > 20 or (TODAY - r['td']).days > 120 or (r['alen'] and prod_by_beam[r['beamkey']] > r['alen']):
        print("  ", r['k'], 'mounted', r['td'].date(), r['loom'], 'C', r['cont'], 'set', r['setno'], 'beam', r['bcode'], 'len', r['alen'], 'prod', round(prod_by_beam[r['beamkey']]), 'last prod', r['lastp'].date() if r['lastp'] else None, 'idle days', idle)
# loom concurrency: more than 2 beams open on the same loom
from itertools import combinations
byloom = defaultdict(list)
for r in rows:
    end = r['rtd'] or TODAY
    byloom[r['loom']].append((r['td'], end, r))
over = 0; ex = []
for l, L in byloom.items():
    for (a1, e1, r1), (a2, e2, r2) in combinations(L, 2):
        if a1 < e2 and a2 < e1 and (min(e1, e2) - max(a1, a2)).days > 3 and r1['beamkey'] != r2['beamkey']:
            over += 1; ex.append((l, r1['k'], r1['td'].date(), (r1['rtd'] or TODAY).date(), r1['cont'], r2['k'], r2['td'].date(), (r2['rtd'] or TODAY).date(), r2['cont']))
print("\nloom overlaps (two different beams open >3 days on same loom):", over)
for e in ex[:20]: print("  ", e)
# 4. never mounted
never = [b for k, b in B.items() if k not in issues_by_beam]
print("\nsized beams never mounted:", len(never), "metres", round(sum((b['blength'] or 0) for b in never)))
agebk = defaultdict(lambda: [0, 0])
for b in never:
    age = (TODAY - b['td']).days
    bucket = '<60d' if age < 60 else '60-180d' if age < 180 else '180-365d' if age < 365 else '>1y'
    agebk[bucket][0] += 1; agebk[bucket][1] += b['blength'] or 0
print(" by age:", dict(agebk))
for b in sorted(never, key=lambda b: b['td']):
    if (TODAY - b['td']).days > 180: print("   ", (b['trans_no'], b['sr_no']), b['td'].date(), b['tt'], 'C', b['cont_no'], 'set', b['setno'], 'beam', b['bcode'], 'len', b['blength'], b['sizer'])
# physical beam id reuse conflicts: new set received on beam_id while previous set on same beam_id still at loom/not complete
print("\n beams issued multiple times:", sum(1 for k, v in issues_by_beam.items() if len(v) > 1))
