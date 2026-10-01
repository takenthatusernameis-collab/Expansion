# Harmony Alpha Autopsy v1 — Highest-Priority Research Diagnostic

## Objective

Run a **deterministic alpha-autopsy layer** against the already-executed/frozen FIN-0012 and FIN-0024 candidates.

The purpose is not to improve, optimize, retune, promote, or mutate either candidate.

The purpose is to determine whether their OOS return streams contain economically meaningful information **beyond simple benchmark exposure**, and whether their asset selection beats a matched random long/short placebo.

## Absolute research-integrity rules

1. Do not modify FIN-0012 or FIN-0024 definitions.
2. Do not change parameters, horizons, universe, rebalance cadence, direction, weights, costs, funding treatment, or signal construction.
3. Do not access any final holdout after 2025-10-31.
4. Do not perform parameter searches or select parameters based on this diagnostic.
5. Reproduce the frozen candidates exactly from their registered specifications and verified caches.
6. Treat all diagnostics as descriptive/audit outputs; do not promote or reject a candidate solely from a post-hoc visual impression.
7. Preserve cryptographic provenance for every input used.
8. If a required candidate/data artifact is unavailable, report the missing dependency explicitly rather than inventing a result.

## Required diagnostics

For each candidate, produce:

### A. Raw OOS performance
Use the frozen OOS period 2024-05-22 through 2025-10-31 and report:
- cumulative return
- CAGR
- Sharpe
- Sortino if available from existing deterministic utilities
- maximum drawdown
- turnover
- transaction costs
- funding PnL

### B. Benchmark-exposure attribution

Using only information available through each date, calculate a **60-observation rolling linear attribution**:

strategy return =
intercept +
BTC return beta +
same-universe equal-weight long-only return beta +
residual return

Report:
- rolling BTC beta
- rolling equal-weight beta
- rolling R²
- cumulative residual equity curve
- residual Sharpe
- residual CAGR
- residual max drawdown

Also provide a full-OOS descriptive regression as a separate diagnostic, clearly labelled non-causal/descriptive.

### C. Benchmark-relative active return

Compute and report:
- strategy cumulative return minus BTC benchmark
- strategy cumulative return minus equal-weight benchmark
- active-return volatility
- information ratio
- active max drawdown

Do not call these “alpha” unless explicitly defined as regression residual return.

### D. Matched placebo

Generate a deterministic placebo distribution preserving, at every rebalance:
- exact universe
- exact number of longs
- exact number of shorts
- exact gross exposure
- same rebalance dates
- same fee/slippage schedule
- same funding accounting
- same terminal liquidation

Randomize only the cross-sectional asset identities.

Use exactly 1,000 deterministic placebo paths with a fixed documented seed and no parameter selection.

Report:
- actual Sharpe percentile
- actual cumulative-return percentile
- actual max-drawdown percentile
- placebo mean/median/5th/95th percentiles
- whether the actual path materially exceeds the matched null distribution

### E. Equity-curve decomposition

Create a CSV with, at minimum:
date,
strategy_return,
strategy_equity,
btc_return,
btc_equity,
equal_weight_return,
equal_weight_equity,
rolling_btc_beta,
rolling_equal_weight_beta,
rolling_r2,
residual_return,
residual_equity.

### F. Redundancy verdict

Classify each candidate descriptively as:
- benchmark-dominant
- mixed exposure
- relatively independent

Do not use this classification as a political/evaluative ranking or as a promotion decision. It is a research diagnostic.

### G. Research recommendation

Produce one of:
- preserve as independent candidate
- investigate portfolio construction / factor neutralization
- archive as largely redundant

This recommendation must be based on the diagnostics above and must **not mutate the frozen candidate**.

## Required artifact set

Write:

artifacts/HARMONY-ALPHA-AUTOPSY-V1/
- alpha_autopsy.json
- alpha_autopsy_report.md
- equity_curves.csv
- placebo_summary.json
- input_manifest.json

Bind all relevant input cache/file hashes into input_manifest.json and include its SHA-256 in alpha_autopsy.json.

## Success condition

The workflow is successful only if:
- both frozen candidates are reproducibly reconstructed or a clearly documented dependency failure is emitted;
- benchmark-factor attribution is complete wherever raw return series exist;
- placebo diagnostics are complete wherever candidate reconstruction is complete;
- no candidate definition was changed;
- no holdout data were accessed;
- all outputs are reproducible from the recorded inputs and fixed seed.
