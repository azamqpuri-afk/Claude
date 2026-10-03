import oracledb, collections
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
def q(s): cur.execute(s); return cur.fetchall()
P = collections.defaultdict(float)
for k, a in q("""select to_char(dated,'YYYY-MM')||case when to_char(fdate,'DD')='01' and to_char(tdate,'DD')<='17' then 'H1' when to_char(fdate,'DD')='16' then 'H2' else 'M' end||case when section_code=1 then 'A' else 'F' end, sum(earned_amount) from payroll.salm group by to_char(dated,'YYYY-MM')||case when to_char(fdate,'DD')='01' and to_char(tdate,'DD')<='17' then 'H1' when to_char(fdate,'DD')='16' then 'H2' else 'M' end||case when section_code=1 then 'A' else 'F' end
 union all select to_char(dated,'YYYY-MM')||case when to_char(fdate,'DD')='01' then 'H1' else 'H2' end||case when section_code=1 then 'A' else 'F' end, sum(nvl(manual_hours,0)*nvl(ot_rate,0)) from payroll.ot group by to_char(dated,'YYYY-MM')||case when to_char(fdate,'DD')='01' then 'H1' else 'H2' end||case when section_code=1 then 'A' else 'F' end"""):
    P[k] += float(a or 0)
L = collections.defaultdict(float); V = collections.defaultdict(set)
for vd, vno, ac, amt in q("select vd, vno, ac, amt from dostex.acl where vt='JV' and ac in ('400201001','450101001')"):
    k = vd.strftime('%Y-%m') + ('H1' if vd.day <= 20 else 'H2') + ('A' if ac=='450101001' else 'F')
    L[k] += float(amt); V[k].add(vno)
keys = sorted(set(P) | set(L))
print("key|payroll(earned+OT)|ledgerJV|diff|JVno")
for k in keys:
    if k < '2023-10': continue
    d = L[k]-P[k]
    if abs(d) > 500: print(f"{k}|{P[k]:.0f}|{L[k]:.0f}|{d:.0f}|{sorted(V[k])}")
print("--- production")
for r in q("""select 'OLD', to_char(dated,'YYYY'), round(sum(total_mtr)) from dostt.wv_eff_v group by to_char(dated,'YYYY')
 union all select 'NEW', to_char(dated,'YYYY'), round(sum(total_mtr)) from dostex.wv_eff_v group by to_char(dated,'YYYY') order by 2,1"""):
    print(r)
