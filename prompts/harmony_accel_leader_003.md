# HARMONY-ACCEL-LEADER-003 — Iteratively Refined Execution Prompt

## Prompt refinement history

### Version 1 — basic objective
Run the fresh normalized crypto acceleration-leader backtest on a 24-symbol OKX spot panel using the 3× ATR trailing stop.

**Defect found:** this does not specify which candle artifact is authoritative, allows implicit universe drift, and is too ambiguous about what counts as a pass.

### Version 2 — evidence-grade objective
Execute a separate preregistered follow-up after Experiment 002's EOS-USDT live-market failure. Replace EOS-USDT with OP-USDT before execution, pin the successful preflight artifact and hashes, reuse the existing signal/stop/cost model, and explicitly report the historical OOS results.

**Defect found:** Experiment 002's promotion evaluator compares primary expectancy with a comparator but does not separately enforce positive absolute primary expectancy at base costs. A strategy could beat an even worse comparator while losing money. Passing infrastructure checks could also be confused with passing strategy gates.

### Final version — executable, falsifiable, fail-closed research task

You are the research engineer for Expansion. Execute HARMONY-ACCEL-LEADER-003 from this frozen branch, independently of the blocked Experiment 002 branch.

#### A. Freeze and authenticate inputs
- Use the exact 24 symbols in `experiments/HARMONY-ACCEL-LEADER-003.yaml`; OP-USDT replaces the unavailable EOS-USDT before this preregistration.
- Fetch only the immutable preflight artifact from run `38643611024`, artifact `11934457001`.
- Require artifact ZIP SHA-256 `9e20b58052446be9574ac77f4d9d3fef5f1c22e5c7f1dfb5ec94a309e183edfd`, preflight commit `a7da33233e1567db91554d819a39957d084d8fe6`, candidate-spec hash `8f561ef21f587dbd1712dd500a891ba58abe891a35c1694e81e2a8ef5bc94186`, and summary hash `0074d5f245fa4323d7370491d320a756c50bcbb6233b3447a2ef62f2cae6c7fc`.
- Revalidate each of the 24 canonical uncompressed candle hashes, row counts, timestamp monotonicity/alignment, OHLCV integrity and common timestamp grid. Do not refetch, forward-fill, silently substitute, or mix data revisions. If any check fails, emit only a `blocked_data_gate` report and no strategy metrics.

#### B. Keep signal and execution frozen
- Evaluate completed 15-minute bars.
- One-hour log return equals the latest four completed 15-minute log returns; acceleration is this value less its value four bars earlier.
- Normalize by the population standard deviation of 168 completed hourly returns.
- The primary signal is the highest normalized acceleration, score at least 1.0, positive one-hour return, and no leader occurrence for that symbol during the prior 96 completed observations. Record leader history on every bar, whether or not other trade filters pass.
- Enter at the following bar open; one long spot position, no leverage, current NAV notional.
- Use ATR(14) from 15-minute bars and the monotonic 3× ATR trail, with conservative pre-existing-stop ordering, gap-through-open fill, and forced final liquidation.
- Charge 5 bps taker fee per side and test 5, 10 and 20 bps slippage per side.
- Retain all frozen comparators, exact 60/20/20 chronology, and 1,000-replication five-day moving-block bootstrap with seed 1401. Do not tune parameters or the final OOS.

#### C. Enforce every gate independently
At base slippage of 5 bps per side, absolute primary net expectancy must be strictly positive. At highest slippage of 20 bps per side, absolute primary net expectancy and annualized net Sharpe must both be strictly positive. The primary must also have at least 50 closed final-OOS trades, exceed the fresh-24-hour-gainer's base-cost expectancy, and have positive net total returns in both final-OOS halves at base costs. Preflight evidence must remain valid.

Implement each gate as an explicit boolean with measured values; missing values are failures. Test exactly 49 vs 50 trades, zero expectancy, zero Sharpe, both strategies losing while the primary outperforms, and nonpositive return in either OOS half.

#### D. Preserve evidence and interpretation
Persist the exact input provenance, source hashes, split boundaries, all cost cases, strategy/comparator metrics, trades, daily equity, bootstrap intervals, and each gate in the GitHub Actions artifact. Keep runs research-only; no live orders. This candidate was chosen after seeing Experiment 001, so even a full historical pass is exploratory and allows only a separate future-only paper test. Do not change or merge either earlier experiment.

#### Execution rule
Run compilation and tests first, execute the fixed strategy only against the authenticated artifact, inspect the job log and uploaded summary after completion, fix any implementation defect in a new commit and rerun the necessary checks, and report the exact final disposition. A successful GitHub Actions job is not itself a strategy pass.
