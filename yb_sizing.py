import oracledb, re, csv, statistics as st, sys
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
SQL = """
select m.trans_no, m.tno, m.td, m.gp_no, m.tt, m.ac1, a.an sizer, c.cont_no, y.yarn_desc, y.count, d.sr_no,
 d.ends, d.set_length, d.yarn_wt, d.sized_kg, d.rate, d.waste, d.gully, d.bags, d.cones, m.tdesc, m.feedd,
 (select count(*) from dostex.wybeams b where b.ref_sr=d.sr_no and b.trans_no=d.trans_no) nbeams,
 (select sum(b.blength*b.bwidth) from dostex.wybeams b where b.ref_sr=d.sr_no and b.trans_no=d.trans_no) blen,
 (select sum(case when w.amt>0 and w.ac='400701002' then w.amt end) from dostex.weaving_t w where w.vt=m.tt and w.vno=m.tno and w.vd=m.td and w.sr_no=d.sr_no) chg,
 (select max(w.qty) from dostex.weaving_t w where w.vt=m.tt and w.vno=m.tno and w.vd=m.td and w.sr_no=d.sr_no) wqty,
 (select max(w.rate) from dostex.weaving_t w where w.vt=m.tt and w.vno=m.tno and w.vd=m.td and w.sr_no=d.sr_no) wrate
from dostex.wytm m, dostex.wytd d, dostex.ach a, dostex.contm c, dostex.yarn y
where m.trans_no=d.trans_no and m.tt in ('IGP_SIZED_BEAMS','OPN_SIZED_BEAMS') and m.ac1=a.ac(+) and d.cont_id=c.cont_id(+) and d.ycode=y.ycode(+)
order by m.td, m.trans_no, d.sr_no"""
cur.execute(SQL)
cols = [x[0].lower() for x in cur.description]
rows = [dict(zip(cols, r)) for r in cur.fetchall()]
def ne(desc, cnt):
    if not desc: return cnt
    m = re.match(r"\s*(\d+)\s*/\s*(\d+)", desc)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b in (2, 3) and 'DNR' not in desc: return a / b
        return a
    return cnt
out = []
for r in rows:
    n = ne(r['yarn_desc'], r['count'])
    theo = r['ends'] * r['set_length'] / (1693.3 * n) if (r['ends'] and r['set_length'] and n) else None
    m = re.search(r"([\d.]+)\s*KG", r['tdesc'] or '')
    desc_kg = float(m.group(1)) if m and m.group(1).replace('.', '').isdigit() else None
    mm = re.search(r"(\d+)\s*MTR", r['tdesc'] or '')
    r.update(ne=n, theo=theo, desc_kg=desc_kg, desc_mtr=int(mm.group(1)) if mm else None,
             wt_theo=(r['yarn_wt'] / theo if theo and r['yarn_wt'] else None),
             sk_wt=(r['sized_kg'] / r['yarn_wt'] if r['sized_kg'] and r['yarn_wt'] else None),
             blen_set=(r['blen'] / r['set_length'] if r['blen'] and r['set_length'] else None),
             chg_per_kg=(r['chg'] / r['yarn_wt'] if r['chg'] and r['yarn_wt'] else None))
    out.append(r)
keys = list(out[0].keys())
with open(sys.argv[1], 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(out)
print(len(out))
