#!/usr/bin/env python3
"""Independent SUIUSDT cfg_0602 replication; Python stdlib only, no live trading."""
import csv, hashlib, io, json, math, urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "takenthatusernameis-collab/Quantitative-Breakout-Trading-Strategy-Research-"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/research/data/candles"
START, END, HOUR, CAPITAL = 1773241920000, 1792162800000, 3600000, 10000.0
COSTS = (1.0, 2.0, 3.0)
HEADER = "timestamp,open,high,low,close,volume\n"
DATA = {
 "SUIUSDT": ("SUIUSDT_1h_4879bb0922da366d.csv", "d4c4ee162f97b0e2cb6b42bfc882a9fc826d32c7", "4879bb0922da366d5e0fc0eb8cbf7c63f7560720eb71f68ca2c8871cf7188bd5"),
 "BTCUSDT": ("BTCUSDT_1h_08c1f8eb22a04e30.csv", "21335dd40645a4391e756390102ef332e6246bd0", "08c1f8eb22a04e3007d3761f834aea30a6d7d77c2195dbfb19297ecb6507d0bc"),
}
EXPECTED = {
 1.0: (0.4390650391219526, 1.429084451931706, 0.213445771232031, 8, 0.025185984196373006, 0.012591044292082796, 16.934640731636343, 0.625),
 2.0: (0.4162061926722916, 1.3743527483503484, 0.21501848739495802, 8, 0.05000262066420802, 0.02499356856477077, 16.801558932439868, 0.375),
 3.0: (0.3936992423256208, 1.3197703787994408, 0.21658884624838043, 8, 0.07445532682336023, 0.037210354737090265, 16.66982218892358, 0.375),
}
def iso(ms):
 return datetime.fromtimestamp(ms/1000, timezone.utc).isoformat().replace("+00:00","Z")
def canonical(candles):
 return HEADER + "".join(",".join([str(int(c["timestamp"]))] + [format(float(c[k]), ".17g") for k in ("open","high","low","close","volume")]) + "\n" for c in candles)
def load(symbol):
 name, blob_expected, sha_expected = DATA[symbol]
 url = f"{RAW}/{name}"
 req = urllib.request.Request(url, headers={"User-Agent":"Expansion-SUI-replication/1.0"})
 with urllib.request.urlopen(req, timeout=60) as response: raw = response.read()
 blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
 if blob != blob_expected: raise RuntimeError(f"{symbol} Git blob SHA-1 mismatch: {blob}")
 candles = []
 for r in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
  c = {"timestamp":int(r["timestamp"])}
  c.update({k:float(r[k]) for k in ("open","high","low","close","volume")})
  if not all(math.isfinite(c[k]) for k in ("open","high","low","close","volume")): raise RuntimeError("non-finite OHLCV")
  if min(c[k] for k in ("open","high","low","close")) <= 0 or c["volume"] < 0: raise RuntimeError("invalid OHLCV")
  if c["high"] < max(c["open"],c["close"],c["low"]) or c["low"] > min(c["open"],c["close"],c["high"]): raise RuntimeError("invalid OHLC bounds")
  candles.append(c)
 if len(candles) != 26280: raise RuntimeError(f"{symbol} expected 26280 rows, got {len(candles)}")
 if any(b["timestamp"]-a["timestamp"] != HOUR for a,b in zip(candles,candles[1:])): raise RuntimeError(f"{symbol} hourly gap/duplicate")
 sha = hashlib.sha256(canonical(candles).encode()).hexdigest()
 if sha != sha_expected: raise RuntimeError(f"{symbol} canonical SHA-256 mismatch: {sha}")
 return candles, {"url":url,"path":name,"git_blob_sha1":blob,"canonical_sha256":sha,"row_count":len(candles),"first_timestamp":iso(candles[0]["timestamp"]),"last_timestamp":iso(candles[-1]["timestamp"]),"gaps":0}
