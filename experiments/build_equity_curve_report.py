import csv, json, math, statistics
from pathlib import Path

OUT=Path("artifacts/HARMONY-PROMOTED-VALIDATION-001")
CSV=OUT/"equity_curves_oos.csv"
SUMMARY=OUT/"equity_curve_summary.csv"
SVG=OUT/"fin0012-fin0024-equity-curves-vs-benchmarks.svg"

def metrics(values):
    c=[1.0]+values
    rr=[c[i]/c[i-1]-1.0 for i in range(1,len(c))]
    sd=statistics.stdev(rr) if len(rr)>1 else 0.0
    sharpe=statistics.mean(rr)/sd*math.sqrt(365.25) if sd else 0.0
    peak=c[0]; mdd=0.0
    for x in c:
        peak=max(peak,x); mdd=min(mdd,x/peak-1.0)
    years=(len(c)-1)/365.25
    cagr=c[-1]**(1/years)-1.0
    return c[-1], c[-1]-1.0, cagr, sharpe, mdd

with CSV.open(newline="",encoding="utf-8") as f:
    rows=list(csv.DictReader(f))

series=[
("FIN-0012",[float(r["FIN-0012"]) for r in rows]),
("FIN-0024",[float(r["FIN-0024"]) for r in rows]),
("BTC buy-and-hold",[float(r["BTCUSDT_buy_and_hold"]) for r in rows]),
("Equal-weight long-only",[float(r["equal_weight_long_only"]) for r in rows]),
]

with SUMMARY.open("w",newline="",encoding="utf-8") as f:
    w=csv.writer(f); w.writerow(["series","final_equity","cumulative_return","CAGR","Sharpe","max_drawdown"])
    for name,vals in series: w.writerow([name,*metrics(vals)])

W,H=1400,760; L,R,T,B=95,45,70,80; PW,PH=W-L-R,H-T-B
all_vals=[v*100 for _,vals in series for v in vals]
ymin=min(all_vals); ymax=max(all_vals); pad=(ymax-ymin)*0.05 or 1.0
ymin-=pad; ymax+=pad
def x(i): return L+PW*i/max(1,len(rows)-1)
def y(v): return T+PH*(1-(v-ymin)/(ymax-ymin))
def path(vals):
    return " ".join(("M" if i==0 else "L")+f"{x(i):.2f},{y(v):.2f}" for i,v in enumerate(vals))
dates=[r["date"] for r in rows]
tick_idx=sorted(set([0,len(rows)//4,len(rows)//2,3*len(rows)//4,len(rows)-1]))
palette=["#2f5d8a","#8c5a2b","#555555","#9a9a9a"]
labels=[s[0] for s in series]
svg=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">','<rect width="100%" height="100%" fill="white"/>',
'<text x="95" y="36" font-family="Arial" font-size="24" font-weight="600">FIN-0012 and FIN-0024 OOS Equity Curves vs Benchmarks</text>',
'<text x="95" y="56" font-family="Arial" font-size="13" fill="#555">2024-05-22 to 2025-10-31 · indexed to 100 · base-case costs + native funding</text>']
for g in range(6):
    val=ymin+(ymax-ymin)*g/5; yy=y(val)
    svg += [f'<line x1="{L}" y1="{yy:.2f}" x2="{W-R}" y2="{yy:.2f}" stroke="#e5e5e5"/>',
            f'<text x="{L-10}" y="{yy+4:.2f}" text-anchor="end" font-family="Arial" font-size="11" fill="#666">{val:.0f}</text>']
svg += [f'<line x1="{L}" y1="{T}" x2="{L}" y2="{H-B}" stroke="#444"/>',
        f'<line x1="{L}" y1="{H-B}" x2="{W-R}" y2="{H-B}" stroke="#444"/>',
        f'<text x="30" y="{T+PH/2:.0f}" transform="rotate(-90 30 {T+PH/2:.0f})" font-family="Arial" font-size="13">Equity index</text>']
for i in tick_idx:
    xx=x(i); svg += [f'<text x="{xx:.2f}" y="{H-B+25}" text-anchor="middle" font-family="Arial" font-size="11" fill="#666">{dates[i][:7]}</text>']
for (name,vals),color in zip(series,palette):
    dash=' stroke-dasharray="7,5"' if "BTC" in name else (' stroke-dasharray="2,5"' if "Equal" in name else "")
    svg.append(f'<path d="{path([v*100 for v in vals])}" fill="none" stroke="{color}" stroke-width="2"{dash}/>')
for j,(label,color) in enumerate(zip(labels,palette)):
    xx=95+j*300
    svg += [f'<line x1="{xx}" y1="715" x2="{xx+28}" y2="715" stroke="{color}" stroke-width="3"/>',
            f'<text x="{xx+36}" y="720" font-family="Arial" font-size="12">{label}</text>']
svg.append("</svg>"); SVG.write_text("\n".join(svg),encoding="utf-8")
print(json.dumps({"rows":len(rows),"date_start":dates[0],"date_end":dates[-1],"summary":str(SUMMARY),"svg":str(SVG)},indent=2))
