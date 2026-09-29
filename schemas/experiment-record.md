# Harmony Experiment Record

This document defines the minimum public-safe contract for an experiment artifact.

The experiment itself **runs on Expansion**.
Its durable research tracking **runs on Contraction**.

## Execution authority

Expansion owns:

- experiment implementation
- data acquisition required for the experiment
- feature construction
- candidate generation
- backtests
- validation runs
- benchmarks and ablations
- experiment-level execution tests
- execution-side artifacts

Contraction owns:

- experiment identity tracking
- provenance and lineage tracking
- outcome/status tracking
- decision history
- result pointers and artifact hashes
- reconciliation and audit records
- durable research state

Contraction must not be used as the experimental compute location.

## Lifecycle

SOURCE → FEATURE → CANDIDATE → BACKTEST → VALIDATION → EVIDENCE

Execution of this lifecycle occurs on Expansion.
Tracking of this lifecycle occurs on Contraction.

## Required identity

- experiment_id
- strategy_id
- data_manifest_id
- code_revision
- created_at
- research_scope

## Required provenance

The record must make it possible to identify:

1. the exact source data;
2. the transformations/features derived from it;
3. the candidate definition;
4. the backtest configuration;
5. the validation procedure;
6. the resulting evidence.

## Required separation

A result must distinguish:

- discovery / training observations;
- validation observations;
- genuine out-of-sample observations;
- any post-hoc analysis.

## Required execution assumptions

Where applicable, explicitly encode:

- fees;
- slippage;
- position-sizing rule;
- leverage;
- funding;
- execution timing;
- liquidity / capacity assumptions;
- missing-data policy.

## Required conclusion status

An experiment must not collapse all outcomes into "success" or "failure".

Recommended states:

- exploratory
- promising
- validated
- rejected
- inconclusive
- blocked

"Promising" is not equivalent to "profitable in live trading".

## Integrity principle

A metric without enough provenance to reproduce or independently inspect its derivation
is evidence of limited value.

This schema is deliberately smaller than a complete research engine. New fields should
be added when the research process demonstrates a durable need for them.
