# SUIUSDT cfg_0602 independent replication

This directory isolates the frozen breakout winner as a separate Expansion experiment.

## Run

```bash
python replications/breakout-sui-cfg0602/replicate.py
```

The stdlib-only runner downloads the public SUIUSDT and BTCUSDT hourly CSVs from the breakout repository, validates each Git blob SHA-1 and canonical hourly CSV SHA-256, and fails closed on any mismatch. It writes `result.json`, `trades.csv`, and `daily_returns.csv`.

## What is tested

1. **Source-faithful replication:** match the frozen source report's signal/fill/exit conventions and reported metrics.
2. **Accounting sensitivity:** preserve the same event schedule while correctly including entry fees in portfolio capital.
3. **Benchmark:** calculate BTC buy-and-hold over common SUI/BTC hourly timestamps with matching fee/slippage assumptions.

The accounting-correction pass intentionally preserves the source event schedule to isolate cost accounting; it is not a complete redesign of event timing.

The preliminary independent calculation matched the source report's return, daily Sharpe, maximum drawdown, trade count, fee/slippage drag, turnover, and win rate across all three cost scenarios within floating-point tolerance. The Python runner is committed for a repeatable repository-side rerun; it was not executed through GitHub Actions in this session.

The source report's `EXPOSED_OR_UNKNOWN` holdout status remains unresolved. Eight trades do not establish a durable edge. The source's gross/net fields and fee-drag aggregation have accounting defects documented in `result.json`.

No parameter optimization, live trading, workflow changes, or unrelated Expansion edits are part of this experiment.

## Decisive validation workflow

`replicate.py` is the **source-faithful regression check**. Its job is to determine whether the frozen source metrics can be recreated; because it preserves the source accounting/event schedule, a successful result is not evidence that the metrics are economically correct.

`causal_backtest.py` adds a separate event-time simulation with:
- fills only at the next bar open after a closed-bar signal;
- open and close equity marks in timestamp order;
- entry and exit fees/slippage deducted once from portfolio cash;
- explicit final-window liquidation costs;
- a zero-cost reference plus the frozen 1x/2x/3x fee-and-slippage scenarios;
- both fold-local indicators (closest to the source convention) and pre-window indicator warm-up while trades remain strictly inside the frozen window.

`test_causal_backtest.py` tests next-open timing, accounting, cost monotonicity, window boundaries, and terminal signals. The dedicated GitHub Actions workflow runs those tests and both backtest passes and uploads the resulting ledgers as an artifact; it does not commit generated results back into the repository.

## Interpretation rules

- A passing source-faithful check means **historical implementation reproduction only**.
- A causal pass is the minimum acceptable basis for interpreting the net historical result, but the eight-trade result and `EXPOSED_OR_UNKNOWN` source holdout status prevent a claim of confirmed edge.
- Corrected accounting and event alignment can change the result; that is a finding to report, not a bug to tune away.
- Any new-data validation must be prospective and frozen before outcomes arrive. Do not retune or reselect on these historic results.



## Full-history extension

The same workflow now also runs \`full_history_audit.py\`, which requests SUIUSDT and BTCUSDT hourly candles from the Binance Spot listing-hour boundary (2023-05-03 12:00 UTC) through the latest fully closed hour observed when the job starts. It validates gap-free coverage and OHLCV invariants, cross-checks every overlapping candle against the previously pinned source CSVs, runs the frozen strategy through the independent causal simulator at zero and 1x/2x/3x costs, and exports market data, trades, daily returns, hashes, and a JSON report.

The full-history result is exploratory, not deployment evidence: the previous holdout remains \`EXPOSED_OR_UNKNOWN\`, and whole-history performance is not an independent holdout. If a public endpoint, completeness check, or pinned-data overlap check fails, the audit writes \`full_history_failure.json\` and exits non-zero rather than claiming full-history coverage.
