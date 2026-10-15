# MOM121 Free Cloud Data Acceptance Audit 001

**Purpose:** unblock the next stage of the frozen MOM121 research using an available no-subscription-cost cloud route, without weakening Expansion's accepted data contract or moving licensed raw data into GitHub.

**This is not a strategy backtest and produces no alpha/performance claim.** It is an initial feasibility/audit pilot to run inside QuantConnect Cloud.

## Why this path

QuantConnect documents free cloud access to US equity data and point-in-time fundamentals from January 1998, plus a US equity security master covering ticker changes, mergers, corporate actions and delistings. Its official historical-fundamentals documentation specifically describes requests returning fundamentals for all US equities, including delisted companies.

- [US Fundamental Data](https://www.quantconnect.com/data/morning-star-us-fundamentals)
- [US Equity Security Master](https://www.quantconnect.com/data/quantconnect-us-equity-security-master)
- [Historical fundamental data in algorithms](https://www.quantconnect.com/docs/v2/writing-algorithms/datasets/morningstar/us-fundamental-data)
- [US equity historical data and delistings](https://www.quantconnect.com/docs/v2/writing-algorithms/historical-data/asset-classes/us-equities)

The platform's current fundamentals feed was rebuilt for the full history in September 2026, so its revision must be recorded in the run evidence. This cloud route is distinct from Expansion's existing data-archive workflow: it must not be presented as a completed Expansion run, and no raw/licensed security history should be copied into GitHub artifacts.

## How to run

1. Sign in to a free QuantConnect account and create a Python Cloud Algorithm project.
2. Copy `main.py` from this directory into that project's main algorithm file.
3. Run a backtest for the configured dates (1 January 1998 through 31 August 2026). This code makes no orders.
4. Inspect the logs for `MOM121_DATA_AUDIT_JSON=`, `MOM121_CORPORATE_ACTION_AUDIT=`, and `MOM121_AUDIT_TERMINAL=`.
5. Save the QuantConnect project identifier, backtest identifier, algorithm code hash, LEAN/engine version, start/end dates, and explicit fundamentals-feed revision in the private research log. Share only allowed aggregate diagnostics and logs, not licensed raw data.

## Acceptance questions

- Does historical fundamentals history return a materially broad point-in-time snapshot at the requested date?
- Are price, market capitalization, security type, share-class status, and exchange identifier populated sufficiently for historical universe reconstruction?
- Can common stock be separated from preferreds, units, ADRs, ETFs, warrants, and other excluded instruments without relying on today's ticker list?
- Do historical observations include both active and inactive/delisted companies in a manner appropriate to their historical dates?
- Are the exchange identifier codes correctly mapped? The set in `main.py` is explicitly a diagnostic assumption, not an approved universe mapping.
- Are representative bankruptcy and merger/delisting cases visible in the data stream?

**Do not interpret a pass on these questions as proof of correct total-return or terminal-delisting-return accounting.** Observing a delisting event is not enough to show whether final proceeds, settlement date, distributions and terminal returns are applied economically correctly. The pilot must then be extended to a case-by-case terminal-event reconciliation before the research can become confirmatory.

## Frozen MOM121 rules remain unchanged

The primary experiment remains defined in `experiments/HARMONY-MOM121-DEEP-GRIND-001.yaml`: USD 300 million market-cap ceiling; USD 1 price floor; monthly equal-weight top quintile; 12–1 momentum timing as frozen in the specification; equal-weight benchmark from the same eligible universe; one-way costs of 25/50/100/200 bps; 60/20/20 chronology; at least 120 usable months and 36 final-test months; 2,000 block-bootstrap replications; 250 random-ranking placebos; seed 121; capacity analysis at USD 100k / USD 1m / USD 10m.

Do not change the existing experiment status from `blocked_data_gate` until its required source fields, data coverage, return/delisting accounting and point-in-time universe are independently evidenced. Do not insert raw QuantConnect data into public Expansion files or Actions artifacts.

## Current status

`prepared_not_executed_quantconnect_cloud_required`

No QuantConnect account is connected to this workflow, so this repository change cannot launch the cloud backtest itself. The next concrete checkpoint is the cloud audit evidence above; if it passes, a separate reviewed implementation can translate the frozen strategy into a full cloud backtest and reconcile its returns against the existing Expansion contract.
