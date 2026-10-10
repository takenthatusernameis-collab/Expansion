# HARMONY PEPEUSDT.P NYSE-session backtest

- Experiment: HARMONY-PEPE-SESSION-001
- Instrument: PEPE-USDT-SWAP (TradingView PEPEUSDT.P, OKX).
- Price data: 2023-05-03T03:55:00+00:00 to 2026-10-10T03:50:00+00:00; 361,728 confirmed 5-minute candles.
- Canonical OHLCV SHA-256: 2a55f9d9610ea61fce036bfd87f2e0bb5643a561f15d17cff265508760b8e493
- Sessions: 864 complete NYSE regular sessions, 78 five-minute bars each.
- Chronological splits: discovery 518 through 2025-05-23; validation 172 through 2026-01-30; final OOS 174 from 2026-02-02 through 2026-10-09.
- Funding history coverage: 8.3% of sessions by date. OKX documents that this endpoint returns funding history for up to three months; older sessions cannot be fully funding-adjusted from this source.

## Frozen methods

ORB30: 30-minute opening-range breakout. VWAP_TREND: session VWAP plus EMA(9/21). VWAP_REVERSION: rolling 20-bar VWAP-deviation z-score, fixed +/-2 entry and zero-cross exit. MOM60: direction of the prior 60-minute return.
Signals use completed-bar closes and execute at the next 5-minute open. Positions are forced flat at the 16:00 New York session close. Only full NYSE regular sessions are retained; holidays, early-close sessions, and incomplete sessions are excluded.

## Final out-of-sample results at base costs

| Candidate | Net OOS return | OOS Sharpe | OOS max DD | OOS trades | Severe-slippage Sharpe | Promotion decision |
|---|---:|---:|---:|---:|---:|---|
| ORB30 | -36.72823531830467% | -1.3190525735967167 | -51.51028128253179% | 241 | -3.571083992724736 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |
| VWAP_TREND | -92.46036129198278% | -7.462198072231504 | -92.80536719389434% | 1104 | -14.06916788028505 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |
| VWAP_REVERSION | -44.084646461408425% | -2.5352861576453174 | -44.92421376939162% | 386 | -8.489085885149377 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |
| MOM60 | -97.15976487366899% | -11.41917508182196 | -97.29109725934507% | 1641 | -21.595343004596444 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |

## Interpretation guardrails

No strategy is promoted unless every fixed gate passes. Funding coverage below 95% automatically blocks promotion regardless of apparent price-only or partially funded performance.
Base costs are a 5 bps taker fee plus 5 bps slippage per fill; stress cases use 10 and 20 bps slippage per fill. Funding uses official OKX realized rates only during the endpoint's available history.
A good OHLCV result may disappear under tick-level fills, spread, market impact, fee-tier differences, complete funding history, or later out-of-sample data.
