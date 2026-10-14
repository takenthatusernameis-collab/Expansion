# Harmony MOM121 Deep Grind 001 — preregistered research prompt

## Mission

Test, do not assume, whether fixed 12–1 month momentum in US microcap common stocks has a reproducible, economically meaningful out-of-sample edge after realistic trading costs. Treat all previous descriptions of `mom121` as a hypothesis and historical context, not as evidence that this run has passed.

## Iterative prompt refinement

1. Basic: backtest 12–1 month microcap momentum.
2. Evidence-grade: use a point-in-time investable universe, stable security identifiers, total returns including delisting returns, lagged market capitalization, chronological OOS evaluation, realistic costs, and a matched benchmark.
3. Final: run the frozen candidate in `experiments/HARMONY-MOM121-DEEP-GRIND-001.yaml`. First validate the data contract. If any required survivorship, delisting, return, identity, point-in-time capitalization, or checksum assertion fails, emit `blocked_data_gate` and do not produce strategy-performance claims. With valid data, execute the fixed primary portfolio, the matched equal-weight microcap benchmark, cost stresses, circular 12-month block bootstrap, random-ranking placebos and ADV-capacity diagnostics. Do not tune parameters, select universes, inspect results to change definitions, or use holdout performance to make decisions.

## Data validity is a hard gate

Use only a source that supports point-in-time membership of US-listed common shares and includes active and inactive/delisted securities. Total returns must incorporate distributions and terminal/delisting returns. Market cap, price, exchange/listing status and 20-session ADV must be available as-of each formation date. Stable security identifiers are mandatory. A current ticker list combined with current Yahoo/Stooq symbols is not an acceptable substitute: the backtest must never silently discard delisted failures or classify the historical universe using today's market cap.

The standard input contract is in the preregistered experiment YAML. An optional archive may be provided through the Actions secrets `MOM121_DATA_ARCHIVE_URL` and `MOM121_DATA_ARCHIVE_SHA256`; never log signed URLs or tokens. The archive must contain `manifest.json` and `monthly_panel.csv`.

## Frozen primary strategy

At each month-end formation date, restrict the universe to point-in-time US-listed common stocks with market capitalization no greater than USD 300 million, price at least USD 1, and an available 20-session average dollar volume. Require 11 contiguous prior monthly total-return observations. Score each eligible security by compounded return over the 11 monthly observations ending immediately before the most recent completed month; exclude the return for the latest month. Hold an equal-weight portfolio of the top quintile for the next month.

The primary benchmark is equal-weighting the same eligible, momentum-history-qualified microcap universe. It must use identical return timestamps, rebalancing dates, corporate-action accounting, and costs. Do not compare against a benchmark with a different universe and call the difference alpha.

## Required accounting and validation

- Keep the walk-forward fold/portfolio state isolated. Recompute each fold's starting portfolio and cash from that fold's declared policy; never carry positions, cash, returns, or P&L into a fold unless explicitly specified by the frozen protocol.
- A zero-trade or zero-exposure period must not inherit prior-period strategy returns.
- Signals use information available by the formation cutoff and earn only subsequent returns.
- Include total returns, delisting returns, cash, position weights, turnover, entry/rebalance/exit costs and terminal liquidation.
- Report gross and net CAGR, Sharpe, drawdown, excess returns vs the matched benchmark, turnover, cost drag, yearly/chronological stability, and ADV participation at USD 100k / 1m / 10m portfolio size, recalculating the 1% ADV threshold separately at each size.
- Stress one-way traded-notional costs at 25, 50, 100 and 200 bps. Treat 50 bps as the primary base assumption; all costs apply to actual absolute weight changes, not only to winning trades.
- Use 2,000 circular 12-month block-bootstrap samples of paired final-test excess returns and 250 random-ranking placebos with identical dates, selection count, and costs; compare benchmark-relative net excess Sharpe rather than raw portfolio Sharpe. Fix random seed 121.
- Predefine chronology as 60% discovery, 20% validation, 20% final test by month. No parameters may be chosen using validation or final-test returns; this candidate has no fitted parameters.
- Require at least 120 usable months overall and 36 final-test months. If the fixed history does not meet those minimums, classify results as exploratory only.
- Preserve every failed test and rejected conclusion. Passing this experiment makes a candidate eligible only for independent confirmation, never automatic deployment.

## Output

Write immutable evidence under `artifacts/HARMONY-MOM121-DEEP-GRIND-001/`:
- `run_manifest.json` (commit, timestamps, source and hashes, code version, runtime, seed);
- `data_gate.json` (coverage and checks, every assertion and its result);
- `summary.json` (final status, gate outcomes, metrics, costs, drawdown, placebo and bootstrap results);
- `monthly_returns.csv` (strategy and benchmark, gross/net returns, costs and turnover);
- `report.md` (method, data limitations, results, caveats, and recommendation).

If data is unavailable or fails validation, still upload the gate report and a precise acquisition checklist; do not fabricate a performance table. Never persist private data, tokens, URLs containing credentials, or an unredacted environment dump.

## Decision rule

The answer is one of: `blocked_data_gate`, `exploratory_only`, `rejected`, `inconclusive`, or `eligible_for_independent_confirmation`. A favorable in-sample return or top-line Sharpe alone is never a pass. Report the exact reason for any non-pass.

## Explicit execution request — 2026-10-14

The user explicitly requested that this heavy MOM121 study be run in the Expansion execution repository. Start a fresh execution from the current default-branch revision; do not treat a historical workflow success as evidence that today's run executed the backtest. The previous run's recorded result was `blocked_data_gate` because no verified US microcap panel was found. Recheck the configured checksum-pinned archive source at runtime and execute all preregistered calculations only if the full data contract passes. If it remains unavailable, publish a precise blocked-data artifact and identify exactly which source fields and historical coverage are missing. Do not weaken the data gate, substitute the crypto cache or surviving tickers, fabricate metrics, or start unrelated experiments. Keep all work research/backtesting only.
