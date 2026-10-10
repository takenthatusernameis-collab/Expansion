# HARMONY PEPEUSDT.P NYSE-session backtest v2

- Experiment: HARMONY-PEPE-SESSION-002
- Instrument: PEPE-USDT-SWAP (TradingView PEPEUSDT.P, OKX).
- Price data: 2023-05-03T03:55:00+00:00 to 2026-10-10T04:20:00+00:00; 361,734 confirmed 5-minute candles.
- Canonical OHLCV SHA-256: fb60a1206cba6a10a786ee4a6413a48209e9758d33a28ff0c07db1240b1f97d3
- Sessions: 856 complete NYSE regular sessions, 78 five-minute bars each.
- Chronological splits: discovery 513 through 2025-05-23; validation 171 through 2026-02-03; final OOS 172 from 2026-02-04 through 2026-10-09.
- Funding history coverage: 8.4% of sessions by date. OKX documents that this endpoint returns funding history for up to three months; older sessions cannot be fully funding-adjusted from this source.

## Frozen methods

ORB30: 30-minute opening-range breakout. VWAP_TREND: session VWAP plus EMA(9/21). VWAP_REVERSION: rolling 20-bar VWAP-deviation z-score, fixed +/-2 entry and zero-cross exit. MOM60: direction of the prior 60-minute return.
Signals use completed-bar closes and execute at the next 5-minute open. Positions are forced flat at the 16:00 New York session close. Only NYSE dates whose scheduled open is 09:30 and scheduled close is 16:00 New York time are retained; holidays, early-close sessions, and incomplete sessions are excluded.

## Final out-of-sample results at base costs

| Candidate | Net OOS return | OOS Sharpe | OOS max DD | OOS trades | Severe-slippage Sharpe | Promotion decision |
|---|---:|---:|---:|---:|---:|---|
| ORB30 | -30.455842507154294% | -1.0348166903632299 | -50.86109117330122% | 237 | -3.3166243114694494 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |
| VWAP_TREND | -92.43481058685967% | -7.513629186257767 | -92.80536719389433% | 1095 | -14.124763582283292 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |
| VWAP_REVERSION | -44.78404210703454% | -2.614856643225924 | -44.924213769391564% | 381 | -8.541954360154962 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |
| MOM60 | -97.25184484791978% | -11.896539639581885 | -97.29109725934507% | 1627 | -22.15779118003917 | NON_PROMOTABLE_INCOMPLETE_FUNDING_HISTORY |

## Interpretation guardrails

No strategy is promoted unless every fixed gate passes. Funding coverage below 95% automatically blocks promotion regardless of apparent price-only or partially funded performance.
Base costs are a 5 bps taker fee plus 5 bps slippage per fill; stress cases use 10 and 20 bps slippage per fill. Funding uses official OKX realized rates only during the endpoint's available history.
A good OHLCV result may disappear under tick-level fills, spread, market impact, fee-tier differences, complete funding history, or later out-of-sample data.
