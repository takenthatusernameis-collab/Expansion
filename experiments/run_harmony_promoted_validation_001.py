import csv, hashlib, io, json, math, statistics, zipfile
from datetime import datetime, timezone
from pathlib import Path

S=["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
FEE=0.0006; SLIP=0.0005; OOS_START="2024-05-22"; END="2025-10-31"
OUT=Path("artifacts/HARMONY-PROMOTED-VALIDATION-001")
ROOT12=Path("data/cache/binance/futures_um/monthly")
ROOT24=Path("data/cache/binance/futures_um/deep_history_2019")

def day(ms): return datetime.fromtimestamp(ms/1000,timezone.utc).date().isoformat()
def rows(path):
    with zipfile.ZipFile(path) as z:
        n=[x for x in z.namelist() if x.lower().endswith(".csv")]
        if len(n)!=1: raise RuntimeError(f"unexpected archive: {path}")
        return list(csv.reader(io.StringIO(z.read(n[0]).decode("utf-8"))))

def metrics(c):
    rr=[c[i]/c[i-1]-1 for i in range(1,len(c))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0
    sh=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0
    peak=c[0]; mdd=0
    for x in c: peak=max(peak,x); mdd=min(mdd,x/peak-1)
    years=max((len(c)-1)/365.25,1e-12)
    return {"final_equity":c[-1],"cumulative_return":c[-1]-1,"cagr":c[-1]**(1/years)-1,"sharpe":sh,"max_drawdown":mdd,"observations":len(c)}

def segment(dates,eq):
    idx=[i for i,d in enumerate(dates) if d>=OOS_START]
    b=1.0 if idx[0]==0 else eq[idx[0]-1]
    c=[1.0]+[eq[i]/b for i in idx]
    x=metrics(c); x["start"]=dates[idx[0]]; x["end"]=dates[idx[-1]]
    return x

# --- FIN-0012 exact frozen signal on the exact frozen monthly cache ---
def run_0012(cost_mult=1.0,apply_funding=True):
    px={s:{} for s in S}; funding={s:{} for s in S}; hashes=[]
    for s in S:
      y,m=2021,1
      while (y,m)<=(2025,10):
        kp=ROOT12/"klines"/s/"1d"/f"{s}-1d-{y:04d}-{m:02d}.zip"
        fp=ROOT12/"fundingRate"/s/f"{s}-fundingRate-{y:04d}-{m:02d}.zip"
        for p in (kp,fp): hashes.append((str(p),hashlib.sha256(p.read_bytes()).hexdigest()))
        for r in rows(kp):
          if r and r[0].isdigit(): px[s][day(int(r[0]))]=float(r[4])
        rr=rows(fp); h={k.strip():i for i,k in enumerate(rr[0])}
        for r in rr[1:]:
          if r: funding[s].setdefault(day(int(r[h["calc_time"]])),
              []).append(float(r[h["last_funding_rate"]]))
        m+=1
        if m==13: y,m=y+1,1
    dates=sorted(set.intersection(*(set(px[s]) for s in S)))
    assert len(dates)==1760 and dates[0]=="2021-01-01" and dates[-1]==END
    split=math.floor(len(dates)*.70); oos=dates[split:]
    assert oos[0]==OOS_START and len(oos)==528

    def target(i):
      if i<21: return {s:0 for s in S}
      signal_idx=i-1; base_idx=i-21
      br=px["BTCUSDT"][dates[signal_idx]]/px["BTCUSDT"][dates[base_idx]]-1
      bd=[px["BTCUSDT"][dates[j]]/px["BTCUSDT"][dates[j-1]]-1 for j in range(i-60,i)]
      bm=sum(bd)/len(bd); bv=sum((r-bm)**2 for r in bd)/len(bd)
      scores=[]
      for s in S:
        ar=px[s][dates[signal_idx]]/px[s][dates[base_idx]]-1
        ad=[px[s][dates[j]]/px[s][dates[j-1]]-1 for j in range(i-60,i)]
        am=sum(ad)/len(ad)
        cov=sum((x-am)*(y-bm) for x,y in zip(ad,bd))/len(bd)
        b=cov/bv if bv else 0
        scores.append((ar-b*br,s))
      scores.sort(key=lambda z:(-z[0],z[1]))
      w={s:0 for s in S}
      for _,s in scores[:2]: w[s]=.25
      for _,s in scores[-2:]: w[s]=-.25
      return w

    start=split; eq=1; prev={s:0 for s in S}; curve=[]
    for i in range(start,len(dates)):
      d=dates[i]
      if apply_funding:
        for s in S:
          for r in funding[s].get(d,[]): eq*=1-prev[s]*r
      if i>start:
        pd=dates[i-1]; eq*=1+sum(prev[s]*(px[s][d]/px[s][pd]-1) for s in S)
      if (i-start)%7==0:
        tgt=target(i); delta=sum(abs(tgt[s]-prev[s]) for s in S)
        eq*=1-(FEE+SLIP)*cost_mult*delta; prev=tgt
      curve.append(eq)
    liq=sum(abs(v) for v in prev.values()); eq*=1-(FEE+SLIP)*cost_mult*liq; curve[-1]=eq
    return {"oos":segment(oos,curve),"cache_files":len(hashes)}

# --- FIN-0024 exact funding-crowding signal on deep history ---
def run_0024(cost_mult=1.0,apply_funding=True):
    close={s:{} for s in S}; fund={s:{} for s in S}
    for s in S:
      for p in sorted((ROOT24/"klines"/s/"1d").glob(f"{s}-1d-*.zip")):
        for r in rows(p):
          if r and r[0].isdigit():
            d=day(int(r[0])); close[s][d]=float(r[4])
      for r in json.loads((ROOT24/"funding_gateway"/f"{s}-2019-2025-10.json").read_text()):
        fund[s].setdefault(day(int(r["fundingTime"])),[]).append(float(r["fundingRate"]))
    dates=sorted(set.intersection(*(set(close[s]) for s in S)))
    assert dates[0]=="2020-07-10" and dates[-1]==END and len(dates)==1935
    by_date={s:sorted((d,r) for d,rs in fund[s].items() for r in rs if d in dates) for s in S}
    def wt(i):
      if i<1 or (i-1)%7: return None
      signal_day=dates[i-1]; ranked=[]
      for s in S:
        obs=[r for d,r in by_date[s] if d<signal_day][-21:]
        if len(obs)<21: return None
        ranked.append((sum(obs)/21,s))
      ranked.sort(key=lambda z:(z[0],z[1])); w={s:0 for s in S}
      for _,s in ranked[:3]: w[s]=1/6
      for _,s in ranked[-3:]: w[s]=-1/6
      return w
    curve=[]; eq=1; prev={s:0 for s in S}; split=next(i for i,d in enumerate(dates) if d==OOS_START)
    for i in range(split,len(dates)):
      d=dates[i]
      if apply_funding:
        for s in S:
          for r in fund[s].get(d,[]): eq*=1-prev[s]*r
      if i>split:
        pd=dates[i-1]; eq*=1+sum(prev[s]*(close[s][d]/close[s][pd]-1) for s in S)
      tgt=wt(i)
      if tgt is not None:
        delta=sum(abs(tgt[s]-prev[s]) for s in S); eq*=1-(FEE+SLIP)*cost_mult*delta; prev=tgt
      curve.append(eq)
    liq=sum(abs(v) for v in prev.values()); eq*=1-(FEE+SLIP)*cost_mult*liq; curve[-1]=eq
    return {"oos":segment(dates[split:],curve)}

expected={
 "HARMONY-FIN-0012":{"cumulative_return":0.4169009579470373,"sharpe":0.9706848261971941},
 "HARMONY-FIN-0024":{"cumulative_return":0.43002307869081013,"sharpe":1.3589824243083648}
}

def main():
    base12=run_0012(1,True); base24=run_0024(1,True)
    for k,b in [("HARMONY-FIN-0012",base12),("HARMONY-FIN-0024",base24)]:
        for field,val in expected[k].items():
            if abs(b["oos"][field]-val)>1e-12:
                raise RuntimeError(f"{k} baseline mismatch {field}: {b['oos'][field]} != {val}")
    result={
      "validation_id":"HARMONY-PROMOTED-VALIDATION-001",
      "baseline_reproduction":{"FIN-0012":base12,"FIN-0024":base24},
      "stress":{
        "FIN-0012":{"three_times_transaction_cost":run_0012(3,True),"funding_neutral":run_0012(1,False)},
        "FIN-0024":{"three_times_transaction_cost":run_0024(3,True),"funding_neutral":run_0024(1,False)}
      },
      "integrity":{"fixed_definitions":True,"parameter_search":False,"universe_search":False,"direction_search":False,"holdout_access":False}
    }
    OUT.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(result,sort_keys=True,indent=2).encode()+b"\n"
    (OUT/"HARMONY-PROMOTED-VALIDATION-001-RESULT.json").write_bytes(raw)
    print(json.dumps({"result_sha256":hashlib.sha256(raw).hexdigest()},indent=2))

if __name__=="__main__":
    main()
