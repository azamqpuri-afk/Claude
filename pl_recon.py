import oracledb, collections
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL")
cur = con.cursor()
def q(sql):
    cur.execute(sql); return cur.fetchall()

# ---- payroll monthly (earned) by admin/factory
pay = collections.defaultdict(lambda: [0,0,0,0,0])  # adm, fac, n_emp_rows, loan, adv
for m, grp, e, n in q("""
 select to_char(dated,'YYYY-MM'), case when section_code in (1,2,3,4) then 'A' else 'F' end, sum(earned_amount), count(*)
 from dost_payroll.salm where dated >= date '2017-01-01' group by to_char(dated,'YYYY-MM'), case when section_code in (1,2,3,4) then 'A' else 'F' end
 union all
 select to_char(dated,'YYYY-MM'), case when section_code=1 then 'A' else 'F' end, sum(earned_amount), count(*)
 from payroll.salm group by to_char(dated,'YYYY-MM'), case when section_code=1 then 'A' else 'F' end"""):
    pay[m][0 if grp=='A' else 1] += float(e or 0); pay[m][2] += n
for m, pc, a in q("""select to_char(dated,'YYYY-MM'), pay_code, sum(earned_amount) from dost_payroll.sald where dated>=date '2017-01-01' group by to_char(dated,'YYYY-MM'), pay_code
  union all select to_char(dated,'YYYY-MM'), pay_code, sum(earned_amount) from payroll.sald group by to_char(dated,'YYYY-MM'), pay_code"""):
    if pc == 5: pay[m][3] += float(a)
    elif pc in (12,13): pay[m][4] += float(a)

# ---- OT value per month (hours*rate)
ot = collections.defaultdict(float)
for m, v in q("""select to_char(dated,'YYYY-MM'), sum(nvl(over_time_hours,0)*nvl(ot_rate,0)) from dost_payroll.ot group by to_char(dated,'YYYY-MM')
  union all select to_char(dated,'YYYY-MM'), sum(nvl(over_time_hours,0)*nvl(ot_rate,0)) from payroll.ot group by to_char(dated,'YYYY-MM')"""):
    ot[m] += float(v or 0)

# ---- ledger monthly
led = collections.defaultdict(lambda: collections.defaultdict(float))
FAC = ('751100001','400201001'); ADM = ('851050001','450101001'); PES=('751100002','400202001'); EOB=('751100003','400202002')
PAYF=('351100001','200203002'); PAYA=('351100002','200203008')
for e, m, vt, ac, dr, cr in q("""
 select 'O', to_char(vd,'YYYY-MM'), vt, ac, sum(case when amt>0 then amt else 0 end), sum(case when amt<0 then -amt else 0 end) from dostt.acl
  where ac in ('751100001','851050001','751100002','751100003','351100001','351100002') group by to_char(vd,'YYYY-MM'), vt, ac
 union all
 select 'N', to_char(vd,'YYYY-MM'), vt, ac, sum(case when amt>0 then amt else 0 end), sum(case when amt<0 then -amt else 0 end) from dostex.acl
  where vt<>'OPN' and ac in ('400201001','450101001','400202001','400202002','200203002','200203008','200203004','200203005','450101002') group by to_char(vd,'YYYY-MM'), vt, ac"""):
    L = led[m]; dr=float(dr); cr=float(cr)
    if ac in FAC: L['facJV' if vt=='JV' else 'facOther'] += dr-cr
    elif ac in ADM: L['admJV' if vt=='JV' else 'admOther'] += dr-cr
    elif ac in PES: L['pessi'] += dr-cr
    elif ac in EOB: L['eobi'] += dr-cr
    elif ac in PAYF+PAYA:
        L['payCr'] += cr - (dr if vt=='JV' else 0)
        if vt in ('CP','BP'): L['payPaid'] += dr
        elif vt!='JV': L['payOth'] += dr-cr
    elif ac == '200203004': L['pessiPaid'] += dr if vt in ('CP','BP') else 0
    elif ac == '200203005': L['eobiPaid'] += dr if vt in ('CP','BP') else 0
    elif ac == '450101002': L['mps'] += dr-cr

months = sorted(set(list(led.keys()) + [m for m in pay if m >= '2014']))
print("month|pay_adm|pay_fac|pay_tot|rows|loan|adv|OTval|L_facJV|L_admJV|L_totJV|L_otherExp|diff(L_totJV-pay)|pct|payCr|paid|pessi|eobi|pessiPaid|eobiPaid|MPS")
yr = collections.defaultdict(lambda: [0.0]*10)
for m in months:
    p = pay.get(m, [0,0,0,0,0]); L = led[m]
    pt = p[0]+p[1]; lt = L['facJV']+L['admJV']
    d = lt-pt if pt else None
    pct = f"{100*d/pt:.1f}" if pt else ""
    print(f"{m}|{p[0]:.0f}|{p[1]:.0f}|{pt:.0f}|{p[2]}|{p[3]:.0f}|{p[4]:.0f}|{ot.get(m,0):.0f}|{L['facJV']:.0f}|{L['admJV']:.0f}|{lt:.0f}|{L['facOther']+L['admOther']:.0f}|{'' if d is None else f'{d:.0f}'}|{pct}|{L['payCr']:.0f}|{L['payPaid']:.0f}|{L['pessi']:.0f}|{L['eobi']:.0f}|{L['pessiPaid']:.0f}|{L['eobiPaid']:.0f}|{L['mps']:.0f}")
    Y = yr[m[:4]]
    for i, v in enumerate([pt, lt, L['facOther']+L['admOther'], L['payCr'], L['payPaid'], L['pessi'], L['eobi'], L['pessiPaid'], L['eobiPaid'], p[3]+p[4]]):
        Y[i] += v
print("\nyear|payroll_earned|ledger_JV_exp|ledger_other_exp|payable_cr_net|paid_CP_BP|pessi_exp|eobi_exp|pessi_paid|eobi_paid|loan+adv_ded")
for y in sorted(yr): print(y + "|" + "|".join(f"{v:.0f}" for v in yr[y]))