def signals(c):
 n=len(c); tr=[]
 for i,x in enumerate(c):
  if i==0: tr.append(x["high"]-x["low"])
  else:
   pc=c[i-1]["close"]; tr.append(max(x["high"]-x["low"],abs(x["high"]-pc),abs(x["low"]-pc)))
 atr=[None]*n
 if n>=14:
  atr[13]=sum(tr[:14])/14
  for i in range(14,n): atr[i]=(atr[i-1]*13+tr[i])/14
 highs=[x["high"] for x in c]; closes=[x["close"] for x in c]; refs=[None]*n
 for i in range(120,n): refs[i]=max(highs[i-120:i])
 out=set(); pending=None
 for i in range(n):
  if i<1 or atr[i-1] is None or refs[i] is None: continue
  if not closes[i] > refs[i]: pending=None; continue
  if pending is None: pending=refs[i]
  else: out.add(i); pending=None
 return out
def calc_metrics(eq, ts, trades, fees, slip, turnover, corrected):
 daily={}
 for t,v in zip(ts,eq): daily[datetime.fromtimestamp(t/1000,timezone.utc).strftime("%Y-%m-%d")]=v
 days=sorted(daily); dr=[daily[days[i]]/daily[days[i-1]]-1 for i in range(1,len(days))]
 mean=sum(dr)/len(dr) if dr else 0
 sd=math.sqrt(sum((x-mean)**2 for x in dr)/(len(dr)-1)) if len(dr)>1 else 0
 sharpe=mean/sd*math.sqrt(365) if sd>0 else None
 peak=CAPITAL; dd=0
 for x in eq: peak=max(peak,x); dd=max(dd,(peak-x)/peak if peak else 0)
 ret=eq[-1]/CAPITAL-1
 duration=(ts[-1]-ts[0])/(365*24*3600*1000)
 winners=sum(t["net_pnl"]>0 for t in trades)
 out={"return":ret,"daily_sharpe":sharpe,"max_drawdown":dd,"trade_count":len(trades),"win_rate":winners/len(trades) if trades else None,"fee_drag":fees/CAPITAL,"slippage_drag":slip/CAPITAL,"turnover":turnover/CAPITAL,"equity_rows":len(eq),"daily_equity":daily,"daily_returns":dict(zip(days[1:],dr)),"annualized_return":(eq[-1]/CAPITAL)**(1/duration)-1 if duration>0 else 0}
 if not corrected: out.update({"gross_total_return":ret,"net_total_return":ret,"reported_exposure":len(trades)/len(eq)})
 return out
