# HARMONY-EXTREME-RELATIVE-SPREAD-001 — one-shot research prompt

## Prompt improvement history

### Version 1 — initial question
Test whether a synthetic spread between the biggest 24-hour crypto gainer and loser can be traded with a 4-hour spread context and faster 15-minute turns.

**Defects identified:** no exact signal timing, pair-selection timing, transaction-cost definition, benchmark, sample split, data pin, or decision policy. That version could easily introduce lookahead and mistake a profitable chart for a tradable strategy.

### Version 2 — reproducible research plan
Freeze a 24-market universe and a pinned 15-minute candle artifact. Build the relative-spread return using the pair selected *before* each return interval, use a 4-hour state plus 15-minute direction change as the primary signal, compare it with a simple hourly breakout, and model both long and short legs with fee/slippage stress.

**Defects identified:** spot candle history cannot establish perpetual-futures funding or execution; historical holdout data were already examined for another experiment; and statistical significance, minimum trades, and absolute profitability still needed explicit independent gates.

### Version 3 — final executable brief
Execute the separate HARMONY-EXTREME-RELATIVE-SPREAD-001 experiment in Expansion. Do not stop after writing a plan, tests, or a workflow. Run the actual backtest from the pinned evidence, independently reconcile outputs, and give one evidence-backed decision. Do not tune after viewing results.

## Mission and scope
You are the research engineer for the Expansion experiment-execution repository. Establish whether the fixed relative-spread hypothesis has sufficient historical evidence to justify a *separately preregistered, forward-only confirmation*. A valid negative result is a successful research outcome. A workflow passing is not evidence of alpha.

## Step 1 — inspect and freeze
1. Review the existing experiment-runner conventions and the prior pinned OKX preflight evidence before coding.
2. Use only the following artifact source: workflow run 38643611024, artifact 11934457001, preflight commit a7da33233e1567db91554d819a39957d084d8fe6. Recheck summary SHA-256 0074d5f245fa4323d7370491d320a756c50bcbb6233b3447a2ef62f2cae6c7fc and every canonical candle hash. The published source artifact ZIP hash is 9e20b58052446be9574ac77f4d9d3fef5f1c22e5c7f1dfb5ec94a309e183edfd. Since the Actions download step extracts the artifact before the runner sees it, do not claim the runner re-hashed the ZIP; the runtime integrity gate is the pinned run/artifact identity, verified embedded-summary hash, and every canonical uncompressed candle hash. Candidate universe spec hash is 8f561ef21f587dbd1712dd500a891ba58abe891a35c1694e81e2a8ef5bc94186.
3. Freeze these 24 symbols exactly: BTC, ETH, LTC, XRP, BNB, BCH, ADA, DOGE, SOL, LINK, DOT, AVAX, TRX, ETC, XLM, ATOM, FIL, OP, UNI, AAVE, ALGO, NEAR, MANA, SAND, each quoted against USDT. Do not refetch, forward-fill, change timeframes, or replace missing markets. Fail closed before strategy metrics if evidence is invalid.

## Step 2 — hypothesis and strategy frozen before execution
The hypothesis is that the relative behavior of extreme 24-hour movers contains a short-horizon state signal missed by individual-asset indicators. It is unproven.

At each completed 15-minute close:
- Rank all valid symbols by trailing 96-bar close return. Resolve ties by score then symbol name ascending.
- Select the top gainer and bottom loser.
- Construct the synthetic spread interval return ending at bar t using the pair selected at t−1, never the pair discovered only at t. Calculate log return of the previous bar's top leg minus log return of its bottom leg.
- The slow context is the sum of the latest 16 completed synthetic-spread interval returns.
- Primary long-spread entry: the most recent synthetic 15-minute return changes from nonpositive to positive while the 4-hour context is positive. Primary short-spread entry: it changes from nonnegative to negative while the 4-hour context is negative.
- Select the current top/bottom pair at the signal close and enter at the next bar open. Freeze the selected pair for the life of that trade.
- Hold gross notional equal to 1.0 times NAV: 0.5 NAV long and 0.5 NAV short. Permit only one open trade and cap the holding time at 96 bars.
- Use the relative log spread of the two legs and a frozen 3× ATR(14) proxy stop, with a monotonic trailing stop. Check pre-existing stops before ratcheting; model gap/open stops, adverse intrabar spread range and terminal liquidation. State clearly where OHLC bars cannot identify the true intra-bar path.

