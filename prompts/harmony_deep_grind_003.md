# Harmony Deep Grind 003 — Highest-Information Frozen Research Session

## Mission

Run the deepest bounded Harmony research session available from the existing frozen research machinery.

Optimize for information gain, falsification power, reproducibility, and research-process improvement—not for finding a positive backtest.

Primary questions:
1. Is any already-preregistered mechanism differentiated and temporally durable?
2. Are apparent contradictions economic, accounting/implementation, data-provenance, or genuine?
3. What is the smallest decisive next experiment with the highest expected uncertainty reduction?

## Immutable boundary

- Candidate definitions, parameters, horizons, universes, rankings, directions, weights, rebalance schedules, signal timing, costs, funding treatment, and acceptance gates are frozen.
- No post-hoc search, tuning, descendant creation, or candidate rescue.
- No data after 2025-10-31 and no final-holdout access, inference, or selection.
- Never weaken a test, tolerance, cost assumption, data rule, or integrity check because a result is inconvenient.
- A failure is information and must remain visible.

## Accounting integrity gate

Every protocol must distinguish:
- gross return before trading costs;
- net return after trading costs;
- funding PnL;
- turnover and transaction-cost drag;
- terminal liquidation cost.

Cross-protocol comparisons and placebo tests must use comparable net accounting unless explicitly labeled otherwise.

Never compare a gross Sharpe/CAGR with a net cumulative return as though they were the same quantity.

## Frozen research components

### 1. FIN-0012 authoritative durability

Run the accepted-engine reproduction and durability audit.

Require:
- exact reproduction within 1e-9;
- OOS halves and quarters;
- residual diagnostics;
- 1.0x / 1.5x / 2.0x cost stress;
- timing concentration;
- explicit durability classification.

### 2. Reconciled FIN-0012 / FIN-0024 alpha autopsy

Run the accounting-reconciled alpha autopsy.

For both candidates report:
- gross metrics;
- net metrics;
- net BTC/equal-weight attribution;
- rolling residual diagnostics;
- benchmark-relative net performance;
- deterministic matched placebo with identical net accounting;
- turnover, transaction costs, funding;
- internal accounting checks.

For FIN-0012, reconcile the net-equity result against the authoritative durability result at 1e-9. Any larger difference must be diagnosed and explicitly classified before economic interpretation.

### 3. HARMONY-CAMPAIGN-002 funding-carry family

Run every frozen candidate across all six chronological blocks and 1.0x / 1.5x / 2.0x costs with native funding accounting.

No post-hoc narrowing.

### 4. HARMONY-DEEP-DISCOVERY-BATCH-007

Run the frozen low-volatility mechanism over the verified common eight-symbol history with OOS halves, benchmarks, and cost stress.

## Cross-protocol reconciliation

After components complete, reconcile:
1. implementation identity;
2. gross-versus-net semantics;
3. funding timing and aggregation;
4. transaction-cost timing and terminal liquidation;
5. signal timing and rebalance anchoring;
6. benchmark definitions;
7. temporal durability;
8. cost durability;
9. placebo/null calibration;
10. redundancy;
11. contradictions.

Every disagreement gets exactly one classification:
- economically meaningful;
- accounting/implementation artifact;
- data/provenance issue;
- unresolved.

Never average, cherry-pick, or select the more attractive implementation.

## Evidence hierarchy

Use:
1. exact accepted-engine gates;
2. independently reproduced frozen calculations;
3. component-native diagnostics with explicit accounting semantics;
4. synthesis.

A narrative cannot override a failing machine gate.

## Meta-research objective

For each surviving uncertainty record:
- consequence if wrong;
- evidence quality;
- cheapest decisive test;
- expected information gain;
- overfitting/evaluator-gaming risk.

Choose the smallest preregisterable test with the highest expected uncertainty reduction.

Prefer falsification, reconciliation, and process improvement over more optimization.

## Execution discipline

- Continue independent components after isolated failures when safe.
- Persist stdout/stderr, return codes, workflow/source SHA, data provenance, and SHA-256 evidence inventory.
- Validate JSON structure and integrity flags.
- Any holdout access, candidate mutation, parameter/universe/direction search, or unexplained accounting mismatch makes the session fail closed.
- Do not broaden a failed candidate or change a frozen mechanism during this session.
- Component artifacts and machine gates are authoritative; generated narrative is secondary.

## Required outputs

Create `artifacts/HARMONY-DEEP-GRIND-003/`:
- `session_report.md`
- `session_summary.json`
- `component_status.json`
- `input_manifest.json`
- `evidence_inventory.json`
- `cross_protocol_reconciliation.json`
- `session_logs/` with stdout/stderr pairs.

End the report with exactly:
- SUPPORTED EVIDENCE
- MIXED / INCONCLUSIVE EVIDENCE
- REJECTED / ARCHIVED EVIDENCE
- HIGHEST-VALUE NEXT TEST

No trading recommendation is implied.
