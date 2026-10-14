# HARMONY-ACCEL-LEADER-001 — Iteratively Refined Research Prompt

## Objective
Determine whether buying a fresh cryptocurrency acceleration leader and managing it with a 3× ATR trailing stop has reproducible predictive value after execution costs. The target is evidence, not a positive backtest. Execute the experiment in Expansion, track its identity and outcome in Contraction, and make no changes to earlier frozen experiments.

## Iterative refinement pass 1 — operational definition
Use the frozen specification in experiments/HARMONY-ACCEL-LEADER-001.yaml as the source of truth. Evaluate completed 15-minute candles. Calculate consecutive 15-minute log returns, the latest 1-hour log return, the change from the previous non-overlapping hourly return, and the acceleration normalized by the standard deviation of the prior 168 completed hourly returns. The primary signal is the top normalized acceleration score, at least 1.0, with positive latest one-hour return. It must not have been the normalized acceleration leader during the preceding 96 completed observations. Enter at the next 15-minute open and use a monotonic 3×ATR(14) trailing stop. Spot, long-only, no leverage, one position at a time, no profit target.

## Iterative refinement pass 2 — adversarial threats
Try to falsify the result. Explicitly investigate: late-entry/exhaustion risk; rank flips and ties; whether volatility normalization adds value; whether the freshness rule contributes value; candle gaps and partial history; survivorship from using a fixed current symbol list; look-ahead from signal/stop timing; optimistic stop fills; cost sensitivity; and concentration of profits in a few trades. Missing/delisted markets are not recoverable from the fixed panel, so label any inference as restricted to the predefined symbols and do not call it market-wide evidence. Do not insert extra filters after observing final OOS results.

## Iterative refinement pass 3 — experiments and acceptance criteria
Run the single pre-registered primary candidate plus the three frozen comparators in the YAML. Do not run a parameter grid. Keep execution costs and stop mechanics identical across comparators. Score discovery, validation and untouched final chronological OOS separately. Evaluate 5, 10 and 20 bps slippage per side in addition to the fixed 5 bps taker fee per side. Report daily performance, closed trade returns, expectancy, drawdown, Sharpe/Sortino, turnover/exposure, holding time, trade concentration, results by OOS half, and a seeded five-day block bootstrap where sample size allows.

## Data and engineering gates
1. Confirm that the requested instruments exist on OKX spot and fetch only confirmed 15-minute candles from the requested start date.
2. Preserve normalized source bars, their hashes, request/coverage metadata, code revision, and deterministic configurations.
3. Reject duplicate timestamps, malformed OHLC values, non-monotonic pagination, insufficient history, stale tails, or material missing-bar gaps. Never forward-fill bars or silently substitute daily data.
4. Run unit tests for the return/acceleration math, freshness window boundaries, stop monotonicity, gap-through-stop execution, and no same-bar look-ahead.
5. If data retrieval is capped or coverage is inadequate, write an explicit BLOCKED_DATA_GATE report and upload it; a green workflow is not a strategy result.
6. When a technical failure occurs, preserve its traceback and artifacts; retry only bounded, diagnosable failures. Do not label execution failures as a negative strategy finding.

## Research interpretation
Do not claim edge merely because cumulative return is positive. The candidate can advance only if out-of-sample expectancy remains positive after the highest predefined slippage, its net OOS Sharpe is positive, it outperforms the fresh-24-hour-gainer baseline on net OOS expectancy, both OOS halves are positive, and at least 50 closed OOS trades exist without material integrity failures. Passing only makes it eligible for independent confirmation, not live deployment. Otherwise label the result rejected, inconclusive, or blocked, with the specific reason.

## Execution and persistence
Run in Expansion using the separate branch research/harmony-accel-leader-001. Preserve data and outputs as a workflow artifact. Record preregistration, code/data digests, workflow run IDs, result artifact digests, metrics and disposition in Contraction. Do not modify main or overwrite previous experiment evidence. Finish with a result summary that distinguishes verified executed outcomes from blockers and assumptions.