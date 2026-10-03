import oracledb, re, statistics as st, datetime as dt
from collections import defaultdict
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL"); cur = con.cursor()
def q(s, **k):
    cur.execute(s, **k); c=[x[0] for x in cur.description]; return [dict(zip(c,r)) for r in cur]
# beams
beams = q("""select b.trans_no, b.sr_no, b.setno, b.bcode, b.blength, b.beam_id, m.tt, m.td, m.tno, h.an party, c.cont_no, c.cont_id, d.ends
 from dostex.wybeams b join dostex.wytd d on d.trans_no=b.trans_no and d.sr_no=b.ref_sr join dostex.wytm m on m.trans_no=b.trans_no
 left join dostex.ach h on h.ac=m.ac1 left join dostex.contm c on c.cont_id=d.cont_id""")
ev = q("""select m.tt, m.td, m.tno, m.trans_no, d.ref_tno, d.ref_sr, d.lcode, d.bwidth, d.bstatus, d.cont_id,
 nvl(d.a,0)+nvl(d.a2,0)+nvl(d.b,0)+nvl(d.c,0)+nvl(d.cp,0) mtr, l.lname
 from dostex.wprdtm m join dostex.wprdtd d on d.trans_no=m.trans_no left join dostex.looms l on l.lcode=d.lcode where m.tt in ('BEAMS_TO_LOOM','BEAMS_FROM_LOOM','PRODUCTION')""")
print("beams",len(beams),"events",len(ev))
B={(b['TRANS_NO'],b['SR_NO']):dict(b,mounts=[],rets=[],prod=0,pdays=set(),plast=None,pfirst=None,ploom=set(),pcont=set()) for b in beams}
orph=defaultdict(int); prod_noref=0; prod_noref_m=0
for e in ev:
    k=(e['REF_TNO'],e['REF_SR'])
    if k not in B:
        if e['TT']=='PRODUCTION': prod_noref+=1; prod_noref_m+=e['MTR']
        else: orph[e['TT']]+=1
        continue
    b=B[k]
    if e['TT']=='BEAMS_TO_LOOM': b['mounts'].append(e)
    elif e['TT']=='BEAMS_FROM_LOOM': b['rets'].append(e)
    else:
        b['prod']+=e['MTR']; b['pdays'].add(e['TD']); b['ploom'].add(e['LNAME']); b['pcont'].add(e['CONT_ID'])
        b['plast']=max(b['plast'] or e['TD'],e['TD']); b['pfirst']=min(b['pfirst'] or e['TD'],e['TD'])
print("orphan events (ref not in WYBEAMS):",dict(orph),"production rows w/o beam ref:",prod_noref,round(prod_noref_m))
today=dt.datetime(2026,9,26)
cats=defaultdict(list)
for k,b in B.items():
    nm=len(b['mounts']); nr=len(b['rets'])
    al = b['BLENGTH']*max([m['BWIDTH'] or 1 for m in b['mounts']] or [1])
    b['al']=al
    if nm==0:
        cats['never_mounted'].append(b);
        if b['prod']>0: cats['prod_without_mount'].append(b)
        continue
    if nm>1: cats['multi_mount'].append(b)
    mdate=min(m['TD'] for m in b['mounts'])
    if nr==0:
        age=(today-mdate).days
        b['age']=age
        cats['at_loom'].append(b)
        if b['prod']==0: cats['at_loom_noprod'].append(b)
        if b['plast'] and (today-b['plast']).days>30: cats['at_loom_idle30'].append(b)
    else:
        r=max(b['rets'],key=lambda x:x['TD']); b['ret']=r
        bal=(al-b['prod'])/al*100 if al else None; b['bal']=bal
        cats['ret_'+str(r['BSTATUS'])].append(b)
        if b['prod']==0: cats['returned_noprod'].append(b)
        if b['plast'] and b['plast']>r['TD']: cats['prod_after_return'].append(b)
        b['days']=(r['TD']-mdate).days
    if b['CONT_ID'] not in b['pcont'] and b['pcont']: cats['prod_other_contract'].append(b)
    if len(b['ploom'])>1: cats['prod_multi_loom'].append(b)
for c,v in cats.items(): print(c,len(v))
# balance distribution
for s in ['COMPLETE','CUT']:
    v=[b['bal'] for b in cats['ret_'+s] if b['bal'] is not None]
    v.sort(); n=len(v)
    if not n: continue
    print(s,'n',n,'median bal%',round(st.median(v),2),'p10',round(v[n//10],2),'p90',round(v[9*n//10],2),
      'overrun(<-5%)',sum(1 for x in v if x<-5),'short(>15%)',sum(1 for x in v if x>15),'>50%',sum(1 for x in v if x>50))
def show(b,extra=''):
    return f"set {b['SETNO']} beam {b['BCODE']} ({b['TRANS_NO']},{b['SR_NO']}) cont {b['CONT_NO']} rcv {b['TD'].date()} {b['PARTY']} len {b['BLENGTH']} al {b.get('al')} prod {round(b['prod'])} {extra}"
print("\n## COMPLETE beams with |balance| extreme (bal<-10% or >20%)")
for b in sorted(cats['ret_COMPLETE'],key=lambda x:x['bal']):
    if b['bal']<-10 or b['bal']>20: print(show(b,f"bal% {round(b['bal'],1)} ret {b['ret']['TD'].date()} tno {b['ret']['TNO']} loom {b['ret']['LNAME']} days {b['days']}"))
print("\n## CUT beams with bal>30%")
for b in sorted(cats['ret_CUT'],key=lambda x:-x['bal']):
    if b['bal']>30: print(show(b,f"bal% {round(b['bal'],1)} ret {b['ret']['TD'].date()} tno {b['ret']['TNO']} loom {b['ret']['LNAME']}"))
print("\n## returned with zero production"); [print(show(b,f"status {b['ret']['BSTATUS']} ret {b['ret']['TD'].date()} tno {b['ret']['TNO']} loom {b['ret']['LNAME']}")) for b in cats['returned_noprod']]
print("\n## at loom now, age>120 days or no prod or idle 30d")
for b in sorted(cats['at_loom'],key=lambda x:-x['age']):
    if b['age']>120 or b['prod']==0 or (b['plast'] and (today-b['plast']).days>30):
        print(show(b,f"mounted {min(m['TD'] for m in b['mounts']).date()} age {b['age']} loom {b['mounts'][-1]['LNAME']} lastprod {b['plast'].date() if b['plast'] else None} bal {round(b['al']-b['prod'])}"))
print("\n## never mounted (not at loom), by rcv date")
nm=cats['never_mounted']; print("count",len(nm),"total len",sum(b['BLENGTH'] for b in nm))
for b in sorted(nm,key=lambda x:x['TD']): print(show(b,b['TT']))
print("\n## prod after return"); [print(show(b,f"ret {b['ret']['TD'].date()} lastprod {b['plast'].date()}")) for b in cats['prod_after_return']]
print("\n## multi mount"); [print(show(b,' | '.join(f"{m['TD'].date()} {m['LNAME']} w{m['BWIDTH']} tno{m['TNO']}" for m in b['mounts']))) for b in cats['multi_mount']]
print("\n## prod on other contract"); [print(show(b,f"prodconts {b['pcont']}")) for b in cats['prod_other_contract']]
print("\n## prod multi loom"); [print(show(b,f"{b['ploom']}")) for b in cats['prod_multi_loom'][:40]]
