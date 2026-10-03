import oracledb
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL"); cur = con.cursor()
for v in ['WV_BEAM_ATLOOM_V','BEAMS_STOCK_V']:
    cur.execute("select text from all_views where owner='DOSTEX' and view_name=:v",v=v)
    print('=====',v); print(cur.fetchone()[0])
for fn in ['GET_BEAM_BAL_DATE','GET_BEAM_ALENGTH','GET_BEAM_PROD']:
    cur.execute("select text from all_source where owner='DOSTEX' and name=:n order by line",n=fn)
    print('=====',fn); print(''.join(r[0] for r in cur))
cur.execute("select vno, vd, ac, amt, naration from dostex.acl where vt='IGP_SIZED_BEAMS' and rownum<6")
for r in cur: print(r)
cur.execute("select trans_no,tno,td,vno,vd,vt,vr_trans_no from dostex.wytm where tt='IGP_SIZED_BEAMS' and rownum<6")
for r in cur: print(r)
