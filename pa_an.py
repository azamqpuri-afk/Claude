import oracledb, collections, datetime as dt, re, sys
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
def q(sql):
    cur.execute(sql); return cur.fetchall()
D = dt.date
def d(x): return x.date() if x else None
out = open(sys.argv[1] if len(sys.argv)>1 else 'pa_an_out.txt','w',encoding='utf8')
def P(*a): print(*a, file=out)

data = {}
for S in ('PAYROLL','DOST_PAYROLL'):
    s = S[0]
    emp = {r[0]: dict(name=r[1], father=r[2], nic=re.sub(r'\D','',r[3] or ''), doj=d(r[4]), quit=r[5], sec=r[6], desg=r[7], feedd=d(r[8]), usern=r[9], sal=r[10], status=r[11])
           for r in q(f"select emp_code, emp_name, father_name, nic_no, doj, quit, sec_code, desg_code, feedd, usern, salary, emp_status from {S}.EMP")}
    salm = [dict(e=r[0], dated=d(r[1]), wd=r[2] or 0, f=d(r[3]), t=d(r[4]), gross=r[5] or 0, earned=r[6] or 0, sec=r[7], fin=r[8], gen=d(r[9])) for r in
            q(f"select emp_code, dated, emp_worked_days, fdate, tdate, gross_salary, earned_amount, section_code, finalized, sal_gen_date from {S}.SALM")]
    io = collections.defaultdict(dict)
    for e, dd, ty in q(f"select emp_code, dated, attendance_type from {S}.IN_OUT"):
        io[e][d(dd)] = ty
    ot = [dict(e=r[0], dated=d(r[1]), oth=r[2] or 0, mh=r[3] or 0, paid=r[4], fin=r[5], fd=r[6], u=r[7], rate=r[8] or 0) for r in
          q(f"select emp_code, dated, over_time_hours, manual_hours, paid, finalized, feeding_date, user_name, ot_rate from {S}.OT")]
    for o in ot: o['fd'] = d(o['fd'])
    data[s] = (emp, salm, io, ot)

    P(f"\n######## {S}")
    # SALM period structure
    c = collections.Counter((r['f'].day if r['f'] else None, r['t'].day if r['t'] else None) for r in salm)
    P("SALM (fdate.day, tdate.day) top:", c.most_common(8))
    P("SALM earned sum", sum(r['earned'] for r in salm), "rows earned>0", sum(1 for r in salm if r['earned']>0))
    # dup rows same emp same period
    k = collections.Counter((r['e'], r['f'], r['t']) for r in salm)
    P("SALM duplicate emp+period rows:", sum(v-1 for v in k.values() if v>1))
    # codes not in EMP
    P("SALM codes not in EMP:", sorted({r['e'] for r in salm} - emp.keys())[:30], " IN_OUT codes not in EMP:", sorted(set(io) - emp.keys())[:30])
    # attendance vs pay
    PAIDTYPES = {'P','GH','CL','SL','R','CP'}
    over = []; nopunch = []; tot_rows = 0; wd_gt_len = 0
    for r in salm:
        if not r['f'] or not r['t'] or r['dated'] < D(2017,9,1): continue
        tot_rows += 1
        days = [r['f'] + dt.timedelta(i) for i in range((r['t']-r['f']).days+1)]
        att = io.get(r['e'], {})
        cnt = collections.Counter(att.get(x) for x in days)
        pres = cnt['P']; paidlv = sum(cnt[t] for t in PAIDTYPES)
        if r['wd'] > len(days): wd_gt_len += 1
        if r['earned'] > 0 and sum(v for kk, v in cnt.items() if kk) == 0:
            nopunch.append((r['e'], r['f'], r['t'], r['wd'], r['earned']))
        elif r['wd'] > paidlv + 0.5:
            over.append((r['e'], emp.get(r['e'],{}).get('name'), r['f'], r['t'], r['wd'], pres, paidlv, dict(cnt), r['earned'], r['gross']))
    P(f"SALM rows since 2017-09 with periods: {tot_rows}; worked_days>period length: {wd_gt_len}")
    P(f"Paid (earned>0) but ZERO IN_OUT rows in period: {len(nopunch)} rows, {len({x[0] for x in nopunch})} emps, earned sum {sum(x[4] for x in nopunch):,.0f}")
    byp = collections.Counter((x[1]) for x in nopunch); P("  by period start:", sorted(byp.items())[:60])
    for x in sorted(nopunch, key=lambda x: -x[4])[:25]: P("  ", x[0], emp.get(x[0],{}).get('name'), x[1], x[2], 'wd', x[3], 'earned', x[4], 'doj', emp.get(x[0],{}).get('doj'), 'quit', emp.get(x[0],{}).get('quit'))
    P(f"worked_days > (P+paid leave/holiday marks): {len(over)} rows, emps {len({x[0] for x in over})}, excess days {sum(x[4]-x[6] for x in over):.1f}")
    for x in sorted(over, key=lambda x: -(x[4]-x[6]))[:25]: P("  ", x)
    # paid before joining / after last attendance
    before = [(r['e'], emp[r['e']]['name'], emp[r['e']]['doj'], r['f'], r['t'], r['wd'], r['earned']) for r in salm if r['e'] in emp and emp[r['e']]['doj'] and r['t'] and r['t'] < emp[r['e']]['doj'] and r['earned']>0]
    P(f"Paid for a period ending BEFORE DOJ: {len(before)} rows, earned {sum(x[6] for x in before):,.0f}")
    for x in before[:20]: P("  ", x)
    # after last present day
    after = []
    for r in salm:
        att = io.get(r['e'])
        if not att or not r['f'] or r['earned'] <= 0: continue
        lastp = max((x for x, ty in att.items() if ty == 'P'), default=None)
        if lastp and r['f'] > lastp + dt.timedelta(1):
            after.append((r['e'], emp.get(r['e'],{}).get('name'), emp.get(r['e'],{}).get('quit'), lastp, r['f'], r['t'], r['wd'], r['earned']))
    P(f"Paid for a period starting AFTER employee's last-ever 'P' day: {len(after)} rows, emps {len({x[0] for x in after})}, earned {sum(x[7] for x in after):,.0f}")
    for x in sorted(after, key=lambda x: -x[7])[:25]: P("  ", x)
    # OT without presence
    otn = [(o['e'], o['dated'], io.get(o['e'],{}).get(o['dated']), o['oth'], o['mh'], o['u'], o['fd']) for o in ot if (o['oth'] or o['mh']) and io.get(o['e'],{}).get(o['dated']) not in ('P',)]
    P(f"OT rows total {len(ot)}, with hours>0 {sum(1 for o in ot if o['oth'] or o['mh'])}; OT hours on a day NOT marked P: {len(otn)} rows, hours {sum((x[3] or 0) for x in otn):.1f}")
    P("  by attendance status that day:", collections.Counter(x[2] for x in otn).most_common())
    P("  by year:", sorted(collections.Counter(x[1].year for x in otn).items()))
    for x in otn[:15]: P("  ", x)
    bigot = sorted([o for o in ot if (o['oth'] or 0) > 8], key=lambda o: -o['oth'])
    P(f"  OT rows >8 hrs/day: {len(bigot)}", [(o['e'], o['dated'], o['oth']) for o in bigot[:12]])
    # daily attendance counts
    daily = collections.Counter(); dailygh = collections.Counter()
    for e, att in io.items():
        for x, ty in att.items():
            if ty == 'P': daily[x] += 1
            if ty == 'GH': dailygh[x] += 1
    data[s] += (daily, dailygh)
    # monthly headcount by section from SALM, and from IN_OUT
    hc = collections.defaultdict(set)
    for r in salm:
        if r['earned']>0: hc[(r['dated'].strftime('%Y-%m'), r['sec'])].add(r['e'])
    months = sorted({m for m, _ in hc}); secs = sorted({sc for _, sc in hc}, key=lambda x: (x is None, x))
    P("HEADCOUNT paid (distinct emp w/ earned>0) by month x section:", secs)
    for m in months: P("  ", m, sum(len(hc[(m, sc)]) for sc in secs), [len(hc.get((m, sc), ())) for sc in secs])
    iom = collections.defaultdict(set)
    for e, att in io.items():
        for x, ty in att.items():
            if ty == 'P': iom[x.strftime('%Y-%m')].add(e)
    P("IN_OUT distinct emps with >=1 P by month:", [(m, len(v)) for m, v in sorted(iom.items())])
    # emp master audit: DOJ vs FEEDD
    lag = [(e, v['name'], v['doj'], v['feedd'], v['usern']) for e, v in emp.items() if v['doj'] and v['feedd'] and (v['feedd'] - v['doj']).days > 60]
    P(f"EMP created >60 days after DOJ: {len(lag)}")
    P("  feedd day counts top:", collections.Counter(v['feedd'] for v in emp.values()).most_common(10))
    for x in sorted(lag, key=lambda x: -(x[3]-x[2]).days)[:15]: P("  ", x)
    bad = [(e, v['name'], v['doj']) for e, v in emp.items() if v['doj'] and (v['doj'].year < 1970 or v['doj'] > D(2026,9,26))]
    P("EMP odd DOJ:", bad, " null DOJ:", sum(1 for v in emp.values() if not v['doj']))
    # same CNIC -> overlapping pay periods across codes
    bynic = collections.defaultdict(list)
    for e, v in emp.items():
        if len(v['nic']) == 13: bynic[v['nic']].append(e)
    per = collections.defaultdict(set)
    for r in salm:
        if r['earned']>0 and r['f']: per[r['e']].add((r['f'], r['t']))
    ov = []
    for nic, codes in bynic.items():
        if len(codes) < 2: continue
        for i in range(len(codes)):
            for j in range(i+1, len(codes)):
                a, b = codes[i], codes[j]
                common = per[a] & per[b]
                # also overlapping attendance P on same day
                pa = {x for x, ty in io.get(a,{}).items() if ty=='P'}; pb = {x for x, ty in io.get(b,{}).items() if ty=='P'}
                if common or (pa & pb):
                    ov.append((nic[-4:], a, emp[a]['name'], b, emp[b]['name'], len(common), sorted(common)[:2], len(pa & pb)))
    P(f"Same-CNIC code pairs BOTH paid in same pay period or both 'P' on same day: {len(ov)}")
    for x in ov: P("  ", x)
    P("Same-CNIC groups:", sum(1 for v in bynic.values() if len(v)>1), "codes in them:", sum(len(v) for v in bynic.values() if len(v)>1))
    diffname = [(nic[-4:], [(c, emp[c]['name'], emp[c]['father']) for c in codes]) for nic, codes in bynic.items() if len(codes)>1 and len({re.sub(r'\W','',emp[c]['name'] or '').upper() for c in codes})>1]
    P("Same-CNIC groups with DIFFERENT names:", len(diffname))
    for x in diffname: P("  ", x)
    # OT audit
    lagot = [o for o in ot if o['fd'] and o['dated'] and (o['fd'] - o['dated']).days > 45]
    P(f"OT feeding_date >45 days after work date: {len(lagot)}; users", collections.Counter(o['u'] for o in ot).most_common(6))

