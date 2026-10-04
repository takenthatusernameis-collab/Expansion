# Harmony Deep Grind 002 — Integrity-First, Accounting-Reconciled Research Session

## Mission

Run the deepest bounded Harmony research session that can be executed from the already-frozen research machinery without turning the session into a parameter-search exercise.

The objective is maximum information gain about:
1. whether any already-preregistered mechanism is genuinely differentiated and durable;
2. whether apparently contradictory evidence is caused by economics, accounting, timing, or implementation semantics;
3. which single next preregistered experiment would most efficiently reduce the largest surviving uncertainty.

Do not manufacture a winner. Do not optimize a candidate because this session produces an attractive number.

## Immutable boundary

- Treat candidate definitions, parameters, horizons, universes, ranking rules, directions, weights, rebalance schedules, signal timing, and acceptance gates as frozen.
- No post-hoc parameter search, candidate search, universe search, direction search, or threshold tuning.
- No data after 2025-10-31.
- No final-holdout access, inference, or selection.
- No weakening of tests, tolerances, accounting rules, costs, data filters, or integrity checks.
- A failed reconciliation is evidence about the research process and must remain visible.

## Accounting semantics are part of the protocol

Every performance result must explicitly distinguish:

- gross economic return before trading costs;
- net return after transaction costs;
- funding carry/PnL;
- turnover and transaction-cost drag;
- any terminal liquidation cost.

Cross-protocol comparisons and placebo tests must use the same net-accounting definition unless explicitly labeled otherwise.

Never compare a gross Sharpe/CAGR to a net cumulative return as though they were the same statistic.

## Frozen components

### 1. FIN-0012 authoritative durability

Run the exact accepted-engine FIN-0012 durability audit. Its reproduction gate remains authoritative.

Required:
- exact accepted metrics within 1e-9;
- temporal halves and quarters;
- residual diagnostics;
- 1.0x / 1.5x / 2.0x cost stress;
- concentration analysis;
- explicit durability classification.

### 2. Accounting-reconciled alpha autopsy

Run the FIN-0012 / FIN-0024 alpha autopsy with corrected accounting semantics.

Required for both candidates:
- gross metrics;
- net metrics;
- BTC/equal-weight net attribution;
- residual diagnostics;
- benchmark-relative net performance;
- deterministic matched placebo using the same net accounting;
- turnover, costs, and funding;
- internal accounting reconciliation.

For FIN-0012, independently reconcile the net result to the authoritative durability output. A mismatch above the declared numerical tolerance is a hard integrity failure, not a reason to reinterpret the strategy.

### 3. Funding-carry family

Run the entire frozen HARMONY-CAMPAIGN-002 family:
- all four candidates;
- six chronological blocks;
- 1.0x / 1.5x / 2.0x cost stress;
- native funding accounting;
- no post-hoc narrowing.

### 4. Low-volatility deep discovery

Run frozen HARMONY-DEEP-DISCOVERY-BATCH-007:
- verified common eight-symbol history;
- frozen monthly mechanism;
- OOS evaluation;
- temporal halves;
- benchmarks;
- cost stress.

## Cross-protocol synthesis

After all components finish, reconcile:

1. implementation identity;
2. gross versus net accounting;
3. funding timing and aggregation;
4. transaction-cost timing and terminal liquidation;
5. signal timing and rebalance anchoring;
6. benchmark definitions;
7. temporal durability;
8. cost durability;
9. placebo/null calibration;
10. redundancy and differentiated evidence;
11. contradictions and invalidating evidence.

A numerical disagreement must be classified as one of:
- economically meaningful;
- accounting/implementation artifact;
- data/provenance issue;
- unresolved.

Do not silently average or choose the more attractive implementation.

## Meta-research

For every unresolved issue, record:
- consequence if wrong;
- current evidence quality;
- cheapest decisive test;
- expected information gain;
- overfitting/evaluator-gaming risk.

Select the smallest next test with the highest expected uncertainty reduction under the frozen research constraints.

Prefer falsification and reconciliation over further optimization.

## Execution discipline

- Run all independent components even when one fails, unless continuing would violate integrity.
- Persist exact stdout/stderr, exit codes, source/workflow SHA, input provenance, and artifact SHA-256 hashes.
- Validate JSON outputs and declared holdout/mutation flags.
- Treat any holdout access, candidate mutation, parameter search, universe search, or direction search as session failure.
- Treat any accounting-reconciliation failure as session failure.
- Never declare a research result from a model-generated summary alone; persisted component artifacts are authoritative.

## Required outputs

Create `artifacts/HARMONY-DEEP-GRIND-002/` containing:
- `session_report.md`
- `session_summary.json`
- `component_status.json`
- `input_manifest.json`
- `evidence_inventory.json`
- `session_logs/` with one stdout/stderr pair per component;
- a machine-readable cross-protocol reconciliation record.

The final report must end with exactly these sections:
- SUPPORTED EVIDENCE
- MIXED / INCONCLUSIVE EVIDENCE
- REJECTED / ARCHIVED EVIDENCE
- HIGHEST-VALUE NEXT TEST

No trading recommendation is implied.
