"""Minimal deterministic long-only backtest substrate for Harmony.

This module is intentionally small and dependency-free. It is an execution substrate,
not a claim that any strategy is profitable.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Sequence


@dataclass(frozen=True)
class Bar:
    timestamp: str
    close: float


@dataclass(frozen=True)
class Trade:
    entry_timestamp: str
    entry_price: float
    exit_timestamp: str
    exit_price: float
    gross_return: float
    net_return: float


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: float
    final_cash: float
    cumulative_return: float
    trades: tuple[Trade, ...]


def validate_bars(bars: Sequence[Bar]) -> None:
    if not bars:
        raise ValueError("bars must not be empty")
    previous_timestamp: str | None = None
    for bar in bars:
        if not bar.timestamp:
            raise ValueError("timestamp must be non-empty")
        if not isfinite(bar.close) or bar.close <= 0:
            raise ValueError("close must be finite and positive")
        if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
            raise ValueError("bars must be strictly increasing by timestamp")
        previous_timestamp = bar.timestamp


def run_long_only(
    bars: Sequence[Bar],
    signals: Iterable[bool],
    *,
    initial_cash: float = 1.0,
    fee_rate: float = 0.0,
    slippage_rate: float = 0.0,
) -> BacktestResult:
    """Run an all-in/all-out long-only strategy.

    Signal semantics:
      * False -> remain flat or exit at the current bar close.
      * True  -> enter at the current bar close when flat; hold otherwise.

    This deliberately uses current-bar prices only and does not expose future bars
    to the decision. Costs are applied once on entry and once on exit.
    """

    validate_bars(bars)

    if initial_cash <= 0 or not isfinite(initial_cash):
        raise ValueError("initial_cash must be finite and positive")
    for name, value in (("fee_rate", fee_rate), ("slippage_rate", slippage_rate)):
        if value < 0 or not isfinite(value):
            raise ValueError(f"{name} must be finite and non-negative")

    signals_tuple = tuple(signals)
    if len(signals_tuple) != len(bars):
        raise ValueError("signals length must equal bars length")

    cash = initial_cash
    in_position = False
    entry_timestamp = ""
    entry_price = 0.0
    trades: list[Trade] = []

    for bar, signal in zip(bars, signals_tuple):
        if signal and not in_position:
            entry_timestamp = bar.timestamp
            entry_price = bar.close * (1.0 + slippage_rate)
            cash *= max(0.0, 1.0 - fee_rate)
            in_position = True
        elif not signal and in_position:
            exit_price = bar.close * (1.0 - slippage_rate)
            gross_return = exit_price / entry_price - 1.0
            net_return = gross_return - (2.0 * fee_rate)
            cash *= max(0.0, exit_price / entry_price)
            cash *= max(0.0, 1.0 - fee_rate)
            trades.append(
                Trade(
                    entry_timestamp=entry_timestamp,
                    entry_price=entry_price,
                    exit_timestamp=bar.timestamp,
                    exit_price=exit_price,
                    gross_return=gross_return,
                    net_return=net_return,
                )
            )
            in_position = False

    if in_position:
        bar = bars[-1]
        exit_price = bar.close * (1.0 - slippage_rate)
        gross_return = exit_price / entry_price - 1.0
        net_return = gross_return - (2.0 * fee_rate)
        cash *= max(0.0, exit_price / entry_price)
        cash *= max(0.0, 1.0 - fee_rate)
        trades.append(
            Trade(
                entry_timestamp=entry_timestamp,
                entry_price=entry_price,
                exit_timestamp=bar.timestamp,
                exit_price=exit_price,
                gross_return=gross_return,
                net_return=net_return,
            )
        )

    return BacktestResult(
        initial_cash=initial_cash,
        final_cash=cash,
        cumulative_return=cash / initial_cash - 1.0,
        trades=tuple(trades),
    )