def backtest(c, mult, corrected):
 sig=signals(c); n=len(c); op=[x["open"] for x in c]; lo=[x["low"] for x in c]; cl=[x["close"] for x in c]; ts=[x["timestamp"] for x in c]
 fee_rate=.001*mult; slip_rate=.0005*mult
 cash=CAPITAL; qty=0.; entry_price=0.; entry_time=0; entry_fee=entry_slip=0.; cash_at_entry=0.; active=False
 trades=[]; eq=[]; eqts=[]; fees=slips=turnover=0.
 for i in range(n):
  if active and qty>0 and i>=48 and cl[i]<min(lo[i-48:i]):
   raw=op[i+1] if i+1<n else cl[i]; eff=raw*(1-slip_rate); fee=eff*qty*fee_rate; slip=(raw-eff)*qty; proceeds=qty*eff-fee
   pnl=proceeds-cash_at_entry if corrected else proceeds-entry_price*qty-entry_fee-entry_slip
   trades.append({"entry_time":iso(entry_time),"entry_price_effective":entry_price,"exit_time":iso(ts[i+1] if i+1<n else ts[i]),"exit_price_effective":eff,"raw_exit_price":raw,"net_pnl":pnl,"exit_reason":"channel_48","entry_fee":entry_fee,"exit_fee":fee,"entry_slippage":entry_slip,"exit_slippage":slip})
   fees+=fee; slips+=slip
   if not corrected: fees+=entry_fee; slips+=entry_slip
   turnover+=qty*raw; cash=proceeds; qty=0.; active=False; entry_fee=entry_slip=0.
  if not active and i in sig:
   if i+1>=n: eq.append(cash); eqts.append(ts[i]); break
   raw=op[i+1]
   if corrected:
    cash_at_entry=cash; entry_price=raw*(1+slip_rate); qty=cash/(entry_price*(1+fee_rate)); entry_fee=entry_price*qty*fee_rate; entry_slip=(entry_price-raw)*qty; cash=0.
   else:
    qty=cash/raw if raw>0 else 0
    if qty<=0: continue
    entry_price=raw*(1+slip_rate); entry_fee=entry_price*qty*fee_rate; entry_slip=(entry_price-raw)*qty
    cost=entry_price*qty
    if cost>cash: qty=cash/entry_price if entry_price>0 else 0; cost=entry_price*qty
    cash-=cost; cash_at_entry=cash+cost
   fees+=entry_fee; slips+=entry_slip; turnover+=qty*raw; entry_time=ts[i+1]; active=True
   eq.append(cash+qty*cl[i]); eqts.append(ts[i]); continue
  eq.append(cash+(qty*cl[i] if active and qty>0 else 0)); eqts.append(ts[i])
 if active and qty>0:
  raw=cl[-1]; eff=raw*(1-slip_rate); fee=eff*qty*fee_rate; slip=(raw-eff)*qty; proceeds=qty*eff-fee
  pnl=proceeds-cash_at_entry if corrected else proceeds-entry_price*qty-entry_fee-entry_slip
  trades.append({"entry_time":iso(entry_time),"entry_price_effective":entry_price,"exit_time":iso(ts[-1]),"exit_price_effective":eff,"raw_exit_price":raw,"net_pnl":pnl,"exit_reason":"end_of_fold","entry_fee":entry_fee,"exit_fee":fee,"entry_slippage":entry_slip,"exit_slippage":slip})
  fees+=fee; slips+=slip
  if not corrected: fees+=entry_fee; slips+=entry_slip
  turnover+=qty*raw; cash=proceeds
 m=calc_metrics(eq,eqts,trades,fees,slips,turnover,corrected); m["cost_multiplier"]=mult; m["trades"]=trades
 return m
def benchmark(btc,sui):
 sm={x["timestamp"] for x in sui}; common=[x for x in btc if x["timestamp"]>=START and x["timestamp"] in sm]
 if not common: raise RuntimeError("no shared BTC/SUI timestamps")
 a,b=common[0],common[-1]; gross=b["close"]/a["close"]-1; out=[]
 for mult in COSTS:
  fee=.001*mult; slip=.0005*mult
  net=(b["close"]*(1-slip)*(1-fee))/(a["close"]*(1+slip)*(1+fee))-1
  out.append({"cost_multiplier":mult,"gross_return":gross,"net_return":net,"first_timestamp":iso(a["timestamp"]),"last_timestamp":iso(b["timestamp"]),"first_close":a["close"],"last_close":b["close"],"common_rows":len(common)})
 return out
