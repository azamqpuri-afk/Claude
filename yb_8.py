import oracledb, re, statistics as st
from collections import defaultdict
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL"); cur = con.cursor()
def q(s, **k):
    cur.execute(s, **k); c=[x[0] for x in cur.description]; return [dict(zip(c,r)) for r in cur]
KG="nvl(d.yarn_wt, nvl(d.bags,0)*45.36 + nvl(d.cones,0)*45.36/nullif(d.cone_pbag,0))"
# (a) party-level balance
rows=q(f"""with y as (select d.cont_id, m.tt, {KG} kg from dostex.wytm m join dostex.wytd d on d.trans_no=m.trans_no),
p as (select d.cont_id, sum(nvl(a,0)+nvl(a2,0)+nvl(b,0)+nvl(c,0)+nvl(cp,0)) prod from dostex.wprdtm m join dostex.wprdtd d on d.trans_no=m.trans_no where m.tt='PRODUCTION' group by d.cont_id),
ya as (select cont_id, sum(case when tt='IGP_YARN' then kg end) igp, sum(case when tt='OGP_YARN' then kg end) ogp,
 sum(case when tt='TO_SIZING' then kg when tt='FROM_SIZING' then -kg end) netsz,
 sum(case when tt='YARN_TO_LOOMS' then kg when tt='YARN_FROM_LOOMS' then -kg end) netweft from y group by cont_id)
select h.an party, count(*) nconts, listagg(c.cont_no,',') within group (order by c.cont_id) conts,
 round(sum(igp)) igp, round(sum(ogp)) ogp, round(sum(netsz)) netsz, round(sum(netweft)) netweft,
 round(sum(p.prod)) prod, round(sum(p.prod*(c.warp_weight+c.weft_weight)*45.36/100)) req_total,
 round(sum(nvl(igp,0)-nvl(ogp,0)-nvl(netsz,0)-nvl(netweft,0))) bal
from dostex.contm c left join ya on ya.cont_id=c.cont_id left join p on p.cont_id=c.cont_id left join dostex.ach h on h.ac=c.party_code
group by h.an order by 1""")
print("## party-level new ERP");
for r in rows: print(r)
# (b) monthly weft actual vs expected (mill total)
rows=q(f"""with w as (select d.cont_id, trunc(m.td,'MM') mth, sum(case when m.tt='YARN_TO_LOOMS' then {KG} else -{KG} end) kg from dostex.wytm m join dostex.wytd d on d.trans_no=m.trans_no where m.tt in ('YARN_TO_LOOMS','YARN_FROM_LOOMS') group by d.cont_id, trunc(m.td,'MM')),
p as (select d.cont_id, trunc(m.td,'MM') mth, sum(nvl(a,0)+nvl(a2,0)+nvl(b,0)+nvl(c,0)+nvl(cp,0)) prod from dostex.wprdtm m join dostex.wprdtd d on d.trans_no=m.trans_no where m.tt='PRODUCTION' group by d.cont_id, trunc(m.td,'MM'))
select to_char(nvl(w.mth,p.mth),'YYYY-MM') mth, round(sum(w.kg)) weft_kg, round(sum(p.prod)) prod, round(sum(p.prod*c.weft_weight*45.36/100)) exp_kg
from w full outer join p on p.cont_id=w.cont_id and p.mth=w.mth left join dostex.contm c on c.cont_id=nvl(w.cont_id,p.cont_id) group by to_char(nvl(w.mth,p.mth),'YYYY-MM') order by 1""")
print("\n## monthly weft")
for r in rows: print(r['MTH'], r['WEFT_KG'], r['PROD'], r['EXP_KG'], round((r['WEFT_KG'] or 0)/r['EXP_KG']*100) if r['EXP_KG'] else None)
# (c) sizing narration vs ledger vs WYTD
led=q("""select vno, trunc(vd) vd, max(naration) nar, sum(case when ac='400701002' then amt end) exp, sum(case when ac='300501007' then amt end) gst, -sum(case when ac like '200106%' then amt end) party from dostex.acl where vt='IGP_SIZED_BEAMS' group by vno, trunc(vd)""")
wy=q("""select m.trans_no, m.tno, trunc(m.td) td, m.gp_no, h.an party, c.cont_no, sum(d.yarn_wt) ykg, sum(d.sized_kg) skg, max(m.tdesc) tdesc
 from dostex.wytm m join dostex.wytd d on d.trans_no=m.trans_no left join dostex.ach h on h.ac=m.ac1 left join dostex.contm c on c.cont_id=d.cont_id
 where m.tt='IGP_SIZED_BEAMS' group by m.trans_no, m.tno, trunc(m.td), m.gp_no, h.an, c.cont_no""")
L={}
for l in led: L.setdefault(l['VNO'],[]).append(l)
print("\n## ledger vouchers", len(led), "IGP_SIZED_BEAMS trans", len(wy), "dup vno in ledger", sum(1 for v in L.values() if len(v)>1))
rx=re.compile(r'([\d,]+\.?\d*)\s*KG\s*@\s*([\d.]+)',re.I)
eff=defaultdict(list); nol=[]
for w in wy:
    ls=[l for l in L.get(w['TNO'],[]) if l['VD']==w['TD']] or L.get(w['TNO'],[])
    if not ls: nol.append(w); continue
    l=ls[0]; m=rx.search(l['NAR'] or '')
    nkg,nrate=(float(m.group(1).replace(',','')),float(m.group(2))) if m else (None,None)
    exp=l['EXP'] or 0
    if w['YKG']: eff[(w['PARTY'],w['TD'].year)].append(exp/w['YKG'])
    flags=[]
    if nkg and abs(nkg*nrate-exp)>max(50,0.01*exp): flags.append(f"exp {exp} != narr {nkg}x{nrate}={round(nkg*nrate)}")
    if nkg and w['YKG'] and abs(nkg-w['YKG'])/w['YKG']>0.05: flags.append(f"narr kg {nkg} vs yarn_wt {round(w['YKG'],1)} sized {w['SKG']}")
    if w['YKG'] and exp/w['YKG']>65: flags.append(f"Rs/kg {round(exp/w['YKG'],1)}")
    if l['GST'] and exp and abs(l['GST']/exp-0.09)>0.005 and abs(l['GST']/exp-0.18)>0.005: flags.append(f"gst% {round(l['GST']/exp*100,1)}")
    if flags: print("FLAG tr",w['TRANS_NO'],'tno',w['TNO'],'gp',w['GP_NO'],w['TD'],w['PARTY'],'cont',w['CONT_NO'],'|',' ; '.join(flags),'| nar:',l['NAR'])
print("\nno ledger voucher:",len(nol))
for w in nol: print("  NOLED tr",w['TRANS_NO'],'tno',w['TNO'],'gp',w['GP_NO'],w['TD'],w['PARTY'],'cont',w['CONT_NO'],'ykg',w['YKG'],'desc',w['TDESC'])
print("\n## effective Rs per yarn kg (ledger expense excl GST / YARN_WT)")
for k in sorted(eff): v=eff[k]; print(k,len(v),round(min(v),1),round(st.median(v),1),round(max(v),1))
wtn={w['TNO'] for w in wy}
print("ledger vouchers w/o IGP_SIZED_BEAMS trans:", [ (l['VNO'],l['VD'],l['EXP']) for l in led if l['VNO'] not in wtn])
