import csv, io, json, math, random, statistics, zipfile, hashlib
from datetime import datetime, timezone
from pathlib import Path

SYMBOLS = ["BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT"]
OOS_START = "2024-05-22"
END = "2025-10-31"
FEE = 0.0006
SLIP = 0.0005
WINDOW = 60
PLACEBOS = 1000
SEED = 260901

OUT = Path("artifacts/HARMONY-ALPHA-AUTOPSY-V1")
MONTHLY_ROOT = Path("data/cache/binance/futures_um/monthly")
DEEP_ROOT = Path("data/cache/binance/futures_um/deep_history_2019")

def sha256(raw):
    return hashlib.sha256(raw).hexdigest()

def months(start_year, start_month, end_year, end_month):
    out=[]
    y,m=start_year,start_month
    while (y,m)<=(end_year,end_month):
        out.append((y,m))
        m += 1
        if m==13: y,m=y+1,1
    return out

def read_zip(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError(f"CRC failure: {path}")
        names=[n for n in z.namelist() if n.lower().endswith(".csv")]
        if len(names)!=1:
            raise RuntimeError(f"unexpected archive members: {path}")
        return list(csv.reader(io.StringIO(z.read(names[0]).decode("utf-8"))))

def day(ms):
    return datetime.fromtimestamp(ms/1000, timezone.utc).date().isoformat()

def load_monthly_close(symbol):
    px={}
    for y,m in months(2021,1,2025,10):
        p=MONTHLY_ROOT/"klines"/symbol/"1d"/f"{symbol}-1d-{y:04d}-{m:02d}.zip"
        if not p.is_file(): raise FileNotFoundError(str(p))
        for r in read_zip(p):
            if r and r[0].isdigit():
                px[day(int(r[0]))]=float(r[4])
    return px

def load_deep_close(symbol):
    px={}
    for y,m in months(2019,1,2025,10):
        p=DEEP_ROOT/"klines"/symbol/"1d"/f"{symbol}-1d-{y:04d}-{m:02d}.zip"
        if not p.is_file(): continue
        for r in read_zip(p):
            if r and r[0].isdigit():
                px[day(int(r[0]))]=float(r[4])
    return px

def load_monthly_funding(symbol):
    out={}
    for y,m in months(2021,1,2025,10):
        p=MONTHLY_ROOT/"fundingRate"/symbol/f"{symbol}-fundingRate-{y:04d}-{m:02d}.zip"
        if not p.is_file(): raise FileNotFoundError(str(p))
        rr=read_zip(p)
        h={k.strip():i for i,k in enumerate(rr[0])}
        for r in rr[1:]:
            if not r: continue
            d=day(int(r[h["calc_time"]]))
            out.setdefault(d,[]).append(float(r[h["last_funding_rate"]]))
    return out

def load_deep_funding(symbol):
    p=DEEP_ROOT/"funding_gateway"/f"{symbol}-2019-2025-10.json"
    if not p.is_file(): raise FileNotFoundError(str(p))
    rows=json.loads(p.read_text())
    out={}
    for r in rows:
        d=day(int(r["fundingTime"]))
        out.setdefault(d,[]).append(float(r["fundingRate"]))
    return out

def metrics(returns):
    eq=[1.0]
    for r in returns: eq.append(eq[-1]*(1+r))
    rr=returns
    sd=statistics.stdev(rr) if len(rr)>1 and statistics.stdev(rr)>0 else 0.0
    sh=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0.0
    downside=[min(0.0,x) for x in rr]
    dsd=statistics.stdev(downside) if len(downside)>1 and statistics.stdev(downside)>0 else 0.0
    sortino=(statistics.mean(rr)/dsd)*math.sqrt(365.25) if dsd else 0.0
    peak=eq[0]; mdd=0.0
    for x in eq:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    years=max(len(rr)/365.25,1e-12)
    return {"cumulative_return":eq[-1]-1.0,"cagr":eq[-1]**(1/years)-1.0,
            "sharpe":sh,"sortino":sortino,"max_drawdown":mdd,"final_equity":eq[-1]}

def curve_metrics(curve):
    """Metrics from realized equity, including transaction costs and funding."""
    if not curve:
        raise ValueError("empty equity curve")
    rr=[curve[i]/curve[i-1]-1.0 for i in range(1,len(curve))]
    sd=statistics.stdev(rr) if len(rr)>1 and statistics.stdev(rr)>0 else 0.0
    sh=(statistics.mean(rr)/sd)*math.sqrt(365.25) if sd else 0.0
    peak=curve[0]; mdd=0.0
    for x in curve:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    years=max(len(rr)/365.25,1e-12)
    return {
        "cumulative_return":curve[-1]-1.0,
        "cagr":curve[-1]**(1/years)-1.0,
        "sharpe":sh,
        "max_drawdown":mdd,
        "final_equity":curve[-1],
        "observations":len(curve),
    }

def oos_dates(close):
    dates=sorted(set.intersection(*(set(close[s]) for s in SYMBOLS)))
    return [d for d in dates if OOS_START <= d <= END]

def daily_returns(close, dates):
    out={s:{} for s in SYMBOLS}
    for s in SYMBOLS:
        for i in range(1,len(dates)):
            d,pd=dates[i],dates[i-1]
            out[s][d]=close[s][d]/close[s][pd]-1.0
    return out

def fund_map(funding, dates):
    # returns total native funding rate paid/received on each date by symbol
    return {s:{d:sum(funding[s].get(d,[])) for d in dates} for s in SYMBOLS}

def pnl_step(prev_w, d, dprev, returns, frates):
    return sum(prev_w[s]*returns[s].get(d,0.0) for s in SYMBOLS) - sum(prev_w[s]*frates[s].get(d,0.0) for s in SYMBOLS)

def simulate(dates, close, funding, weight_fn, start_idx):
    rets=daily_returns(close,dates)
    fr=fund_map(funding,dates)
    prev={s:0.0 for s in SYMBOLS}
    eq=1.0; curve=[]; daily=[]; turnover=0.0; costs=0.0; fund_pnl=0.0
    for i in range(start_idx,len(dates)):
        d=dates[i]
        if i>start_idx:
            r=sum(prev[s]*rets[s].get(d,0.0) for s in SYMBOLS)
            f=-sum(prev[s]*fr[s].get(d,0.0) for s in SYMBOLS)
            daily_r=r+f
            fund_pnl += f
            eq *= 1.0+daily_r
            daily.append(daily_r)
        else:
            daily.append(0.0)
        target=weight_fn(i,d)
        if target is not None:
            delta=sum(abs(target.get(s,0.0)-prev[s]) for s in SYMBOLS)
            cost=(FEE+SLIP)*delta
            eq *= max(0.0,1.0-cost)
            turnover += delta/2.0
            costs += cost
            prev={s:target.get(s,0.0) for s in SYMBOLS}
        curve.append(eq)
    liq=sum(abs(v) for v in prev.values())
    lc=(FEE+SLIP)*liq
    eq*=max(0.0,1.0-lc); costs+=lc
    curve[-1]=eq
    return {"dates":dates[start_idx:],"returns":daily,"equity":curve,
            "turnover":turnover,"costs":costs,"funding_pnl":fund_pnl}

def fin12_weights(close, dates, i):
    if i < max(21,61):
        return None
    sig=i-1; base=i-21
    btc20=close["BTCUSDT"][dates[sig]]/close["BTCUSDT"][dates[base]]-1.0
    btc_daily=[close["BTCUSDT"][dates[j]]/close["BTCUSDT"][dates[j-1]]-1.0 for j in range(i-60,i)]
    bm=sum(btc_daily)/len(btc_daily)
    bv=sum((x-bm)**2 for x in btc_daily)/len(btc_daily)
    scores=[]
    for s in SYMBOLS:
        ar=close[s][dates[sig]]/close[s][dates[base]]-1.0
        ad=[close[s][dates[j]]/close[s][dates[j-1]]-1.0 for j in range(i-60,i)]
        am=sum(ad)/len(ad)
        cov=sum((x-am)*(y-bm) for x,y in zip(ad,btc_daily))/len(ad)
        beta=cov/bv if bv else 0.0
        scores.append((ar-beta*btc20,s))
    scores.sort(key=lambda z:(-z[0],z[1]))
    w={s:0.0 for s in SYMBOLS}
    for _,s in scores[:2]: w[s]=0.25
    for _,s in scores[-2:]: w[s]=-0.25
    return w

def fin24_weights(funding, dates, i):
    if i<1: return None
    d=dates[i-1]
    scores=[]
    for s in SYMBOLS:
        rows=[]
        for dd in sorted(funding[s]):
            if dd < d:
                rows.append((dd,sum(funding[s][dd])))
        rows=rows[-21:]
        if len(rows)<21: return None
        mean=sum(x[1] for x in rows)/21.0
        scores.append((mean,s))
    scores.sort(key=lambda z:(z[0],z[1]))
    w={s:0.0 for s in SYMBOLS}
    for _,s in scores[:3]: w[s]=1.0/6.0
    for _,s in scores[-3:]: w[s]=-1.0/6.0
    return w

def equal_weights(i,d):
    return {s:1.0/len(SYMBOLS) for s in SYMBOLS} if i==0 else None

def btc_weights(i,d):
    return {s:(1.0 if s=="BTCUSDT" else 0.0) for s in SYMBOLS} if i==0 else None

def rolling_attribution(strategy_returns, btc_returns, ew_returns):
    n=len(strategy_returns)
    residual=[0.0]*n; betas=[]; r2s=[]
    for i in range(n):
        if i<WINDOW:
            residual[i]=strategy_returns[i]; betas.append((0.0,0.0)); r2s.append(None); continue
        y=strategy_returns[i-WINDOW:i]
        x1=btc_returns[i-WINDOW:i]; x2=ew_returns[i-WINDOW:i]
        mx=sum(x1)/WINDOW; mz=sum(x2)/WINDOW; my=sum(y)/WINDOW
        s11=sum((a-mx)**2 for a in x1); s22=sum((a-mz)**2 for a in x2); s12=sum((a-mx)*(b-mz) for a,b in zip(x1,x2))
        s1y=sum((a-mx)*(b-my) for a,b in zip(x1,y)); s2y=sum((a-mz)*(b-my) for a,b in zip(x2,y))
        det=s11*s22-s12*s12
        if abs(det)<1e-18:
            b1=b2=0.0
        else:
            b1=(s1y*s22-s2y*s12)/det
            b2=(s2y*s11-s1y*s12)/det
        a=my-b1*mx-b2*mz
        pred=a+b1*btc_returns[i]+b2*ew_returns[i]
        residual[i]=strategy_returns[i]-pred
        ss_tot=sum((v-my)**2 for v in y)
        ss_res=sum((v-(a+b1*u+b2*v2))**2 for v,u,v2 in zip(y,x1,x2))
        r2s.append(1.0-ss_res/ss_tot if ss_tot>0 else 0.0); betas.append((b1,b2))
    return residual, betas, r2s

def percentile(actual, values):
    return 100.0*sum(v<=actual for v in values)/len(values)

def placebo(close, funding, dates, start_idx, weight_builder, gross_per_leg, n_longs, n_shorts, seed):
    rets=daily_returns(close,dates); fr=fund_map(funding,dates)
    rng=random.Random(seed); results=[]
    for _ in range(PLACEBOS):
        prev={s:0.0 for s in SYMBOLS}; eq=1.0; daily=[]; curve=[]
        for i in range(start_idx,len(dates)):
            d=dates[i]
            if i>start_idx:
                r=sum(prev[s]*rets[s].get(d,0.0) for s in SYMBOLS)
                f=-sum(prev[s]*fr[s].get(d,0.0) for s in SYMBOLS)
                rr=r+f; eq*=1+rr; daily.append(rr)
            else:
                daily.append(0.0)
            if (i-start_idx)%7==0:
                deterministic=weight_builder(i,d)
                if deterministic is not None:
                    pool=list(SYMBOLS); rng.shuffle(pool)
                    longs=pool[:n_longs]
                    remaining=[s for s in pool if s not in longs]
                    shorts=remaining[:n_shorts]
                    w={s:0.0 for s in SYMBOLS}
                    for s in longs: w[s]=gross_per_leg
                    for s in shorts: w[s]=-gross_per_leg
                    delta=sum(abs(w[s]-prev[s]) for s in SYMBOLS)
                    eq*=max(0.0,1-(FEE+SLIP)*delta)
                    prev=w
            curve.append(eq)
        eq*=max(0.0,1-(FEE+SLIP)*sum(abs(v) for v in prev.values()))
        curve[-1]=eq
        gross=metrics(daily)
        net=curve_metrics(curve)
        results.append({
            "cum":eq-1.0,
            "sharpe":net["sharpe"],
            "mdd":net["max_drawdown"],
            "net_cumulative_return":net["cumulative_return"],
            "gross_sharpe":gross["sharpe"],
            "gross_mdd":gross["max_drawdown"]
        })
    return results


def process(name, close, funding, raw_weight_builder, n_longs, n_shorts, gross_per_leg):
    dates=sorted(set.intersection(*(set(close[s]) for s in SYMBOLS)))
    dates=[d for d in dates if d<=END]
    start_idx=next(i for i,d in enumerate(dates) if d>=OOS_START)

    def candidate_weight(i,d):
        if (i-start_idx)%7!=0:
            return None
        return raw_weight_builder(close if name=="FIN-0012" else funding, dates, i)

    def benchmark_weight(i,d,kind):
        if i!=start_idx:
            return None
        if kind=="btc":
            return {s:(1.0 if s=="BTCUSDT" else 0.0) for s in SYMBOLS}
        return {s:1.0/len(SYMBOLS) for s in SYMBOLS}

    strat=simulate(dates,close,funding,candidate_weight,start_idx)
    btc=simulate(dates,close,funding,lambda i,d: benchmark_weight(i,d,"btc"),start_idx)
    ew=simulate(dates,close,funding,lambda i,d: benchmark_weight(i,d,"ew"),start_idx)

    residual,betas,r2s=rolling_attribution(strat["returns"],btc["returns"],ew["returns"])
    usable_residual=residual[WINDOW:]
    placebo_seed=SEED+(12 if name=="FIN-0012" else 24)
    placeholders=placebo(close,funding,dates,start_idx,(lambda i,d: raw_weight_builder(close if name=="FIN-0012" else funding, dates, i)),gross_per_leg,n_longs,n_shorts,placebo_seed)

    def info_ratio(a,b):
        x=[u-v for u,v in zip(a,b)]
        sd=statistics.stdev(x) if len(x)>1 else 0.0
        return (statistics.mean(x)/sd)*math.sqrt(365.25) if sd>0 else 0.0

    sm=metrics(strat["returns"]); bm=metrics(btc["returns"]); em=metrics(ew["returns"])
    sm_net=curve_metrics(strat["equity"]); bm_net=curve_metrics(btc["equity"]); em_net=curve_metrics(ew["equity"])
    result={
        "candidate":name,
        "oos":{"start":strat["dates"][0],"end":strat["dates"][-1],"observations":len(strat["dates"]),
                "full_history_start":dates[0],"full_history_end":dates[-1]},
        "raw_metrics":sm,
        "net_metrics":sm_net,
        "benchmarks":{"btc":bm,"equal_weight":em},
        "net_benchmarks":{"btc":bm_net,"equal_weight":em_net},
        "benchmark_relative":{
            "vs_btc_relative_wealth_return":(1+sm["cumulative_return"])/(1+bm["cumulative_return"])-1,
            "vs_equal_weight_relative_wealth_return":(1+sm["cumulative_return"])/(1+em["cumulative_return"])-1,
            "information_ratio_vs_btc":info_ratio(strat["returns"],btc["returns"]),
            "information_ratio_vs_equal_weight":info_ratio(strat["returns"],ew["returns"])
        },
        "net_benchmark_relative":{
            "vs_btc_relative_wealth_return":(1+sm_net["cumulative_return"])/(1+bm_net["cumulative_return"])-1,
            "vs_equal_weight_relative_wealth_return":(1+sm_net["cumulative_return"])/(1+em_net["cumulative_return"])-1,
            "information_ratio_vs_btc":info_ratio([strat["equity"][i]/strat["equity"][i-1]-1.0 for i in range(1,len(strat["equity"]))],
                                                  [btc["equity"][i]/btc["equity"][i-1]-1.0 for i in range(1,len(btc["equity"]))]),
            "information_ratio_vs_equal_weight":info_ratio([strat["equity"][i]/strat["equity"][i-1]-1.0 for i in range(1,len(strat["equity"]))],
                                                          [ew["equity"][i]/ew["equity"][i-1]-1.0 for i in range(1,len(ew["equity"]))])
        },
        "rolling_factor_metrics":metrics(usable_residual),
        "rolling_factor_metrics_layer":"gross_before_transaction_costs",
        "rolling_factor_warmup_observations":WINDOW,
        "accounting_layers":{
            "raw_metrics":"gross_return_series_before_transaction_costs",
            "net_metrics":"realized_equity_after_transaction_costs_and_funding",
            "placebo_percentiles":"realized_equity_net_of_costs_and_funding",
            "diagnostic_only":True
        },
        "rolling_beta_summary":{
            "btc_mean":statistics.mean(b[0] for b in betas[WINDOW:]),
            "btc_median":statistics.median(b[0] for b in betas[WINDOW:]),
            "equal_weight_mean":statistics.mean(b[1] for b in betas[WINDOW:]),
            "equal_weight_median":statistics.median(b[1] for b in betas[WINDOW:]),
            "r2_mean":statistics.mean(x for x in r2s[WINDOW:] if x is not None),
            "r2_median":statistics.median(x for x in r2s[WINDOW:] if x is not None)
        },
        "placebo":{
            "n":len(placeholders),
            "seed":placebo_seed,
            "actual_cumulative_percentile":percentile(sm_net["cumulative_return"],[x["cum"] for x in placeholders]),
            "actual_sharpe_percentile":percentile(sm_net["sharpe"],[x["sharpe"] for x in placeholders]),
            "actual_mdd_percentile":percentile(sm_net["max_drawdown"],[x["mdd"] for x in placeholders]),
            "cum_mean":statistics.mean(x["cum"] for x in placeholders),
            "cum_median":statistics.median(x["cum"] for x in placeholders),
            "cum_p05":sorted(x["cum"] for x in placeholders)[int(0.05*len(placeholders))],
            "cum_p95":sorted(x["cum"] for x in placeholders)[int(0.95*len(placeholders))-1]
        },
        "execution":{"turnover":strat["turnover"],"transaction_costs":strat["costs"],"funding_pnl":strat["funding_pnl"]}
    }
    curves=[]
    residual_eq=1.0
    for i,d in enumerate(strat["dates"]):
        if i>=WINDOW:
            residual_eq*=1+residual[i]
            residual_return=residual[i]
            residual_equity=residual_eq
        else:
            residual_return=""
            residual_equity=""
        curves.append({
            "date":d,
            "strategy_return":strat["returns"][i],
            "strategy_equity":strat["equity"][i],
            "btc_return":btc["returns"][i],
            "btc_equity":btc["equity"][i],
            "equal_weight_return":ew["returns"][i],
            "equal_weight_equity":ew["equity"][i],
            "rolling_btc_beta":betas[i][0],
            "rolling_equal_weight_beta":betas[i][1],
            "rolling_r2":"" if r2s[i] is None else r2s[i],
            "residual_return":residual_return,
            "residual_equity":residual_equity
        })
    return result,curves,placeholders

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    monthly={s:load_monthly_close(s) for s in SYMBOLS}
    monthly_f={s:load_monthly_funding(s) for s in SYMBOLS}
    deep={s:load_deep_close(s) for s in SYMBOLS}
    deep_f={s:load_deep_funding(s) for s in SYMBOLS}

    r12,c12,p12=process("FIN-0012",monthly,monthly_f,fin12_weights,2,2,0.25)
    r24,c24,p24=process("FIN-0024",deep,deep_f,fin24_weights,3,3,1.0/6.0)

    manifest_files=[]
    for root in [MONTHLY_ROOT,DEEP_ROOT]:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                raw=p.read_bytes()
                manifest_files.append({"path":str(p),"bytes":len(raw),"sha256":sha256(raw)})
    manifest={"generated_from_github_sha":__import__("os").environ.get("GITHUB_SHA"),"files":manifest_files,
              "oos_start":OOS_START,"oos_end":END,"placebos":PLACEBOS,"seed_base":SEED,
              "holdout_access":False,"candidate_mutation":False,"parameter_search":False}
    mb=json.dumps(manifest,sort_keys=True,indent=2).encode()+b"\\n"; (OUT/"input_manifest.json").write_bytes(mb)
    msha=sha256(mb)

    payload={"version":"1.0","input_manifest_sha256":msha,
             "method":{"factor_window":WINDOW,"placebos":PLACEBOS,"seed_base":SEED,
                       "diagnostic_only":True,"candidate_mutation":False,"holdout_access":False},
             "candidates":{"FIN-0012":r12,"FIN-0024":r24}}
    raw=json.dumps(payload,sort_keys=True,indent=2).encode()+b"\\n"; (OUT/"alpha_autopsy.json").write_bytes(raw)

    with (OUT/"equity_curves.csv").open("w",newline="") as f:
        fields=list(c12[0].keys())+["candidate"]
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
        for candidate,curves in [("FIN-0012",c12),("FIN-0024",c24)]:
            for row in curves:
                x=row.copy(); x["candidate"]=candidate; w.writerow(x)

    placebo_payload={"FIN-0012":p12,"FIN-0024":p24}
    (OUT/"placebo_summary.json").write_text(json.dumps(placebo_payload,sort_keys=True,indent=2)+"\\n")

    def classification(r):
        r2=r["rolling_beta_summary"]["r2_mean"]; rs=r["rolling_factor_metrics"]["sharpe"]
        if r2>=0.70: return "benchmark-dominant"
        if r2>=0.40: return "mixed exposure"
        return "relatively independent"
    report=[
        "# Harmony Alpha Autopsy v1","",
        "Diagnostic-only run. Frozen candidates were not modified; no holdout data were accessed.",
        "",
        "## FIN-0012",
        json.dumps(r12,indent=2,sort_keys=True),
        f"Descriptive classification: **{classification(r12)}**",
        "",
        "## FIN-0024",
        json.dumps(r24,indent=2,sort_keys=True),
        f"Descriptive classification: **{classification(r24)}**",
        "",
        "## Interpretation rule",
        "Raw metrics are gross return-series diagnostics. Net metrics are realized-equity diagnostics including transaction costs and funding and are the authoritative layer for executable performance reconciliation. Rolling residual performance remains an attribution diagnostic, not a claim of causal alpha. Placebo percentiles are descriptive null diagnostics and must not be interpreted as promotion evidence.",
    ]
    (OUT/"alpha_autopsy_report.md").write_text("\\n".join(report)+"\\n")
    print(json.dumps(payload,indent=2,sort_keys=True))

if __name__=="__main__":
    main()
