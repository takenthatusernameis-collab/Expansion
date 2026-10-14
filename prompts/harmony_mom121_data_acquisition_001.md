# HARMONY MOM121 Data Acquisition — unblock the frozen microcap experiment

**Decision (2026-10-14):** do not rerun the strategy until a source archive passes the existing schema and checksum gates. The fresh Expansion run #9 reached the fail-closed gate and produced no performance metrics. This note is source-selection guidance, not evidence of alpha and not authorization to buy data.

## Candidate sources, ranked for this experiment

### 1. CRSP US Stock Database via an existing WRDS/institutional license — preferred

CRSP documents daily and monthly data for active and inactive U.S. securities, permanent PERMNO/PERMCO identifiers, returns with and without dividends, market capitalization, shares outstanding, volume, security delisting information and corporate actions. This is the closest documented fit to the frozen requirements. Check access through a university, employer or existing WRDS entitlement first; pricing/licensing is not assumed.

Official provider reference: https://www.crsp.org/research/crsp-us-stock-databases/

Required transformation:
- Restrict to the predeclared common-stock security types and approved U.S. exchanges using date-effective security history; never use the current ticker roster as historical membership.
- Use PERMNO as `security_id`; retain effective-dated ticker/name/exchange/type/status metadata.
- Use monthly total-return fields that include distributions. Preserve CRSP delisting returns and verify its version-specific methodology. Where both ordinary and delisting returns are distinct, the combined period return must be calculated under the documented CRSP convention (commonly multiplicative combination where both components are valid), without double-counting.
- Reconstruct market cap using the correct point-in-time price and shares-outstanding units, or use the provided market-cap field. Document date lag and the treatment of negative price codes and missing shares.
- Compute monthly 20-session average dollar volume from past daily observations only; no future rows are allowed.
- Include delisted/bankrupt securities through the terminal return. Record source version, extraction query/date, row counts and SHA-256.

### 2. Sharadar Prices + Daily Fundamentals + Tickers/Actions — alternative only if entitled and terminal returns can be defensibly validated

Official references:
- Prices / active and delisted coverage / adjusted price fields: https://sharadar.com/docs/stocks
- Daily Fundamentals / point-in-time market metrics: https://sharadar.com/docs/daily

Sharadar documents EOD prices for active and delisted securities, adjusted prices, corporate actions, delisting reasons, ticker changes, and daily historical fundamentals including market capitalization. The Tickers table supplies a permanent ticker identifier. These are useful building blocks, but do **not** assume that the stock price table itself is a verified delisting-return series. Before using it, inspect the licensed schemas and sample records and establish how each terminal delisting outcome affects the final total return; if that cannot be established, fail the data gate rather than infer a zero or reuse the last close.

### 3. CRSP-style academic factor/portfolio files, public ticker-history lists, Yahoo/Stooq current-ticker history — not sufficient as a substitute

Portfolio-level factor returns cannot reconstruct this strategy's stock-level point-in-time rankings, market-cap screens or execution costs. Delisting/ticker lists alone do not provide the historical return, market-cap, stable-identity and ADV panel. Current-symbol prices may be exploratory diagnostics only, never confirmatory evidence.

## Archive contract for the existing Expansion workflow

The existing workflow accepts an optional checksum-pinned ZIP through repository Actions secrets `MOM121_DATA_ARCHIVE_URL` and `MOM121_DATA_ARCHIVE_SHA256`. Set these directly in GitHub repository settings; do not put secrets in commits, issue comments, prompts, or chat. The ZIP must contain `manifest.json` and `monthly_panel.csv` at its root. Do not commit licensed raw source files into the public Expansion repository.

Required CSV fields:
`month_end, security_id, ticker, exchange, security_type, is_active_at_month_end, price, market_cap_usd, adv20_usd, total_return`.

The manifest must truthfully assert:
- `point_in_time_universe: true`
- `includes_active_and_delisted_securities: true`
- `includes_delisting_returns_in_total_return: true`
- `total_returns_include_distributions: true`
- `market_cap_is_point_in_time: true`
- `stable_security_id: true`

Also include `files.monthly_panel.csv.sha256` matching the exact CSV bytes. Retain the provider citation, license restrictions, source schema/version, extraction query, time range, counts, universe definitions and known gaps. Confirm the archive and license permit use on a GitHub-hosted runner; never expose raw licensed data in public artifacts. If license terms do not permit this, use an authorized private execution host and alter the delivery path only after explicit authorization.

## Pre-run data acceptance

Before a costly run, validate a small authorized sample, then the full panel:
1. Verify archive SHA-256 and the CSV checksum; reject mismatches.
2. Verify a single stable security identity per monthly row and no duplicate `security_id/month`.
3. Verify histories contain both active and inactive/delisted names and terminal returns are traceable to provider fields / corporate actions.
4. Verify common-stock/exchange mapping, month-end timing, point-in-time market cap, prices, no fabricated forward-filled monthly returns, and past-only ADV.
5. Confirm at least 120 valid strategy months and 36 final-test months under the frozen sample split.
6. Generate the final monthly panel and a manifest with source details and hashes; only then trigger `Harmony MOM121 Deep Grind 001`.

## Important methodological reminders

The primary experiment is fixed: market cap <= USD 300 million, price >= USD 1, prior 11 completed monthly total returns ending before the latest completed monthly return (12–1), top quintile equal-weight long-only, monthly rebalance, matched equal-weight eligible-universe benchmark, and cost scenarios 25/50/100/200 bps one-way traded notional. No tuning to the holdout.

Carry over the Extreme Quant `mom121` failure-mode checks: per-fold cash/position/equity isolation for any fold-specific calculation, no inherited returns in zero-trade folds, explicit signal/execution timing, and independent verification of universe construction. Preserve the distinction between a passed workflow/data-gate test and a completed strategy backtest.
