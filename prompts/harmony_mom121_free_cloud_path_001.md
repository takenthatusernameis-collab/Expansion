# Harmony MOM121 — Free Cloud Data Path 001

Status: `free_cloud_route_identified_not_yet_executed`

## Decision

Use QuantConnect Free Cloud as the first no-subscription-cost execution route. Run the strategy inside QuantConnect's hosted backtesting environment; do not export or redistribute licensed raw price/fundamental data into Expansion or Contraction. This is a *platform execution path*, not a bulk-download source for Harmony's current `monthly_panel.csv` contract.

Official dataset pages list free cloud access to the following data:

- US equities market data from AlgoSeek: listed and delisted securities, historical SIP coverage from January 1998, with OTC excluded: https://www.quantconnect.com/data/algoseek-us-equities
- US coarse universe: historical daily US equity constituents with price and volume, approximately 30,000 securities total / approximately 8,000 per day from January 1998; free in the cloud: https://www.quantconnect.com/data/quantconnect-us-coarse-universe-constituents
- Morningstar US fundamentals: point-in-time historical corporate fundamentals, including delisted tickers, from January 1998; free in the cloud; excludes ETFs, ADRs, and OTC securities: https://www.quantconnect.com/data/morning-star-us-fundamentals
- US equity security master: split, dividend, ticker-change, merger/acquisition, IPO, and delisting events from January 1998; free cloud access: https://www.quantconnect.com/data/quantconnect-us-equity-security-master
- Free organization tier features: https://www.quantconnect.com/docs/v2/cloud-platform/organizations/tier-features

This avoids purchasing CRSP/WRDS or Sharadar simply to make the initial study runnable. It also avoids moving provider-restricted raw data through GitHub Actions artifacts.

## Scope that this free path can test

The implementable target is US exchange-listed microcap common-stock momentum, with a USD 300 million cap ceiling, USD 1 price floor, monthly equal-weight top-quintile selection by prior 12–1 month total return, and a matched eligible-universe benchmark. The platform sources start in January 1998, and the core cloud datasets are advertised as including delisted history/events. The eligible universe is narrower than all US securities because OTC securities are not covered and ADRs/ETFs are excluded by the fundamentals dataset. Enforce the common-stock filter explicitly wherever metadata allows it; document any remaining security-type ambiguity.

Use the cloud engine's as-of simulated data only. Rebalance after the month-end signal is available and apply weights to subsequent returns. Freeze signal, price, market-cap, and ADV timestamps before viewing final-test results. Use the original Expansion experiment's costs, folds, bootstrap/placebo counts, benchmark, and capacity tests; do not search or tune variants.

## Hard gates — do not weaken

1. **Feed/revision pinning.** Record the QuantConnect project ID, algorithm commit/hash, backtest ID, start/end dates, engine branch/version, and data-feed revision. QuantConnect announced that the US fundamentals feed was rebuilt using new feeds for the whole history from September 23, 2026, changing some historical values. On October 14, 2026, explicitly record that the new feed is being used, or deliberately run the old branch only while it remains available and label that run as a distinct data revision. Never mix metrics from the two feeds. Migration notes: https://www.quantconnect.com/docs/v2/writing-algorithms/universes/equity/morningstar-migration

2. **Universe reconstruction.** At each formation date, select from the historical, as-of universe—not from today's symbols. Require listed US common shares, market cap <= USD 300 million, price >= USD 1, and valid point-in-time inputs. Do not silently add ADRs, ETFs, preferred shares, warrants, units, or OTC names.

3. **12–1 timing.** The signal uses 11 completed monthly total returns through the month before the most recent completed month, deliberately skipping that most recent month. Trade only on the next eligible session/month. No current-period prices, revised future information, or survivorship-filtered current ticker list may enter formation.

4. **Corporate actions and delisting outcome.** The security master documents delisting events and symbol changes; that alone does not prove the correct terminal delisting *return/proceeds* are reflected. Before calling results confirmatory, inspect representative bankruptcy, merger/cash-out, ticker-change, and ordinary delisting cases in the engine output. Record the exact liquidation/settlement behavior and compare against conservative terminal-value stress assumptions. If the engine cannot demonstrate economically correct terminal proceeds, status must remain `exploratory_only` or `blocked_data_gate`; do not claim equivalence to CRSP `DLRET`.

5. **Total returns and costs.** Account for splits and distributions exactly once. Use a matched equal-weight eligible-microcap benchmark on identical dates and terminal-event assumptions. Stress one-way transaction costs at 25, 50, 100, and 200 bps on actual absolute traded notional changes. Report turnover, net cost drag, 1% ADV capacity for USD 100k / USD 1m / USD 10m, and terminal liquidation.

6. **Statistical controls.** Preserve the frozen 60% / 20% / 20% chronological split; final test at least 36 months; at least 120 usable months overall; 2,000 circular 12-month block-bootstrap replications; 250 random-ranking placebos; seed 121. No post-hoc parameter changes, universes, or test-period decisions.

7. **No raw-data redistribution.** The raw QuantConnect/AlgoSeek/Morningstar source data must stay inside the licensed cloud environment. Persist only code, source/version identifiers, allowed aggregate statistics, monthly portfolio/benchmark return series if the platform license permits, gate evidence, and report artifacts. Never copy the underlying historical data into the public repository or an unlicensed runner.

## Execution checklist

- Create/use a free QuantConnect account and start a Python algorithm project.
- Confirm that the four datasets above are available to the project as cloud datasets; no API key or vendor subscription is expected for their listed cloud tier, but check the current dataset pricing page before execution.
- Implement the frozen candidate without optimization, alongside the matched benchmark and cost scenarios.
- Save the complete QuantConnect backtest report, platform/backtest identifiers, code hash, data-feed revision, delisting-case audit, monthly strategy/benchmark aggregate returns (only if licensing permits), and all required test results.
- Transfer that *evidence and aggregate results*, not raw vendor data, into Contraction's readout.
- Keep Expansion's existing `HARMONY-MOM121-DEEP-GRIND-001` result at `blocked_data_gate` until its accepted panel is present. A QuantConnect cloud run is a distinct execution route and must not be represented as a completed Expansion backtest.

## Why not use other free stock files as the primary result?

The free Kenneth French portfolios are useful for a broad small-stock/momentum diagnostic but are pre-aggregated portfolios and do not reproduce this custom USD 300m microcap universe. The archived WIKI/PRICES data (roughly 3,199 symbols, 1962–March 2018) can help test data plumbing or run a plainly labeled exploratory proxy, but it does not by itself supply the validated point-in-time capitalization and terminal delisting-return contract required here. Neither substitute may be used to bypass the hard gates.