# closed days
closed = {'Eid-ul-Adha 2017-09-01':D(2017,9,1),'Eid-ul-Adha 2017-09-02':D(2017,9,2),'Ashura 2017-10-01':D(2017,10,1),'Eid-ul-Fitr 2018-06-16':D(2018,6,16),'Eid-ul-Adha 2018-08-22':D(2018,8,22),'Ashura 2018-09-21':D(2018,9,21),
 'Eid-ul-Fitr 2019-06-05':D(2019,6,5),'Eid-ul-Adha 2019-08-12':D(2019,8,12),'Ashura 2019-09-10':D(2019,9,10),'Eid-ul-Fitr 2020-05-24':D(2020,5,24),'Eid-ul-Adha 2020-08-01':D(2020,8,1),'Ashura 2020-08-30':D(2020,8,30),
 'Eid-ul-Fitr 2021-05-13':D(2021,5,13),'Eid-ul-Adha 2021-07-21':D(2021,7,21),'Ashura 2021-08-19':D(2021,8,19),'Eid-ul-Fitr 2022-05-03':D(2022,5,3),'Eid-ul-Adha 2022-07-10':D(2022,7,10),'Ashura 2022-08-09':D(2022,8,9),
 'Eid-ul-Fitr 2023-04-22':D(2023,4,22),'Eid-ul-Adha 2023-06-29':D(2023,6,29),'Ashura 2023-07-28':D(2023,7,28),'Eid-ul-Fitr 2024-04-10':D(2024,4,10),'Eid-ul-Adha 2024-06-17':D(2024,6,17),'Ashura 2024-07-17':D(2024,7,17),
 'Eid-ul-Fitr 2025-03-31':D(2025,3,31),'Eid-ul-Adha 2025-06-07':D(2025,6,7),'Ashura 2025-07-06':D(2025,7,6),'Eid-ul-Fitr 2026-03-21':D(2026,3,21),'Eid-ul-Adha 2026-05-27':D(2026,5,27),'Ashura 2026-06-26':D(2026,6,26)}
P("\n######## CLOSED DAYS (P count / GH count that day; median P of the month)")
import statistics
for k, x in closed.items():
    for s in ('D','P'):
        daily, gh = data[s][4], data[s][5]
        mon = [v for dd, v in daily.items() if dd.year==x.year and dd.month==x.month]
        if mon: P(" ", k, s, 'P=', daily.get(x,0), 'GH=', gh.get(x,0), 'monthMedianP=', statistics.median(mon))
P("Apr-May 2020 daily P (D):", [(str(x), data['D'][4].get(x,0)) for x in sorted(k for k in data['D'][4] if D(2020,3,15)<=k<=D(2020,5,31))])
for s in ('D','P'):
    daily = data[s][4]
    P(s, "Sundays with P>0 count:", sum(1 for x in daily if x.weekday()==6), " total days:", len(daily))
    P(s, "Lowest-attendance days (non-zero):", sorted(daily.items(), key=lambda kv: kv[1])[:15])
    P(s, "GH days:", sorted((str(x), v) for x, v in data[s][5].items() if v>=10))
out.close()
