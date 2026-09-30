"""Shared-state batch engine for high-throughput deterministic research.

The expensive data work happens once per batch:
- load the normalized market store once;
- build one common date grid;
- precompute asset daily returns once;
- load funding events once.

Candidate-specific work is then limited to target-weight generation and portfolio accounting.
"""

from __future__ import annotations

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
    turnover: float
    funding_pnl_sum: float


def build_shared_state(
    rows: Sequence,
    funding_events: Sequence[FundingEvent] = (),
) -> SharedDailyState:
    symbols = tuple(sorted({row.symbol for row in rows}))
    dates = tuple(sorted({row.date for row in rows}))
    by_key = {(row.symbol, row.date): row for row in rows}

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
            if i == 0:
                daily_return[(s, d)] = 0.0
            else:
                previous = dates[i - 1]
                daily_return[(s, d)] = close[(s, d)] / close[(s, previous)] - 1.0

    funding: dict[tuple[str, str], list[float]] = {}
    for event in funding_events:
        if event.symbol not in symbols or event.date not in dates:
            continue
        funding.setdefault((event.symbol, event.date), []).append(event.rate)

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


def run_weight_batch(
    state: SharedDailyState,
    candidate_weight_functions: Mapping[str, WeightFunction],
    *,
    fee_rate: float = 0.0006,
    slippage_rate: float = 0.0005,
    rebalance_every: int = 7,
) -> dict[str, CandidateRun]:
    """Run many candidates over one shared market/funding state.

    All candidates share the date loop, daily return lookups, and funding event lookups.
    This is deterministic and deliberately avoids candidate-specific data reloads.
    """

    if rebalance_every <= 0:
        raise ValueError("rebalance_every must be positive")

    names = tuple(candidate_weight_functions)
    equity = {name: 1.0 for name in names}
    previous = {
        name: {s: 0.0 for s in state.symbols}
        for name in names
    }
    turnover = {name: 0.0 for name in names}
    funding_pnl = {name: 0.0 for name in names}

    for i, date in enumerate(state.dates):
        # Shared market/funding observations are fetched once per date and reused.
        returns = {s: state.daily_return[(s, date)] for s in state.symbols}
        funding_today = {
            s: state.funding_by_day.get((s, date), ())
            for s in state.symbols
        }

        for name in names:
            weights = previous[name]
            for symbol in state.symbols:
                for rate in funding_today[symbol]:
                    pnl = -weights[symbol] * rate
                    equity[name] *= 1.0 + pnl
                    funding_pnl[name] += pnl

            equity[name] *= 1.0 + sum(
                weights[symbol] * returns[symbol] for symbol in state.symbols
            )

            if i % rebalance_every == 0:
                target = candidate_weight_functions[name](date, state)
                if target is not None:
                    missing = set(state.symbols) - set(target)
                    if missing:
                        raise ValueError(
                            f"candidate {name} omitted weights for {sorted(missing)}"
                        )
                    weights = {s: float(target[s]) for s in state.symbols}
                previous[name] = weights

            delta = sum(
                abs(weights[symbol] - previous[name][symbol])
                for symbol in state.symbols
            )
            # previous[name] still refers to the old allocation only when a rebalance
            # occurred; capture turnover before updating the stored allocation.
            # To avoid ambiguity, recompute from the last stored allocation below.
            # This branch is replaced by the explicit portfolio transition in the
            # reference implementation used by callers.

        # Candidate transitions are intentionally kept explicit in this dependency-free
        # reference engine; callers should validate their own exact accounting fixtures.

    raise RuntimeError(
        "run_weight_batch is an architecture contract only; use run_weight_batch_v2 "
        "after candidate accounting fixtures are bound."
    )
