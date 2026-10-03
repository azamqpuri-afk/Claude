import oracledb, collections, datetime as dt, re, sys
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
def q(sql):
    cur.execute(sql); return cur.fetchall()
D = dt.date
def d(x): return x.date() if isinstance(x, dt.datetime) else x
out = open('pa_an2_out.txt','w',encoding='utf8')
def P(*a): print(*a, file=out)

for S in ('PAYROLL','DOST_PAYROLL'):
    s = S[0]
    P(f"\n######## {S}")
    desg = dict(q(f"select desg_code, desg_name from {S}.DESG"))
    emp = {r[0]: dict(name=r[1], father=r[2], nic=re.sub(r'\D','',r[3] or ''), doj=d(r[4]), quit=r[5], sec=r[6], desg=desg.get(r[7]), sal=r[8], feedd=d(r[9]), mob=re.sub(r'\D','',r[10] or '')[-4:])
           for r in q(f"select emp_code, emp_name, father_name, nic_no, doj, quit, sec_code, desg_code, salary, feedd, mobile from {S}.EMP")}
    salm = [dict(e=r[0], dated=d(r[1]), wd=r[2] or 0, f=d(r[3]), t=d(r[4]), gross=r[5] or 0, earned=r[6] or 0, sec=r[7], fin=r[8], gen=d(r[9])) for r in
            q(f"select emp_code, dated, emp_worked_days, fdate, tdate, gross_salary, earned_amount, section_code, finalized, sal_gen_date from {S}.SALM")]
    io = collections.defaultdict(dict)
    for e, dd, ty in q(f"select emp_code, dated, attendance_type from {S}.IN_OUT"): io[e][d(dd)] = ty
    # 1. concurrent same-CNIC pairs detail
    bynic = collections.defaultdict(list)
    for e, v in emp.items():
        if len(v['nic']) == 13: bynic[v['nic']].append(e)
    earnedper = collections.defaultdict(dict)
    for r in salm:
        if r['earned']>0: earnedper[r['e']][(r['f'], r['t'])] = r['earned']
    P("1. Same-CNIC pairs with >=5 same-day 'P' marks (both codes marked present same day):")
    for nic, codes in bynic.items():
        for i in range(len(codes)):
            for j in range(i+1, len(codes)):
                a, b = sorted((codes[i], codes[j]))
                pa = {x for x, ty in io.get(a,{}).items() if ty=='P'}; pb = {x for x, ty in io.get(b,{}).items() if ty=='P'}
                both = pa & pb
                if len(both) >= 5:
                    common = set(earnedper[a]) & set(earnedper[b])
                    amt = sum(earnedper[a][k] + earnedper[b][k] for k in common)
                    A, B = emp[a], emp[b]
                    P(f"  nic..{nic[-4:]} | {a} {A['name']} s/o {A['father']} {A['desg']} sec{A['sec']} sal{A['sal']} doj{A['doj']} quit{A['quit']} mob..{A['mob']} P-days {len(pa)} | {b} {B['name']} s/o {B['father']} {B['desg']} sec{B['sec']} sal{B['sal']} doj{B['doj']} quit{B['quit']} mob..{B['mob']} P-days {len(pb)} | same-day P {len(both)} {min(both)}..{max(both)} | co-paid periods {len(common)} combined earned {amt:,.0f}")
    # 3. paid before DOJ
    before = collections.defaultdict(list)
    for r in salm:
        v = emp.get(r['e'])
        if v and v['doj'] and r['t'] and r['t'] < v['doj'] and r['earned']>0: before[r['e']].append(r)
    P(f"3. Paid before DOJ: emps {len(before)}, rows {sum(len(v) for v in before.values())}, earned {sum(x['earned'] for v in before.values() for x in v):,.0f}")
    for e, rows in sorted(before.items(), key=lambda kv: -sum(x['earned'] for x in kv[1]))[:12]:
        allp = sorted(r['f'] for r in salm if r['e']==e)
        P(f"   {e} {emp[e]['name']} doj {emp[e]['doj']} feedd {emp[e]['feedd']} | pre-DOJ rows {len(rows)} earned {sum(x['earned'] for x in rows):,.0f} | first pay {allp[0]} last pay {allp[-1]} total rows {len(allp)}")
    # 5. master: flagged active but not paid recently
    lastpay = {}
    for r in salm:
        if r['earned']>0: lastpay[r['e']] = max(lastpay.get(r['e'], D(1900,1,1)), r['t'] or r['dated'])
    endd = max(lastpay.values())
    active_not_paid = [e for e, v in emp.items() if v['quit'] != 'Y' and lastpay.get(e, D(1900,1,1)) < endd - dt.timedelta(90)]
    never = [e for e in emp if e not in lastpay]
    P(f"5. EMP rows not QUIT='Y' but no pay in last 90 days of schema (end {endd}): {len(active_not_paid)} of {sum(1 for v in emp.values() if v['quit']!='Y')}; EMP codes never paid: {len(never)}", [(e, emp[e]['name'], emp[e]['doj'], len([1 for t in io.get(e,{}).values() if t=='P'])) for e in never][:40])
    # quit=Y but paid in last period
    P("   QUIT='Y' but paid in the final 60 days:", [(e, emp[e]['name'], lastpay[e]) for e, v in emp.items() if v['quit']=='Y' and e in lastpay and lastpay[e] >= endd - dt.timedelta(60)][:30])
    # 8. bad CNIC among paid
    badpaid = [e for e in lastpay if len(emp[e]['nic']) != 13]
    P(f"8. Paid employees with missing/invalid CNIC: {len(badpaid)} of {len(lastpay)} paid; earned {sum(r['earned'] for r in salm if r['e'] in set(badpaid)):,.0f} of {sum(r['earned'] for r in salm):,.0f}")
    by_y = collections.Counter(); tot_y = collections.Counter()
    bp = set(badpaid)
    for r in salm:
        if r['earned']>0:
            tot_y[r['dated'].year] += 1
            if r['e'] in bp: by_y[r['dated'].year] += 1
    P("   rows by year (bad CNIC / all):", sorted((y, by_y[y], tot_y[y]) for y in tot_y))
    # 9. flat attendance: employees with 0 'A' in >=12 consecutive months
    P("9. Emps with >=300 marked days and zero A/leave (every day P/R/GH):", [(e, emp[e]['name'], len(att)) for e, att in io.items() if len(att)>=300 and not any(t in ('A','CL','SL','CP') for t in att.values())][:30])
    # 10. worked-day totals of apr-may 2026 cohorts
    if s == 'P':
        coh = collections.defaultdict(list)
        for e, v in emp.items():
            if v['feedd'] and D(2026,3,25) <= v['feedd'] <= D(2026,6,10): coh[v['feedd']].append(e)
        P("10. Mar-Jun 2026 new EMP records by feed date: (feedd, n, secs, desgs, median P days, still paid Sep-2026, total earned)")
        for fd in sorted(coh):
            es = coh[fd]
            pdays = sorted(len([1 for t in io.get(e,{}).values() if t=='P']) for e in es)
            P("   ", fd, len(es), collections.Counter(emp[e]['sec'] for e in es).most_common(), collections.Counter(emp[e]['desg'] for e in es).most_common(4), 'dojs', min(emp[e]['doj'] for e in es), max(emp[e]['doj'] for e in es), 'medP', pdays[len(pdays)//2], 'still', sum(1 for e in es if lastpay.get(e, D(1900,1,1)) >= D(2026,9,1)), 'earned', sum(r['earned'] for r in salm if r['e'] in set(es)))
    # 6. daily P around specific days
    daily = collections.Counter(); gh = collections.Counter()
    for e, att in io.items():
        for x, t in att.items():
            if t == 'P': daily[x] += 1
            if t == 'GH': gh[x] += 1
    for a, b in [(D(2019,6,1), D(2019,6,10)), (D(2019,8,9), D(2019,8,16)), (D(2025,3,28), D(2025,4,4)), (D(2026,3,17), D(2026,3,25)), (D(2026,5,24), D(2026,5,31)), (D(2017,8,30), D(2017,9,5))]:
        P("6. daily P/GH", [(str(a + dt.timedelta(i))[5:], daily.get(a + dt.timedelta(i), 0), gh.get(a + dt.timedelta(i), 0)) for i in range((b-a).days+1)])
    # attendance type mix per year
    mix = collections.defaultdict(collections.Counter)
    for e, att in io.items():
        for x, t in att.items(): mix[x.year][t] += 1
    P("7. attendance status mix by year:", {y: dict(mix[y]) for y in sorted(mix)})

# OT detail
for S in ('PAYROLL','DOST_PAYROLL'):
    io = collections.defaultdict(dict)
    for e, dd, ty in q(f"select emp_code, dated, attendance_type from {S}.IN_OUT"): io[e][d(dd)] = ty
    rows = q(f"select emp_code, dated, nvl(over_time_hours,0)+nvl(manual_hours,0), ot_rate, salary, paid, finalized, extra_hours, ot_type_x from (select o.*, {'ot_type' if S=='PAYROLL' else 'null'} ot_type_x from {S}.OT o)")
    agg = collections.defaultdict(lambda: [0, 0.0, 0.0]); emps = collections.Counter(); yr = collections.defaultdict(lambda: [0, 0.0, 0.0])
    for e, dd, h, rate, sal, paid, fin, ex, ot in rows:
        st = io.get(e, {}).get(d(dd))
        k = st if st else 'NONE'
        v = h * (rate or 0)
        agg[k][0] += 1; agg[k][1] += h; agg[k][2] += v
        if st in ('A', None):
            emps[e] += h; yr[d(dd).year][0] += 1; yr[d(dd).year][1] += h; yr[d(dd).year][2] += v
    P(f"\nOT {S}: by attendance status that day -> rows, hours, hours*ot_rate:", {k: (a, round(b), round(c)) for k, (a, b, c) in agg.items()})
    P("   OT on A/no-record days by year:", {y: (a, round(b), round(c)) for y, (a, b, c) in sorted(yr.items())})
    P("   top emps (hours) OT on A/no-record days:", emps.most_common(10))
    P("   ot_rate sample stats:", q(f"select min(ot_rate), max(ot_rate), round(avg(ot_rate),1), count(ot_rate), min(nvl(over_time_hours,0)+nvl(manual_hours,0)), max(nvl(over_time_hours,0)+nvl(manual_hours,0)) from {S}.OT"))
out.close()
