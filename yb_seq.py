import oracledb, statistics as st
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
for tbl in ('dostex.wytm', 'dostex.wprdtm'):
    cur.execute(f"select trans_no, tno, tt, td, feedd from {tbl} order by trans_no")
    R = cur.fetchall()
    print("==", tbl, len(R))
    for i, r in enumerate(R):
        nb = [x[3] for x in R[max(0, i-6):i] + R[i+1:i+7]]
        med = sorted(nb)[len(nb)//2]
        d = (r[3] - med).days
        if abs(d) > 25:
            print(r[0], r[1], r[2], r[3].date(), 'neighbours median', med.date(), 'diff', d, 'feedd', r[4])
cur.execute("""select m.tt, m.trans_no, d.sr_no, m.td, l.lname, d.ref_tno, d.ref_sr, d.ref_iss_id, d.bstatus from dostex.wprdtm m, dostex.wprdtd d, dostex.looms l
 where m.trans_no=d.trans_no and d.lcode=l.lcode(+) and m.tt<>'PRODUCTION' and ((m.trans_no in (163,921,1771,46,880,80)) or d.ref_iss_id in (163,921,1771,46))""")
for r in cur.fetchall(): print(r)
