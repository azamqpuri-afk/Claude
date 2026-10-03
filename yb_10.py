import oracledb
from collections import defaultdict
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL"); cur = con.cursor()
def q(s, **k):
    cur.execute(s, **k); c=[x[0] for x in cur.description]; return [dict(zip(c,r)) for r in cur]
cur.execute("select listagg(column_name,',') within group (order by column_id) from all_tab_columns where owner='DOSTT' and table_name='YARN'"); print(cur.fetchone())
rows=q("""select m.tt, m.td, m.ac1, m.ac2, d.ic, nvl(d.yarn_wt, nvl(d.bags,0)*45.36+nvl(d.cones,0)*45.36/nullif(d.cone_pbag,0)) kg, d.sized_kg
 from dostt.wytm m join dostt.wytd d on d.tno=m.tno and d.tt=m.tt and d.td=m.td""")
sizers={r['AC1'] for r in rows if r['TT']=='IGP_SIZED_BEAMS'}
names=dict(q_ for q_ in [(r[0],r[1]) for r in (cur.execute("select ac, an from dostt.ach") or cur)])
cur.execute("select ycode, ydesc from dostt.yarn") if False else None
I=defaultdict(lambda: defaultdict(float))
for r in rows:
    k=r['IC']; kg=r['KG'] or 0; t=r['TT']
    if t=='IGP_YARN':
        I[k]['in_from_sizer' if r['AC1'] in sizers else 'in_party']+=kg
    elif t=='OGP_YARN':
        I[k]['out_to_sizer' if r['AC2'] in sizers else 'out_return']+=kg
    elif t=='IGP_SIZED_BEAMS': I[k]['beams_yarn']+=kg; I[k]['beams_sized']+=r['SIZED_KG'] or 0
    elif t=='YARN_TO_LOOMS': I[k]['weft']+=kg
    else: I[k][t]+=kg
tot=defaultdict(float)
for k,v in I.items():
    for a,b in v.items(): tot[a]+=b
print("TOTAL", {a:round(b) for a,b in tot.items()})
cur.execute("select column_name from all_tab_columns where owner='DOSTT' and table_name='YARN'")
ycols=[r[0] for r in cur]; print(ycols)
