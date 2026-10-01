import csv,hashlib,json,math,statistics
from datetime import datetime,timezone
from pathlib import Path
SYMBOLS=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
ROOT=Path("data/cache/binance/futures_um/deep_history_2019"); OUT=Path("artifacts/HARMONY-DISCOVERY-BATCH-010")
END="2025-10-31"; DISCOVERY_END="2024-05-21"; OOS_START="2024-05-22"; FEE=.0006; SLIP=.0005

def parse_zip(path):
 import zipfile
 with zipfile.ZipFile(path) as z:
  ns=[n for n in z.namelist() if n.lower().endswith(".csv")]
  if len(ns)!=1: raise RuntimeError("archive shape")
  return list(csv.reader(z.open(ns[0]).read().decode().splitlines()))

def load():
 px={s:{} for s in SYMBOLS}; funding={s:{} for s in SYMBOLS}
 for s in SYMBOLS:
  for p in sorted((ROOT/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
   for row in parse_zip(p):
    if row and row[0].isdigit():
     d=datetime.fromtimestamp(int(row[0])/1000,timezone.utc).date().isoformat()
     if d<=END: px[s][d]=float(row[4])
  fp=ROOT/"funding_gateway"/f"{s}-2019-2025-10.json"
  for row in json.loads(fp.read_text()):
   d=datetime.fromtimestamp(int(row["fundingTime"])/1000,timezone.utc).date().isoformat()
   if d<=END: funding[s].setdefault(d,[]).append(float(row["fundingRate"]))
 dates=sorted(set.intersection(*(set(px[s]) for s in SYMBOLS)))
 if (dates[0],dates[-1],len(dates))!=("2020-07-10",END,1935): raise RuntimeError("unexpected panel")
 return dates,px,funding

def rets(dates,px):
 return {s:{d:(0 if i==0 else px[s][d]/px[s][dates[i-1]]-1) for i,d in enumerate(dates)} for s in SYMBOLS}

def month_key(d): return d[:7]
def kurt(v):
 n=len(v); m=sum(v)/n; m2=sum((x-m)**2 for x in v)/n
 if m2<=0: return 0.0
 m4=sum((x-m)**4 for x in v)/n
 return m4/(m2*m2)

def weights(scores):
 if len(scores)!=len(SYMBOLS) or set(s for _,s in scores)!=set(SYMBOLS): raise RuntimeError("ranking universe")
 o=sorted(scores,key=lambda x:(x[0],x[1])); w={s:0 for s in SYMBOLS}
 for _,s in o[:3]: w[s]=-1/6
 for _,s in o[-3:]: w[s]=1/6
 if abs(sum(abs(w[s]) for s in SYMBOLS)-1)>1e-12: raise RuntimeError("gross")
 return w

def targets(dates,r):
 out={}
 for i,d in enumerate(dates):
  if i<60 or (i>0 and month_key(dates[i-1])==month_key(d)): continue
  out[d]=weights([(kurt([r[s][dates[j]] for j in range(i-60,i)]),s) for s in SYMBOLS])
 return out

def simulate(dates,px,funding,tar,mult=1,end_date=None):
 endi=len(dates)-1 if end_date is None else max(i for i,d in enumerate(dates) if d<=end_date)
 eq=1.; prev={s:0 for s in SYMBOLS}; curve=[]; turn=0; rebs=0; fpnl=0
 for i in range(endi+1):
  d=dates[i]
  for s in SYMBOLS:
   for rate in funding[s].get(d,[]): pnl=-prev[s]*rate; eq*=1+pnl; fpnl+=pnl
  if i:
   pd=dates[i-1]; eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in SYMBOLS)
  if d in tar:
   t=tar[d]; delta=sum(abs(t[s]-prev[s]) for s in SYMBOLS); eq*=max(0,1-(FEE+SLIP)*mult*delta); turn+=delta/2; rebs+=1; prev=t
  curve.append(eq)
 liq=sum(abs(v) for v in prev.values()); eq*=max(0,1-(FEE+SLIP)*mult*liq); curve[-1]=eq
 return curve,turn,rebs,fpnl

def metrics(curve):
 rr=[curve[i]/curve[i-1]-1 for i in range(1,len(curve))]; sd=statistics.stdev(rr) if len(rr)>1 else 0
 sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0; pk=curve[0]; dd=0
 for x in curve: pk=max(pk,x); dd=min(dd,x/pk-1)
 yrs=max((len(curve)-1)/365.25,1e-12)
 return {"final_equity":curve[-1],"cumulative_return":curve[-1]-1,"cagr":curve[-1]**(1/yrs)-1,"sharpe":sh,"max_drawdown":dd,"observations":len(curve)}

def seg(dates,curve,start):
 idx=[i for i,d in enumerate(dates) if d>=start]; base=curve[idx[0]-1] if idx[0] else 1
 m=metrics([1]+[curve[i]/base for i in idx]); m.update(start=dates[idx[0]],end=dates[idx[-1]]); return m

def halves(dates,curve,start):
 idx=[i for i,d in enumerate(dates) if d>=start]; mid=len(idx)//2
 def one(xs):
  base=curve[xs[0]-1] if xs[0] else 1; m=metrics([1]+[curve[i]/base for i in xs]); m.update(start=dates[xs[0]],end=dates[xs[-1]]); return m
 return {"first_half":one(idx[:mid]),"second_half":one(idx[mid:])}

def main():
 OUT.mkdir(parents=True,exist_ok=True); dates,px,funding=load(); r=rets(dates,px); tar=targets(dates,r)
 cheap={}
 for mult in (1,2):
  curve,turn,rebs,fpnl=simulate(dates,px,funding,tar,mult,DISCOVERY_END)
  cheap[f"{mult:.1f}x"]={"discovery":seg(dates,curve,"2020-07-10"),"turnover":turn,"rebalance_count":rebs,"funding_pnl_sum":fpnl}
 passed=cheap["1.0x"]["discovery"]["cumulative_return"]>0 and cheap["1.0x"]["discovery"]["sharpe"]>0 and cheap["1.0x"]["rebalance_count"]>=20
 deep={}
 if passed:
  runs={}
  for mult in (1,2):
   curve,turn,rebs,fpnl=simulate(dates,px,funding,tar,mult)
   runs[f"{mult:.1f}x"]={"full":metrics(curve),"oos":seg(dates,curve,OOS_START),"oos_halves":halves(dates,curve,OOS_START),"turnover":turn,"rebalance_count":rebs,"funding_pnl_sum":fpnl}
  deep={"HARMONY-FIN-0056":{"runs":runs}}
 manifest={"batch_id":"HARMONY-DISCOVERY-BATCH-010","cache_key":"harmony-binance-um-deep-history-2019-2025-10-v1-36777989764","panel":{"start":dates[0],"end":dates[-1],"rows":len(dates),"symbols":SYMBOLS},"discovery_end":DISCOVERY_END,"oos_start":OOS_START,"candidate":"HARMONY-FIN-0056","deep_capacity":1,"oos_computed_only_if_cheap_gate_passes":True,"no_parameter_search":True,"holdout_access":False}
 mr=(json.dumps(manifest,sort_keys=True,indent=2)+"\n").encode(); (OUT/"input-manifest.json").write_bytes(mr)
 payload={"batch_id":"HARMONY-DISCOVERY-BATCH-010","input_manifest_sha256":hashlib.sha256(mr).hexdigest(),"cheap_screen":{"HARMONY-FIN-0056":{"passed_gate":passed,"runs":cheap}},"selected_for_deep":["HARMONY-FIN-0056"] if passed else [],"deep_results":deep,"integrity":{"holdout_access":False,"parameter_search":False,"universe_search":False,"direction_search":False,"candidate_mutation":False,"oos_computed_for_nonselected":False}}
 raw=(json.dumps(payload,sort_keys=True,indent=2)+"\n").encode(); sha=hashlib.sha256(raw).hexdigest()
 (OUT/"HARMONY-DISCOVERY-BATCH-010-RESULT.json").write_bytes(raw)
 (OUT/"HARMONY-DISCOVERY-BATCH-010-SUMMARY.json").write_text(json.dumps({"batch_id":payload["batch_id"],"passed_cheap":[ "HARMONY-FIN-0056" ] if passed else [],"selected_for_deep":payload["selected_for_deep"],"result_sha256":sha,"discovery_sharpe_2x_cost":cheap["2.0x"]["discovery"]["sharpe"]},indent=2,sort_keys=True)+"\n")
 print(json.dumps({"passed":passed,"selected":payload["selected_for_deep"],"result_sha256":sha},indent=2))
if __name__=="__main__": main()