def main():
 sui_all,sui_meta=load("SUIUSDT"); btc_all,btc_meta=load("BTCUSDT")
 sui=[x for x in sui_all if START<=x["timestamp"]<=END]
 if len(sui)!=5256: raise RuntimeError(f"unexpected SUI holdout rows: {len(sui)}")
 source=[backtest(sui,m,False) for m in COSTS]; corrected=[backtest(sui,m,True) for m in COSTS]
 for r in source:
  e=EXPECTED[r["cost_multiplier"]]; got=(r["net_total_return"],r["daily_sharpe"],r["max_drawdown"],r["trade_count"],r["fee_drag"],r["slippage_drag"],r["turnover"],r["win_rate"])
  if any(abs(float(a)-float(b))>1e-9 for a,b in zip(got,e)): raise RuntimeError(f"source replication mismatch at {r['cost_multiplier']}x: {got} != {e}")
 compact=lambda r:{k:v for k,v in r.items() if k not in ("daily_equity","daily_returns","trades")}
 bench=benchmark(btc_all,sui_all)
 report={"experiment_id":"HARMONY-BREAKOUT-SUI-CFG0602-REPLICATION-001","status":"INDEPENDENT_REPLICATION_PASSED_WITH_ACCOUNTING_CAVEATS","generated_utc":datetime.now(timezone.utc).isoformat(),"configuration":{"asset":"SUIUSDT","timeframe":"1h","config_id":"cfg_0602","content_hash":"f61acc43bbc4f28b","entry_rule":"classic_high_breakout","entry_lookback":120,"atr_period":14,"atr_buffer_multiple":0,"confirmation":"two_close","trend_filter":"none","exit":"channel_48","fill":"next_bar_open","holdout_cutoff":iso(START),"first_bar":iso(sui[0]["timestamp"]),"last_bar":iso(sui[-1]["timestamp"]),"rows":len(sui),"initial_capital":CAPITAL},"provenance":{"source_report":f"https://github.com/{REPO}/blob/main/reports/final_research_report.json","holdout_report":f"https://github.com/{REPO}/blob/main/reports/holdout_evaluation_report.json","candidate_manifest":f"https://github.com/{REPO}/blob/main/research/candidate_manifest.json","data":{"SUIUSDT":sui_meta,"BTCUSDT":btc_meta},"holdout_exposure":"EXPOSED_OR_UNKNOWN","source_code_hash":"1f8a7ec03f84c6ce90c372655e58c692a0c15728dd680396a272e22fb93de576","candidate_manifest_code_hash":"382e314141d4826e29ae1c00e720aa3320a2f71fe00723ff07dfa025b79abfb8","code_hash_scopes_reconciled":False},"source_faithful_replication":[compact(x) for x in source],"corrected_cost_accounting_same_fills":[compact(x) for x in corrected],"btc_buy_and_hold_common_timestamps":bench,"conclusions":["Source-faithful metrics must match the frozen report within 1e-9.","The source assigns gross_total_return and net_total_return from the same equity curve and does not consistently deduct entry fees from equity.","The source fee_drag aggregation counts entry fees again when a trade closes; it is not a clean cost decomposition.","Corrected accounting preserves source entry/exit event timing to isolate accounting effects; it is not a full redesign of execution timing.","Holdout independence remains unverified; the source report says EXPOSED_OR_UNKNOWN.","Eight trades are a small sample. No parameter search or winner reselection was performed."]}
 (HERE/"result.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
 with (HERE/"trades.csv").open("w",newline="",encoding="utf-8") as f:
  fields=["cost_multiplier","entry_time","entry_price_effective","exit_time","exit_price_effective","raw_exit_price","net_pnl","exit_reason","entry_fee","exit_fee","entry_slippage","exit_slippage"]
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader()
  for r in source:
   for t in r["trades"]: w.writerow({"cost_multiplier":r["cost_multiplier"],**t})
 with (HERE/"daily_returns.csv").open("w",newline="",encoding="utf-8") as f:
  fields=["date"]+[f"equity_{int(r['cost_multiplier'])}x" for r in corrected]+[f"daily_return_{int(r['cost_multiplier'])}x" for r in corrected]
  w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); days=sorted(set().union(*(set(r["daily_equity"]) for r in corrected)))
  for day in days:
   row={"date":day}
   for r in corrected:
    m=int(r["cost_multiplier"]); row[f"equity_{m}x"]=r["daily_equity"].get(day,""); row[f"daily_return_{m}x"]=r["daily_returns"].get(day,"")
   w.writerow(row)
 print(json.dumps({"status":report["status"],"source_faithful":[compact(x) for x in source],"corrected":[compact(x) for x in corrected],"benchmark":bench,"artifacts":["result.json","trades.csv","daily_returns.csv"]},indent=2))
if __name__=="__main__": main()
