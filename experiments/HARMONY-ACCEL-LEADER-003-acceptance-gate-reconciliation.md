# HARMONY-ACCEL-LEADER-003 — acceptance-gate reconciliation checklist

Status: preflight design note only. This is not the strategy preregistration and does not authorize a strategy run.

## Canonical inherited promotion gates

When (and only when) the universe preflight passes, HARMONY-ACCEL-LEADER-003 must copy these requirements from the Experiment 002 YAML/runner and make their evaluation fields explicit:

1. At least 50 closed primary-strategy trades in final OOS.
2. Positive primary net expectancy at 5 bps slippage per side (base-cost gate).
3. Positive primary net expectancy at 20 bps slippage per side (highest-cost gate).
4. Positive annualized primary net Sharpe at 20 bps slippage per side.
5. Primary final-OOS expectancy at 5 bps per side strictly exceeds the fresh-24-hour-gainer comparator expectancy at 5 bps per side.
6. Positive primary net total return in each of the two final-OOS halves, evaluated at 5 bps slippage per side to match the current runner.
7. No material data-integrity failure; if any preflight/history/data issue blocks admissibility, no strategy metrics may be treated as evaluable.
8. Passing all historical gates grants eligibility for independent future-only paper confirmation only, never automatic live deployment.

The base-cost positive-expectancy condition is present in the YAML's positive_net_expectancy_at_base_and_highest_slippage gate but is not an explicit boolean in the current Experiment 002 runner's promotion-gate object. The comparator test alone does not logically imply positive primary expectancy: a primary strategy can outperform a worse comparator while both have negative expectancy. The next experiment's runner and tests must enforce both the base-cost absolute expectancy gate and the relative comparator gate separately.

## Required implementation reconciliation before any Experiment 003 backtest

- Treat the frozen preregistration YAML as the canonical gate declaration.
- Include a single evaluator that returns each gate as a named boolean plus measured value and cost scenario; avoid status inference from a green Actions job.
- Add deterministic unit tests for each failure boundary, including missing expectancy, exactly 50 vs 49 trades, zero expectancy, zero Sharpe, a comparator that is worse while both strategies lose, and either OOS half returning zero or less.
- Check consistency between YAML, prompt, runner, report, Contraction record, and workflow summary.
- Keep the acceleration formula, freshness rule, next-bar execution, stop semantics, 5 bps taker fee, 5/10/20 bps per-side slippage scenarios, 60/20/20 chronological split, and comparator definitions unchanged.
- No final-OOS tuning, symbol substitutions, or parameter searches.
- Historical metrics remain exploratory because the direction was chosen after inspecting Experiment 001; separately preregister future-only paper evaluation before live consideration.

## Preflight/experiment boundary

The candidate universe remains provisional until all candidate instruments pass the live-instrument check and all 24 markets have confirmed, aligned, fresh 15-minute data from the required historical start, acceptable individual/shared gaps, and at least 730 days of common history. A failed data gate must be recorded and must not generate strategy performance metrics. The preflight itself is not a strategy experiment.
