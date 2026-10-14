# HARMONY-ACCEL-LEADER-002 — Preregistered Universe-Breadth Follow-up

## Objective and single permitted change

Test whether cross-sectional breadth—not a changed signal or tuned stop—was the main reason HARMONY-ACCEL-LEADER-001 produced only seven closed trades in final OOS and failed higher-cost profitability gates.

The **only strategy change** is expanding the fixed universe from eight symbols to this frozen list of 24 OKX spot markets:

BTC-USDT, ETH-USDT, LTC-USDT, XRP-USDT, BNB-USDT, BCH-USDT, ADA-USDT, DOGE-USDT, SOL-USDT, LINK-USDT, DOT-USDT, AVAX-USDT, TRX-USDT, ETC-USDT, XLM-USDT, ATOM-USDT, FIL-USDT, EOS-USDT, UNI-USDT, AAVE-USDT, ALGO-USDT, NEAR-USDT, MANA-USDT, SAND-USDT.

Do not replace a symbol if it is delisted, absent, or has inadequate history. Write a blocked-data report instead.

## Hypothesis and falsifiable improvement

The larger panel should create more fresh normalized-acceleration leader events without diluting the underlying trade edge. Relative to HARMONY-ACCEL-LEADER-001's seven final-OOS trades, the new candidate must:
1. Close at least **50 final-OOS trades**;
2. Have **positive net per-trade expectancy at 20 bps slippage per side**;
3. Have **positive annualized net OOS Sharpe at 20 bps per side**; and
4. Deliver **positive net total return in both halves** of final OOS.

Failure of any of these gates falsifies the pre-registered improvement claim; insufficient data or too few trades is not evidence of profitability. Passing only makes the candidate eligible for future-only paper confirmation, not live use.

## Frozen signal and execution (unchanged from 001)

- Completed 15-minute candles; no look-ahead.
- One-hour log return is the sum of the latest four 15-minute log returns.
- Acceleration is current one-hour log return minus the one-hour log return four bars earlier.
- Normalize acceleration by the population standard deviation of the previous 168 completed hourly returns.
- Choose the highest normalized-acceleration leader, require score >= 1.0 and positive latest one-hour log return, and reject symbols that were leader in any of the prior 96 completed 15-minute observations.
- Record leader history on every completed bar even when the trade filters fail.
- Enter at next bar open; long-only spot, one position at a time, no leverage, notional based on NAV.
- Use the same monotonic 3 x ATR(14) stop on 15-minute bars, same conservative stop ordering and gap-fill logic, same forced terminal liquidation.
- Charge 5 bps taker fee per side and stress 5, 10 and 20 bps slippage per side.
- Preserve the same three chronological segments (60/20/20), fixed bootstrap settings and all four frozen comparator definitions from 001. No grid search and no final-OOS-based changes.

## Data and reliability gates

Fetch only confirmed OKX spot candles from 2023-01-01. Preserve normalized candle files, SHA-256 hashes, API coverage, source/code/config versions, trades, daily equity, detailed metrics and the blocked/failed report if any. Reject duplicate/nonmonotonic timestamps, invalid OHLC, material gaps, stale tails, and insufficient common history. Never forward-fill or silently substitute data. Unit-test feature math, strict freshness boundaries, monotonic stops, gap-through-stop handling and next-bar execution.

## Interpretation and bias disclosure

This experiment was chosen after observing HARMONY-ACCEL-LEADER-001, so the exercise is separately preregistered and has a frozen configuration, but is **not an independent statistical confirmation**. Its historical final-OOS window must be described as exploratory in this context. A positive result must be followed by a predeclared future-only paper trial before any live-deployment consideration. The 24-symbol present-day list is not a point-in-time universe and cannot support a market-wide or survivorship-free claim.

## Completion requirements

Run via its isolated GitHub Actions workflow. Preserve the artifact and digest. Update the Contraction tracking record with the preregistration commit, runner/spec hashes, Actions run ID, artifact digest, metrics, gates and disposition. Keep the branch isolated and PR in draft; do not merge to main or modify HARMONY-ACCEL-LEADER-001 artifacts.
