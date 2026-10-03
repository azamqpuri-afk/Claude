import oracledb, collections
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
# Salary expense JVs: every JV line on expense accounts, both ERPs
q = """
select 'OLD' e, vd, vt, vno, ac, substr(naration,1,90) nar, amt from dostt.acl
 where vt='JV' and ac in ('751100001','851050001','751100002','751100003','751100004')
union all
select 'NEW', vd, vt, vno, ac, substr(naration,1,90), amt from dostex.acl
 where vt='JV' and ac in ('400201001','450101001','450101002','400202001','400202002','400202003')
order by 2,4,5"""
cur.execute(q)
out = open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\pl_jv_out.txt", "w")
for r in cur:
    out.write(" | ".join(str(x) if not hasattr(x, 'strftime') else x.strftime('%Y-%m-%d') for x in r) + "\n")
out.close()
