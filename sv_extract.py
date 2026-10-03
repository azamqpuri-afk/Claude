import oracledb, pickle
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
out = {}
def q(sql):
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]

out['old'] = q("""select 'OLD' erp, m.dno, m.dd, m.uc, m.ac, a.an, a.gst_no, a.ntn, m.bno, m.bd, m.tdesc, m.usern, m.feedd,
  d.ic, i.idesc, i.uom, d.qty, d.rate, d.avg_rate, d.gst_per, d.discount, d.freight, d.sr_no, m.vno, m.gst_per m_gst_per
  from dostt.dmn m join dostt.ddt d on d.dno=m.dno and d.dd=m.dd and d.dt=m.dt and d.uc=m.uc
  left join dostt.ach a on a.ac=m.ac
  left join dostt.i i on i.ic=d.ic and i.uc=d.uc
  where m.dt='SRN'""")
out['new'] = q("""select 'NEW' erp, m.dno, m.dd, m.uc, m.ac, a.an, a.gst_no, a.ntn, m.bno, m.bd, m.tdesc, m.usern, m.feedd,
  d.ic, i.idesc, i.uom, d.qty, d.rate, d.avg_rate, d.gst_per, d.discount, d.freight, d.sr_no, m.vno, m.gst_per m_gst_per, m.trans_no, m.ref_po_tno
  from dostex.dmn m join dostex.ddt d on d.trans_no=m.trans_no and d.uc=m.uc
  left join dostex.ach a on a.ac=m.ac and a.uc=m.uc
  left join dostex.i i on i.ic=d.ic and i.uc=d.uc
  where m.dt='SRN'""")
out['iss_old'] = q("""select d.ic, sum(d.qty) q, count(*) n, max(m.dd) last_dd from dostt.dmn m join dostt.ddt d on d.dno=m.dno and d.dd=m.dd and d.dt=m.dt and d.uc=m.uc where m.dt='ISS' group by d.ic""")
out['iss_new'] = q("""select d.ic, sum(d.qty) q, count(*) n, max(m.dd) last_dd from dostex.dmn m join dostex.ddt d on d.trans_no=m.trans_no and d.uc=m.uc where m.dt='ISS' group by d.ic""")
out['ret_new'] = q("""select d.ic, sum(d.qty) q from dostex.dmn m join dostex.ddt d on d.trans_no=m.trans_no and d.uc=m.uc where m.dt='RET' group by d.ic""")
out['ret_old'] = q("""select d.ic, sum(d.qty) q from dostt.dmn m join dostt.ddt d on d.dno=m.dno and d.dd=m.dd and d.dt=m.dt and d.uc=m.uc where m.dt='RET' group by d.ic""")
out['ih_new'] = q("select hc, hn from dostex.ih")
out['ih_old'] = q("select hc, hn from dostt.ih")
out['ach_new'] = q("select ac, an, gst_no, ntn, categ, closed from dostex.ach")
for k,v in out.items(): print(k, len(v))
pickle.dump(out, open(r"C:\Users\DBSERVER\AppData\Local\Temp\claude\C--Users-DBSERVER\28bb1713-1a7e-4080-a861-91cc5051d564\scratchpad\sv_data.pkl","wb"))
print(out['old'][0]); print(out['new'][0]); print(out['ih_new'][:10])
