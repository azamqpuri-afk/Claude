import oracledb, pickle
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
d = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad"
def q(s):
    cur.execute(s); cols=[c[0].lower() for c in cur.description]
    return [dict(zip(cols,r)) for r in cur.fetchall()]
rows = q("""select 'OLD' e, m.vt, m.vno, m.vd, m.feedd, m.usern, m.pcn, m.remarks, d.sr_no, d.ac, h.an, d.naration, nvl(d.dr,0) dr, nvl(d.cr,0) cr
 from dostt.tm m join dostt.td d on d.vno=m.vno and d.vd=m.vd and d.vt=m.vt and d.uc=m.uc left join dostt.ach h on h.ac=d.ac""")
rows += q("""select 'NEW' e, m.vt, m.vno, m.vd, m.feedd, m.usern, m.pcn, m.remarks, d.sr_no, d.ac, h.an, d.naration, nvl(d.dr,0) dr, nvl(d.cr,0) cr
 from dostex.tm m join dostex.td d on d.trans_no=m.trans_no left join dostex.ach h on h.ac=d.ac""")
ach = q("select 'OLD' e, ac, an, lev, hc from dostt.ach union all select 'NEW', ac, an, lev, hc from dostex.ach")
pickle.dump((rows, ach), open(d+r"\fj_lines.pkl","wb"))
print(len(rows), len(ach))
for a in ach:
    if a['e']=='OLD' and len(a['ac'])<=4: print(a['ac'], a['an'])
# same codes?
old = {a['ac']:a['an'] for a in ach if a['e']=='OLD'}; new = {a['ac']:a['an'] for a in ach if a['e']=='NEW'}
same = sum(1 for k in old if k in new and (old[k] or '').strip()==(new[k] or '').strip())
print('old',len(old),'new',len(new),'codes in both',sum(1 for k in old if k in new),'same name',same)
for k in ['200105006','300306013']: print(k, old.get(k), '|', new.get(k))
