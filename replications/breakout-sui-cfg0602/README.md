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
