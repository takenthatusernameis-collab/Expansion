# HARMONY-EXTREME-RELATIVE-SPREAD-001 — execution decision

## Decision

**REJECTED by the preregistered historical gates.** This is a valid negative exploratory result for the exact rule tested, not proof that every relative-spread or market-neutral approach is unprofitable. No parameters were tuned after seeing the final-OOS result. No live orders were placed or permitted.

## Reproducibility and run status

- Primary hosted execution: [GitHub Actions run 38696279753](https://github.com/takenthatusernameis-collab/Expansion/actions/runs/38696279753)
- PR-triggered duplicate verification: [GitHub Actions run 38696285849](https://github.com/takenthatusernameis-collab/Expansion/actions/runs/38696285849)
- Draft review PR: [Expansion PR #10](https://github.com/takenthatusernameis-collab/Expansion/pull/10)
- Evidence artifact: [harmony-extreme-relative-spread-001-evidence](https://github.com/takenthatusernameis-collab/Expansion/actions/runs/38696279753/artifacts/11953449796)
- Artifact ID: 11953449796
- Artifact SHA-256: c782562d827743ead97b3e1d12ff1e2a865faea1cd6115ae2f01a49ea7256d24
- Experiment code commit used by the primary run: 171a15608073463a285f728abfdfef4c210ab3cb
- Runner SHA-256: 2e0ce13c9f761116d871ad5dd52ff39844347548799784486b4145ee25122ac4
- Specification SHA-256: 0de04b8a2dfd20ed9c37045f729c9d1d3f2a6b9bf8070c7203368635debf6a2b
- Automated unit/regression tests: **12 passed**.
- GitHub Actions research job, evidence upload and final-decision gate: **passed**. This indicates successful execution and reporting, not a profitable strategy.

The pinned source is run 38643611024, artifact 11934457001. The independently downloaded result archive's SHA-256 matches GitHub's artifact digest. The embedded preflight-summary SHA-256 matches 0074d5f245fa4323d7370491d320a756c50bcbb6233b3447a2ef62f2cae6c7fc; all 24 canonical uncompressed candle hashes matched. The source panel has 132,758 common 15-minute bars, 2023-01-01 00:00 UTC through 2026-10-14 21:15 UTC, with zero joint missing timestamps.

A first attempt failed before strategy execution because direct script execution did not put the repository root on Python's import path. That failure was preserved and explicitly not treated as a strategy conclusion. The path fix changed execution plumbing only, not the hypothesis, signal parameters, costs or gates. Both subsequent hosted runs completed with the same decision and metrics.

## Primary final out-of-sample results

Frozen method: rank the 24 spot-candle markets by trailing 24-hour return; construct lagged pair-relative returns between top gainer and bottom loser; use a 4-hour synthetic relative-return context and a 15-minute sign change; enter the selected pair on the next bar; keep the pair fixed while trading. Synthetic trade PnL is a relative-log-return proxy with 1.0x gross exposure, not exact perpetual account equity.

| Metric | 5 bps slippage per side per leg | 10 bps | 20 bps |
|---|---:|---:|---:|
| Final-OOS net total return | **−90.29%** | −95.02% | −98.69% |
| Annualized Sharpe | −5.254 | −6.644 | **−9.204** |
| Maximum drawdown | −91.59% | −95.43% | −98.75% |
| Closed trades | 665 | 665 | 665 |
| Mean net trade return | **−0.333%** | −0.433% | −0.633% |
| Win rate | 29.62% | 26.92% | 23.01% |

At base costs, the first and second final-OOS halves returned −65.16% and −70.03%, respectively. Their base-cost trade expectancies were −0.295% and −0.352%. Each half independently failed the required positive-return gate.

The hourly-breakout comparator also failed badly: final-OOS net return was −87.01% at 5 bps, −92.40% at 10 bps, and −97.40% at 20 bps; base-cost expectancy was −0.366%. The primary therefore did not demonstrate a useful positive edge or a statistically supported advantage over the comparator.

## Statistical and event-study evidence

Using 2,000 five-day moving-block bootstrap replications on the final-OOS daily series:

- Primary mean daily-return 95% interval: **[−1.141%, −0.468%]**. This is wholly negative, not above zero.
- Primary-minus-hourly-breakout mean daily-return 95% interval: **[−0.402%, +0.237%]**. This includes zero, so the primary's relative advantage is not established.

A separate daily-spaced formation study fixed the top/bottom pair at formation and measured its next-24-hour relative log return over 1,381 formations. Mean was −0.042%, median −0.298%, and 46.92% of observations were positive. Its block-bootstrap 95% interval was [−0.352%, +0.247%], which includes zero. This does **not** establish a reliable persistence or reversal effect.

The independently reconciled evidence contained zero discrepancies in primary/comparator trade counts, mean trade returns or final daily equity versus summary metrics across the tested segments and cost scenarios.

## Gate outcome

| Preregistered gate | Result |
|---|---|
| Pinned data evidence valid | PASS |
| At least 50 final-OOS primary trades | PASS — 665 |
| Positive base-cost absolute expectancy | **FAIL** |
| Positive base-cost final-OOS total return | **FAIL** |
| Base-cost daily-return bootstrap lower bound above zero | **FAIL** |
| Primary beats hourly breakout with bootstrap lower bound above zero | **FAIL** |
| Positive final-OOS return at 20 bps slippage | **FAIL** |
| Positive Sharpe at 20 bps slippage | **FAIL** |
| Positive returns in both final-OOS halves | **FAIL** |
| No recorded margin breach/account-ruin event in proxy | PASS |
| Baseline and expectancy metrics present | PASS |

The required minimum trade count was met, so the preregistered decision is a rejection rather than insufficient evidence.

## Limitations and next action

The history had been inspected in an earlier, unrelated experiment, so this result is exploratory, not independent confirmation. The fixed present-day 24-market universe has listing/survivorship bias. Spot OHLCV does not model perpetual funding, spot-perpetual basis, borrow, real fills, exchange margin or liquidation. The synthetic relative-log-PnL proxy can reject this rule under its own specified assumptions, but cannot prove anything about actual futures-account PnL without a suitable execution model.

**Next action:** preserve this result and move to a materially different mechanism or instrument construction. Do not tune this signal against the same final-OOS window. Any future candidate that passes its own frozen gates must still undergo a separately preregistered, forward-only confirmation. Live trading remains prohibited.
