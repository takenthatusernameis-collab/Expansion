import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from harmony_backtest.batch_engine import FundingEvent, build_shared_state, run_weight_batch
from harmony_backtest.daily_store import read_daily_store

SYMBOLS = ("BTCUSDT","ETHUSDT","LTCUSDT","XRPUSDT","BNBUSDT","BCHUSDT","ADAUSDT","DOGEUSDT")
STORE = Path("data/cache/research/daily_8sym_v1.csv.gz")
FUNDING_ROOT = Path("data/cache/binance/futures_um/deep_history_2019/funding_gateway")

CANDIDATES = ("HARMONY-FIN-0014", "HARMONY-FIN-0015")


def _load_funding():
    events = []
    for symbol in SYMBOLS:
        rows = json.loads((FUNDING_ROOT / f"{symbol}-2019-2025-10.json").read_text())
        for row in rows:
            date = datetime.fromtimestamp(
                int(row["fundingTime"]) / 1000.0, timezone.utc
            ).date().isoformat()
            events.append(FundingEvent(symbol, date, float(row["fundingRate"])))
    return events


def _prior_index(state, date):
    index = {d: i for i, d in enumerate(state.dates)}
    return index[date]


def _reversal_weights(date, state):
    i = _prior_index(state, date)
    if i < 21:
        return None
    prior = state.dates[i - 1]
    prior2 = state.dates[i - 2]
    scores = []
    for symbol in state.symbols:
        median_window = [
            state.quote_volume[(symbol, state.dates[j])]
            for j in range(max(0, i - 20), i)
        ]
        median_volume = statistics.median(median_window)
        if median_volume <= 0:
            return None
        volume_state = state.quote_volume[(symbol, prior)] / median_volume
        if volume_state >= 1.0:
            continue
        prior_return = state.close[(symbol, prior)] / state.close[(symbol, prior2)] - 1.0
        scores.append((-prior_return, symbol))

    if len(scores) < 4:
        return None
    scores.sort(key=lambda x: (-x[0], x[1]))
    if len(scores) % 2:
        scores.pop(len(scores) // 2)
    half = len(scores) // 2
    weights = {symbol: 0.0 for symbol in state.symbols}
    for _, symbol in scores[:half]:
        weights[symbol] = 0.5 / half
    for _, symbol in scores[half:]:
        weights[symbol] = -0.5 / half
    return weights


def _flow_weights(date, state):
    i = _prior_index(state, date)
    if i < 8:
        return None
    scores = []
    for symbol in state.symbols:
        imbalance = []
        for j in range(i - 7, i):
            total = state.quote_volume[(symbol, state.dates[j])]
            buy = state.taker_buy_quote_volume[(symbol, state.dates[j])]
            if total <= 0:
                return None
            imbalance.append((2.0 * buy - total) / total)
        scores.append((statistics.mean(imbalance), symbol))

    scores.sort(key=lambda x: (-x[0], x[1]))
    weights = {symbol: 0.0 for symbol in state.symbols}
    for _, symbol in scores[:3]:
        weights[symbol] = 1.0 / 6.0
    for _, symbol in scores[-3:]:
        weights[symbol] = -1.0 / 6.0
    return weights


def main():
    if not STORE.is_file():
        raise FileNotFoundError(
            f"{STORE} is missing; run the deterministic daily-store materializer first"
        )

    rows = read_daily_store(STORE)
    if tuple(sorted({row.symbol for row in rows})) != tuple(sorted(SYMBOLS)):
        raise RuntimeError("daily store symbol universe mismatch")

    state = build_shared_state(rows, _load_funding())
    functions = {
        "HARMONY-FIN-0014": _reversal_weights,
        "HARMONY-FIN-0015": _flow_weights,
    }

    results = run_weight_batch(
        state,
        functions,
        fee_rate=0.0006,
        slippage_rate=0.0005,
        rebalance_every=7,
        terminal_liquidation=True,
    )

    output = {
        "batch_id": "HARMONY-DISCOVERY-BATCH-001",
        "candidate_count": len(functions),
        "candidates": {
            name: {
                "final_equity": result.final_equity,
                "cumulative_return": result.cumulative_return,
                "cagr": result.cagr,
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
                "turnover": result.turnover,
                "funding_pnl_sum": result.funding_pnl_sum,
            }
            for name, result in results.items()
        },
        "execution_model": "one_normalized_load_shared_state_two_candidates",
        "holdout_access": False,
        "candidate_mutation": False,
        "purpose": "compute-amortized discovery execution; not a substitute for preregistered acceptance artifacts",
    }
    out = Path("artifacts/HARMONY-DISCOVERY-BATCH-001")
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(
        json.dumps(output, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
