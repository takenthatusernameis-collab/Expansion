# Harmony Experiment Identity Contract

A real research experiment must be identifiable before its result is interpreted.

The execution contract is:

DATA MANIFEST
  → STRATEGY SPECIFICATION
  → EXPERIMENT SPECIFICATION
  → EXECUTION ARTIFACT
  → TRACKING RECORD

Expansion owns execution.
Contraction owns tracking.

## Data manifest

A manifest identifies the exact source, symbol, timeframe, interval, source locator,
content hash, and row count. The content hash is the identity of the acquired data
object, not a substitute for storing the data itself.

## Strategy specification

A strategy specification identifies the strategy, parameters, signal rule, execution
rule, fee assumption, and slippage assumption.

## Experiment specification

An experiment specification links the data manifest, strategy digest, validation method,
and exact code revision.

The three identities are hashed deterministically so a future run can prove that its
inputs and configuration match the registered experiment.

This contract is deliberately narrow: it does not prescribe a complete research
engine, data vendor, optimizer, or strategy family.
