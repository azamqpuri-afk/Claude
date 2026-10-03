import oracledb, statistics as st
from collections import defaultdict
oracledb.init_oracle_client(lib_dir=r"D:\ORACLE_DB\product\11.2.0\dbhome_1\BIN")
con = oracledb.connect(user="dostex", password="B", dsn="ORCL"); cur = con.cursor()
def q(s, **k):
    cur.execute(s, **k); c=[x[0] for x in cur.description]; return [dict(zip(c,r)) for r in cur]
# production by year old
for r in q("""select to_char(td,'YYYY') yr, round(sum(nvl(a,0)+nvl(b,0)+nvl(c,0)+nvl(cp,0))) prod, count(distinct lcode) looms, round(avg(pick)) pick from dostt.wprdtd where tt='PRODUCTION' group by to_char(td,'YYYY') order by 1"""): print(r)
# set uniqueness
print(q("""select count(*) n, count(distinct setno) ds, count(distinct setno||'-'||bcode) dsb from dostt.wybeams"""))
print(q("""select count(*) from (select setno from dostt.wybeams group by setno having count(distinct td)>1)"""))
wb=q("""select b.setno, b.bcode, b.blength, b.td, b.tno, b.tt, h.an party from dostt.wybeams b left join dostt.wytm m on m.tno=b.tno and m.tt=b.tt and m.td=b.td left join dostt.ach h on h.ac=m.ac1""")
W={}
for b in wb: W.setdefault((str(b['SETNO']),str(b['BCODE'])),[]).append(b)
print("wybeams keys",len(W),"dup keys",sum(1 for v in W.values() if len(v)>1))
ev=q("""select tt, td, tno, lcode, set_no, beam_code, blength, bwidth, nvl(a,0)+nvl(b,0)+nvl(c,0)+nvl(cp,0) mtr from dostt.wprdtd where tt in ('BEAMS_TO_LOOM','PRODUCTION','BEAMS_TRF')""")
B=defaultdict(lambda: {'mount':[], 'trf':[], 'prod':0,'pfirst':None,'plast':None,'looms':set()})
for e in ev:
    k=(str(e['SET_NO']),str(e['BEAM_CODE'])); b=B[k]
    if e['TT']=='BEAMS_TO_LOOM': b['mount'].append(e)
    elif e['TT']=='BEAMS_TRF': b['trf'].append(e)
    else:
        b['prod']+=e['MTR']; b['looms'].add(e['LCODE']); b['pfirst']=min(b['pfirst'] or e['TD'],e['TD']); b['plast']=max(b['plast'] or e['TD'],e['TD'])
nomount_prod=[(k,v) for k,v in B.items() if not v['mount'] and v['prod']>0]
print("prod keys",sum(1 for v in B.values() if v['prod']>0),"prod without BEAMS_TO_LOOM",len(nomount_prod),"meters",round(sum(v['prod'] for k,v in nomount_prod)))
nowyb=[(k,v) for k,v in B.items() if k not in W and v['prod']>0]
print("prod on set/beam not in WYBEAMS",len(nowyb),round(sum(v['prod'] for k,v in nowyb)))
mounted_noprod=[(k,v) for k,v in B.items() if v['mount'] and v['prod']==0]
print("mounted with zero prod", len(mounted_noprod))
for k,v in sorted(mounted_noprod,key=lambda x:x[1]['mount'][0]['TD'])[:60]: print("  ",k,v['mount'][0]['TD'].date(),'loom',v['mount'][0]['LCODE'],'len',v['mount'][0]['BLENGTH'],'w',v['mount'][0]['BWIDTH'])
bal=[]; out=[]
for k,v in B.items():
    if not v['mount'] or v['prod']==0: continue
    al=max((m['BLENGTH'] or 0)*(m['BWIDTH'] or 1) for m in v['mount'])
    if not al: continue
    p=(al-v['prod'])/al*100; bal.append(p); out.append((p,k,al,v))
bal.sort(); n=len(bal)
print("old beams n",n,"median bal%",round(st.median(bal),2),"p10",round(bal[n//10],2),"p90",round(bal[9*n//10],2),"overrun<-10%",sum(1 for x in bal if x<-10),"short>30%",sum(1 for x in bal if x>30))
print("## worst overruns")
for p,k,al,v in sorted(out)[:25]: print(round(p,1),k,'al',al,'prod',round(v['prod']),'mount',v['mount'][0]['TD'].date(),'looms',v['looms'],'pdates',v['pfirst'].date(),v['plast'].date(),'nmount',len(v['mount']))
print("## worst shortfalls (excluding last 0)")
for p,k,al,v in sorted(out,reverse=True)[:25]: print(round(p,1),k,'al',al,'prod',round(v['prod']),'mount',v['mount'][0]['TD'].date(),'looms',v['looms'],'pdates',v['pfirst'].date(),v['plast'].date())
# overrun by year
yr=defaultdict(lambda:[0,0,0])
for p,k,al,v in out:
    y=v['mount'][0]['TD'].year; yr[y][0]+=1; yr[y][1]+= (p<-10); yr[y][2]+=(p>30)
print("by mount year: n, overrun<-10%, short>30%", dict(yr))
# mounted length vs wybeams length
diff=0; ex=[]
for k,v in B.items():
    if v['mount'] and k in W:
        wl=W[k][0]['BLENGTH']; ml=v['mount'][0]['BLENGTH']
        if wl and ml and abs(wl-ml)/wl>0.05: diff+=1; ex.append((k,wl,ml,v['mount'][0]['TD'].date()))
print("mount BLENGTH differs from WYBEAMS >5%:",diff, ex[:15])
