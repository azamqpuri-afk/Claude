import oracledb, statistics as st
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL"); cur = con.cursor()
sql = """
with b as (select trans_no, ref_sr, count(*) nb, sum(blength*nvl(bwidth,1)) bl from dostex.wybeams group by trans_no, ref_sr),
l as (select vno, vd, sum(case when ac='400701002' then amt end) exp_amt, sum(case when ac='300501007' then amt end) gst from dostex.acl where vt='IGP_SIZED_BEAMS' group by vno, vd)
select m.trans_no, m.tno, m.gp_no, m.td, h.an party, c.cont_no, d.yarn_wt, d.sized_kg, d.set_length, d.ends, b.nb, b.bl, d.rate, d.gully, d.waste,
 y.count ycount, y.blend, m.vno, l.exp_amt, l.gst, m.tt, d.sr_no, m.tdesc
from dostex.wytm m join dostex.wytd d on d.trans_no=m.trans_no
left join b on b.trans_no=d.trans_no and b.ref_sr=d.sr_no
left join dostex.ach h on h.ac=m.ac1 left join dostex.contm c on c.cont_id=d.cont_id
left join dostex.yarn y on y.ycode=d.ycode
left join l on l.vno=m.vno and trunc(l.vd)=trunc(m.vd)
where m.tt in ('IGP_SIZED_BEAMS','OPN_SIZED_BEAMS') order by m.td"""
cur.execute(sql); cols=[c[0] for c in cur.description]; rows=[dict(zip(cols,r)) for r in cur]
print(len(rows),"rows")
def f(x): return x if x is not None else 0
# 1 set_length vs beam length
print("\n# SET_LENGTH vs sum beam length (|diff|>2%) or no beams")
nob=0
for r in rows:
    if not r['BL']:
        if r['SET_LENGTH']: nob+=1; print("NOBEAMS",r['TRANS_NO'],r['TNO'],r['GP_NO'],r['TD'].date(),r['PARTY'],r['CONT_NO'],r['SET_LENGTH'],r['SR_NO'])
        continue
    if r['SET_LENGTH'] and abs(r['BL']-r['SET_LENGTH'])/r['SET_LENGTH']>0.02:
        print("LENDIFF",r['TRANS_NO'],r['TNO'],r['GP_NO'],r['TD'].date(),r['PARTY'],'cont',r['CONT_NO'],'setlen',r['SET_LENGTH'],'beams',r['NB'],r['BL'])
# 2 implied count
print("\n# implied Ne = ends*len/(sized_kg*1000*1.6934) vs yarn count; flag ratio outside 0.85-1.15")
rat=[]
for r in rows:
    if r['ENDS'] and r['SET_LENGTH'] and r['YARN_WT'] and r['YCOUNT']:
        try: cnt=float(str(r['YCOUNT']).split('/')[0])
        except: continue
        ne = r['ENDS']*r['SET_LENGTH']/(r['YARN_WT']*1000)/1.6934
        q = ne/cnt; rat.append(q)
        if q<0.85 or q>1.15: print("CNT",r['TRANS_NO'],r['TNO'],r['TD'].date(),r['PARTY'],'cont',r['CONT_NO'],'yarn',r['YARN_WT'],'sized',r['SIZED_KG'],'ends',r['ENDS'],'len',r['SET_LENGTH'],'count',r['YCOUNT'],'impliedNe',round(ne,1))
if rat: print("median ratio", round(st.median(rat),3), "n", len(rat))
# 3 sized_kg / yarn_wt
print("\n# sized_kg/yarn_wt outside 0.9-1.15")
for r in rows:
    if r['YARN_WT'] and r['SIZED_KG']:
        q=r['SIZED_KG']/r['YARN_WT']
        if q<0.9 or q>1.15: print("SZ",r['TRANS_NO'],r['TNO'],r['GP_NO'],r['TD'].date(),r['PARTY'],'cont',r['CONT_NO'],'yarn',r['YARN_WT'],'sized',r['SIZED_KG'],round(q,2),'rate',r['RATE'],'vno',r['VNO'],'exp',r['EXP_AMT'])
# 4 charges vs ledger per voucher
print("\n# ledger expense vs rate*sized_kg (per trans, diff>1%)")
bytr={}
for r in rows:
    if r['TT']!='IGP_SIZED_BEAMS': continue
    t=bytr.setdefault(r['TRANS_NO'],{'r':r,'calc':0,'calcy':0})
    t['calc']+=f(r['RATE'])*f(r['SIZED_KG']); t['calcy']+=f(r['RATE'])*f(r['YARN_WT'])
nol=0; tot_calc=0; tot_led=0
for k,t in bytr.items():
    r=t['r']; e=r['EXP_AMT']
    tot_calc+=t['calc']; tot_led+=f(e)
    if e is None: nol+=1; print("NOLEDGER",k,r['TNO'],r['GP_NO'],r['TD'].date(),r['PARTY'],'cont',r['CONT_NO'],'vno',r['VNO'],'calc',round(t['calc'])); continue
    if abs(e-t['calc'])>max(1,0.01*t['calc']): print("CHGDIFF",k,r['TNO'],r['TD'].date(),r['PARTY'],'vno',r['VNO'],'ledger',e,'rate*skg',round(t['calc']),'rate*ykg',round(t['calcy']),'rate',r['RATE'],'desc',r['TDESC'])
print("no-ledger trans",nol,"tot calc",round(tot_calc),"tot ledger",round(tot_led))
# 5 rate per kg by party-year
print("\n# rate per kg by party/year (min/median/max)")
g={}
for r in rows:
    if r['TT']=='IGP_SIZED_BEAMS' and r['RATE']: g.setdefault((r['PARTY'],r['TD'].year),[]).append(r['RATE'])
for k in sorted(g): v=g[k]; print(k, len(v), min(v), round(st.median(v),2), max(v))
# 6 waste %
print("\n# waste/yarn >3% or gully/yarn>12%")
for r in rows:
    if r['YARN_WT'] and ((f(r['WASTE'])/r['YARN_WT']>0.03) or (f(r['GULLY'])/r['YARN_WT']>0.12)):
        print("WASTE",r['TRANS_NO'],r['TNO'],r['TD'].date(),r['PARTY'],'cont',r['CONT_NO'],'yarn',r['YARN_WT'],'waste',r['WASTE'],'gully',r['GULLY'])
