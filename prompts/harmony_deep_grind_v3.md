# Harmony Deep Grind v3 — Measurement Integrity, Durability, and Research-Process Session

## Mission

Run the deepest deterministic Harmony research session available from the existing frozen research machinery, with the primary objective of increasing trustworthy information gain rather than maximizing headline performance.

The session must distinguish:
- gross signal diagnostics;
- realized net/executable performance;
- attribution diagnostics;
- validation evidence;
- promotion decisions.

The highest-priority unresolved issue is the previously observed FIN-0012 accounting discrepancy between the authoritative durability engine and the independent alpha-autopsy implementation. The current evidence indicates that native funding settlement may be applied event-by-event by the accepted engine while an independent implementation aggregates same-day funding before compounding. Resolve this at the daily equity-path level before interpreting derived alpha-attribution or placebo evidence as though it were authoritative.

## Immutable boundary

- Do not optimize, retune, mutate, or promote any candidate.
- Do not change candidate parameters, universe, direction, ranking, horizon, rebalance cadence, signal timing, weights, cost assumptions, funding treatment, or acceptance gates merely because of observed results.
- Do not access, infer, or derive any data after 2025-10-31.
- Do not use final holdout information for selection, interpretation, or tuning.
- Treat the accepted FIN-0012 engine reproduction as the accounting authority.
- A mismatch is evidence to explain, not permission to weaken the authority.
- Preserve failed, mixed, or inconclusive outcomes.
- Never call a descriptive robustness pattern independent OOS validation unless its protocol supports that claim.

## Frozen research components

Run all of these in one bounded session:

1. FIN-0012 reproducibility-first durability audit.
2. FIN-0012 / FIN-0024 alpha autopsy with explicit gross-vs-net accounting layers.
3. A deterministic FIN-0012 reconciliation audit comparing alpha-autopsy net metrics, execution accounting, and the full 528-row daily equity path against the authoritative durability trace.
4. HARMONY-CAMPAIGN-002 funding-carry family: all four frozen candidates, six chronological blocks, 1.0x / 1.5x / 2.0x costs.
5. HARMONY-DEEP-DISCOVERY-BATCH-007 low-volatility deep-history validation.

The later components must never be altered based on earlier component results.

## Iteration refinement: reconciliation as a hard dependency

The reconciliation is not merely a post-hoc report. It is a measurement-contract test between two implementations of the same frozen experiment. If it fails, downstream alpha-autopsy interpretation must be marked unresolved even when the headline performance is attractive.

## FIN-0012 reconciliation gate

The reconciliation stage must answer, with exact evidence:

1. Do alpha-autopsy net metrics reproduce the authoritative FIN-0012 trace within 1e-9?
2. Do observed execution quantities agree where the protocols are intended to be identical?
3. If gross metrics differ from net metrics, is the difference explicitly attributable to transaction-cost/funding accounting rather than hidden signal differences?
4. Does the alpha-autopsy artifact clearly label every metric with its accounting layer?
5. Are placebo percentile statistics computed on the same accounting layer as the quantity being compared?
6. Does the reconciliation fail closed if the required authority files are missing, ambiguous, or materially inconsistent?
7. Are funding events settled in the same native event-by-event order as the authoritative engine?
8. Does the full daily FIN-0012 equity path reproduce within 1e-9 at every observation, not merely at the terminal value?
9. Is any remaining discrepancy explicitly localized to the first divergent date and accounting operation before interpretation?
10. Can every material discrepancy be classified as accounting-layer, signal-timing, data, funding, cost, or implementation error without speculation?
11. Does the corrected diagnostic preserve the authority of the accepted engine rather than changing it to make the numbers agree?
12. Does the reconciliation independently recompute recorded placebo percentiles from persisted trial-level null results rather than trusting the summary field?

No downstream interpretation may treat an unreconciled alpha-autopsy metric as authoritative.

## Execution ordering

Run FIN-0012 durability first, then alpha autopsy, then reconciliation, then the two independent research families. The ordering is for dependency and evidence control only: no result may modify the frozen inputs of a later component.

## Funding-accounting contract

For any component using funding, distinguish additive funding PnL totals from realized equity compounding. Native funding events must be settled in the same order and multiplicative form as the authoritative engine. Matching only the summed funding PnL is insufficient.

## Cross-protocol synthesis

After all components:

- reconcile accounting, data provenance, signal timing, funding, turnover, costs, and benchmark definitions;
- identify contradictions and negative evidence before highlighting attractive results;
- separate supported evidence, mixed/inconclusive evidence, rejected/archived evidence, and unresolved questions;
- identify redundant evidence families;
- rank unresolved uncertainties by consequence, evidence quality, cheapest decisive test, expected information gain, and overfitting/evaluator-gaming risk;
- prefer the smallest preregisterable falsification test with high information gain over another optimization pass.

## Research-engineering integrity

Persist:
- exact stdout/stderr for every component;
- both the pre-reconciliation and post-reconciliation alpha-autopsy artifacts;
- an explicit machine-checkable reconciliation result;

- return codes and timestamps;
- workflow commit SHA;
- data/cache provenance and hashes;
- every generated artifact with SHA-256;
- reconciliation gate results;
- explicit holdout/mutation/search flags.

The final session is PASS only when all required components return 0 and all integrity/reconciliation gates pass. A reconciliation FAIL is a session FAIL even if every strategy backtest returns success. A failed research hypothesis is not itself a session failure; silent inconsistency is.

## Required session artifacts

Create 'artifacts/HARMONY-DEEP-GRIND-003/' containing:
- 'session_report.md'
- 'session_summary.json'
- 'component_status.json'
- 'input_manifest.json'
- 'evidence_inventory.json'
- 'session_logs/'

Also persist the component artifacts for:
- 'HARMONY-FIN-0012-DURABILITY-V2'
- 'HARMONY-ALPHA-AUTOPSY-V1'
- 'HARMONY-RECONCILIATION-002'
- 'HARMONY-CAMPAIGN-002'
- 'HARMONY-DEEP-DISCOVERY-BATCH-007'

The report must end with exactly these headings:
- SUPPORTED EVIDENCE
- MIXED / INCONCLUSIVE EVIDENCE
- REJECTED / ARCHIVED EVIDENCE
- HIGHEST-VALUE NEXT TEST

Before the final synthesis, explicitly answer whether any attractive result changed category because of the reconciliation. Never promote a result merely because the discrepancy was explained.

No trading recommendation is implied.