## Step 3 — baselines, event study and costs
Run a comparably executed hourly breakout baseline: at completed hourly boundaries, trade long when synthetic spread close breaks above all previous 16 spread closes, or short when it breaks below all previous 16; use the same selected pair convention, stop, maximum holding time, gross exposure and costs. Include a flat/no-signal baseline.

Separately estimate whether formed gainer-loser spreads persist or reverse: once every 96 bars, freeze the pair selected at formation and measure the forward 96-bar log relative return. Report sample count, mean, median, fraction positive and a five-day moving-block bootstrap 95% interval. This is a descriptive event study, not a parameter-selection tool.

Charge 5 bps fees per side per leg and stress 5, 10 and 20 bps slippage per side per leg. For a 1.0 gross-notional 50/50 long-short spread, the approximate round-trip portfolio cost is 2 × (fee bps + slippage bps) / 10,000 of entry NAV. Include costs on both legs and both entry/exit sides. Funding, perp/spot basis, liquidation, borrow and actual perpetual execution are unavailable in the pinned spot candles; do not claim they are included or that a positive proxy result is live-tradeable.

## Step 4 — validation and decisive gates
Use a strictly chronological 60% discovery / 20% validation / 20% final-OOS split. Do not optimize parameters; do not use discovery/validation or final-OOS results to alter this frozen rule; do not restart with alternate values if it fails. Within final OOS also report each half separately. Do not carry positions or cash between segments; force-liquidate at segment boundaries.

At 5 bps slippage, the primary must have at least 50 closed final-OOS trades, positive absolute mean net trade return, positive final-OOS total return, and a 2,000-replication, five-day moving-block bootstrap 95% confidence interval for mean daily return whose lower bound is above zero. The lower bound of the paired moving-block 95% confidence interval for primary-minus-hourly-breakout mean daily return must also exceed zero. At 20 bps slippage, total return and annualized Sharpe must both be positive. Primary total return must be positive in both final-OOS halves at 5 bps. Treat each condition as an explicit boolean; missing values fail. Statistical uncertainty is not a substitute for absolute economic profitability.

Because this historical dataset was already inspected for a different experiment, mark the entire result exploratory rather than independent confirmation. The fixed current-symbol list also has listing/survivorship bias. Even if every historical gate passes, allow only a separately preregistered forward-only confirmation; live orders are prohibited.

Decision rules:
- Invalid pinned evidence or a technical inability to produce valid metrics: BLOCKED, with no strategy conclusion.
- Valid evidence but fewer than 50 primary final-OOS trades: INCONCLUSIVE.
- At least 50 trades but any fixed economic/statistical gate fails: REJECT the historical edge claim.
- All gates pass: ELIGIBLE FOR FUTURE-ONLY CONFIRMATION, not approved for live trading.

## Step 5 — execute, verify and preserve
Create a separate branch from main; never modify or merge into main. Implement a standalone pinned data loader, explicit experiment spec, runnable backtest, unit tests and isolated GitHub Actions workflow. Run compilation and tests, then run the backtest on the actual pinned artifact. Upload data manifest, run manifest, complete trade ledger, daily equity, event-study results, summary and readable report. Independently reconcile summary totals against the ledger/equity artifact and verify all gates are represented and evaluated. A valid rejected result must still make the workflow green; blocked evidence and technical errors must fail the workflow. Open a draft PR after the run. Finish with the exact decision, quantitative drivers, run/PR/artifact links, caveats, and one recommended next action. Never describe CI success as strategy success.