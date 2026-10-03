import oracledb, pickle, collections
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
D = r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\\"
def q(sql):
    cur = con.cursor(); cur.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
o = q("""select 'OLD' src, vd, vt, vno, null tbl, ac, naration, amt, usern, feedd from dostt.acl
         where (ac like '501%' or ac like '30%') and length(ac)>5""")
n = q("""select 'NEW' src, vd, vt, vno, tbl, ac, naration, amt, usern, feedd from dostex.acl
         where (ac like '3003%' or ac like '2001%') and length(ac)>6""")
ho = q("select 'OLD' src, ac, an, gst_no, ntn, categ, null closed, address, ph, emp_code from dostt.ach where (ac like '501%' or ac like '30%') and length(ac)>5")
hn = q("select 'NEW' src, ac, an, gst_no, ntn, categ, closed, address, ph, emp_code from dostex.ach where (ac like '3003%' or ac like '2001%') and length(ac)>6")
oc = q("""select 'OLD' src, a.vd, a.vt, a.vno, a.ac, a.amt from dostt.acl a where exists (select 1 from dostt.acl b where b.vt=a.vt and b.vno=a.vno and b.vd=a.vd and (b.ac like '501%' or b.ac like '30%') and length(b.ac)>5)""")
nc = q("""select 'NEW' src, a.vd, a.vt, a.vno, a.tbl, a.ac, a.amt from dostex.acl a where exists (select 1 from dostex.acl b where b.vt=a.vt and b.vno=a.vno and b.vd=a.vd and (b.ac like '3003%' or b.ac like '2001%') and length(b.ac)>6)""")
allo = q("select ac, an from dostt.ach"); alln = q("select ac, an from dostex.ach")
pickle.dump(dict(led=o+n, ach=ho+hn, vouch=oc+nc, allo=allo, alln=alln), open(D+"fr_data.pkl", "wb"))
print(len(o), len(n), len(ho), len(hn), len(oc), len(nc))
print(collections.Counter((r['src'], r['vt'], r.get('tbl')) for r in o+n))
