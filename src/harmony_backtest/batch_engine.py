"""Shared-state batch engine for high-throughput deterministic research.

The expensive data work happens once per batch:
- load the normalized market store once;
- build one common date grid;
- precompute asset daily returns once;
- load funding events once.

Candidate-specific work is limited to target-weight generation and portfolio accounting.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class FundingEvent:
    symbol: str
    date: str
    rate: float


@dataclass(frozen=True)
class SharedDailyState:
    symbols: tuple[str, ...]
    dates: tuple[str, ...]
    close: Mapping[tuple[str, str], float]
    quote_volume: Mapping[tuple[str, str], float]
    taker_buy_quote_volume: Mapping[tuple[str, str], float]
    daily_return: Mapping[tuple[str, str], float]
    funding_by_day: Mapping[tuple[str, str], tuple[float, ...]]


@dataclass(frozen=True)
class CandidateRun:
    final_equity: float
    cumulative_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    turnover: float
    funding_pnl_sum: float
    rebalance_count: int
    equity_curve: tuple[float, ...]


def build_shared_state(rows: Sequence, funding_events: Sequence[FundingEvent] = ()) -> SharedDailyState:
    symbols = tuple(sorted({row.symbol for row in rows}))
    dates = tuple(sorted({row.date for row in rows}))
    by_key = {(row.symbol, row.date): row for row in rows}

    if not symbols or not dates:
        raise ValueError("shared state requires non-empty rows")
    if any((symbol, date) not in by_key for symbol in symbols for date in dates):
        raise ValueError("shared state requires an exact common symbol/date panel")

    close = {(s, d): by_key[(s, d)].close for s in symbols for d in dates}
    quote_volume = {(s, d): by_key[(s, d)].quote_volume for s in symbols for d in dates}
    taker_buy_quote_volume = {
        (s, d): by_key[(s, d)].taker_buy_quote_volume for s in symbols for d in dates
    }

    daily_return: dict[tuple[str, str], float] = {}
    for s in symbols:
        for i, d in enumerate(dates):
            daily_return[(s, d)] = 0.0 if i == 0 else (
                close[(s, d)] / close[(s, dates[i - 1])] - 1.0
            )

    funding: dict[tuple[str, str], list[float]] = {}
    for event in funding_events:
        if event.symbol not in symbols or event.date not in dates:
            continue
        funding.setdefault((event.symbol, event.date), []).append(float(event.rate))

    return SharedDailyState(
        symbols=symbols,
        dates=dates,
        close=close,
        quote_volume=quote_volume,
        taker_buy_quote_volume=taker_buy_quote_volume,
        daily_return=daily_return,
        funding_by_day={k: tuple(v) for k, v in funding.items()},
    )


WeightFunction = Callable[[str, SharedDailyState], Mapping[str, float] | None]


def _metrics(curve: Sequence[float]) -> tuple[float, float, float]:
    if not curve or curve[0] <= 0:
        raise ValueError("invalid equity curve")
    returns = [
        curve[i] / curve[i - 1] - 1.0
        for i in range(1, len(curve))
    ]
    if len(returns) > 1 and statistics.stdev(returns) > 0:
        sharpe = statistics.mean(returns) / statistics.stdev(returns) * math.sqrt(365.25)
    else:
        sharpe = 0.0

    peak = curve[0]
    mdd = 0.0
    for value in curve:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1.0)

    years = max((len(curve) - 1) / 365.25, 1e-12)
    cagr = curve[-1] ** (1.0 / years) - 1.0
    return cagr, sharpe, mdd


def run_weight_batch(
    state: SharedDailyState,
    candidate_weight_functions: Mapping[str, WeightFunction],
    *,
    fee_rate: float = 0.0006,
    slippage_rate: float = 0.0005,
    terminal_liquidation: bool = True,
) -> dict[str, CandidateRun]:
    """Run many deterministic candidates over one shared market/funding state.

    All candidates share the same date loop, return calculations, funding-event lookup,
    and normalized market data. Each candidate retains its own exact portfolio accounting,
    target-weight function, and rebalance schedule by returning None on non-rebalance dates.
    """

    if fee_rate < 0 or slippage_rate < 0:
        raise ValueError("cost rates must be non-negative")
    if not candidate_weight_functions:
        raise ValueError("at least one candidate is required")

    names = tuple(candidate_weight_functions)
    weights = {
        name: {s: 0.0 for s in state.symbols}
        for name in names
    }
    equity = {name: 1.0 for name in names}
    turnover = {name: 0.0 for name in names}
    funding_pnl = {name: 0.0 for name in names}
    rebalance_count = {name: 0 for name in names}
    curves = {name: [] for name in names}

    for i, date in enumerate(state.dates):
        returns = {s: state.daily_return[(s, date)] for s in state.symbols}
        funding_today = {
            s: state.funding_by_day.get((s, date), ())
            for s in state.symbols
        }

        for name in names:
            current = weights[name]

            # 1. Funding on the position held immediately before the decision-date rebalance.
            for symbol in state.symbols:
                for rate in funding_today[symbol]:
                    pnl = -current[symbol] * rate
                    equity[name] *= 1.0 + pnl
                    funding_pnl[name] += pnl

            # 2. Price return of the position held during the current day.
            equity[name] *= 1.0 + sum(
                current[symbol] * returns[symbol]
                for symbol in state.symbols
            )

            # 3. Candidate decides whether today is its declared rebalance date.
            # Returning None means no rebalance; returning a mapping means rebalance.
            target = candidate_weight_functions[name](date, state)
            if target is not None:
                missing = set(state.symbols) - set(target)
                extra = set(target) - set(state.symbols)
                if missing or extra:
                    raise ValueError(
                        f"candidate {name} weight universe mismatch: "
                        f"missing={sorted(missing)} extra={sorted(extra)}"
                    )
                target = {s: float(target[s]) for s in state.symbols}
                if any(not math.isfinite(v) for v in target.values()):
                    raise ValueError(f"candidate {name} produced non-finite target weights")
                rebalance_count[name] += 1
                delta = sum(abs(target[s] - current[s]) for s in state.symbols)
                turnover[name] += delta / 2.0
                equity[name] *= max(
                    0.0,
                    1.0 - (fee_rate + slippage_rate) * delta,
                )
                current = target
                weights[name] = current

            curves[name].append(equity[name])

    for name in names:
        if terminal_liquidation:
            liquidation = sum(abs(v) for v in weights[name].values())
            equity[name] *= max(
                0.0,
                1.0 - (fee_rate + slippage_rate) * liquidation,
            )
            curves[name][-1] = equity[name]

    results: dict[str, CandidateRun] = {}
    for name in names:
        curve = tuple(curves[name])
        cagr, sharpe, mdd = _metrics(curve)
        results[name] = CandidateRun(
            final_equity=equity[name],
            cumulative_return=equity[name] - 1.0,
            cagr=cagr,
            sharpe=sharpe,
            max_drawdown=mdd,
            turnover=turnover[name],
            funding_pnl_sum=funding_pnl[name],
            rebalance_count=rebalance_count[name],
            equity_curve=curve,
        )
    return results
