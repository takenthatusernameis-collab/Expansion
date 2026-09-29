"""Immutable identity contracts for Harmony experiments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class DataManifest:
    source: str
    symbol: str
    timeframe: str
    start: str
    end: str
    source_locator: str
    content_sha256: str
    row_count: int

    def validate(self) -> None:
        if not self.source or not self.symbol or not self.timeframe:
            raise ValueError("source, symbol, and timeframe are required")
        if not self.start or not self.end or not self.source_locator:
            raise ValueError("start, end, and source_locator are required")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a SHA-256 hex digest")
        int(self.content_sha256, 16)
        if self.row_count < 0:
            raise ValueError("row_count must be non-negative")

    @property
    def manifest_id(self) -> str:
        self.validate()
        return digest(asdict(self))


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str
    parameters: Mapping[str, Any]
    signal_rule: str
    execution_rule: str
    fee_rate: float
    slippage_rate: float

    def validate(self) -> None:
        if not self.strategy_id or not self.name:
            raise ValueError("strategy_id and name are required")
        if not self.signal_rule or not self.execution_rule:
            raise ValueError("signal_rule and execution_rule are required")
        if self.fee_rate < 0 or self.slippage_rate < 0:
            raise ValueError("cost assumptions must be non-negative")

    @property
    def strategy_digest(self) -> str:
        self.validate()
        return digest(asdict(self))


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    research_scope: str
    data_manifest_id: str
    strategy_digest: str
    validation_method: str
    code_revision: str

    def validate(self) -> None:
        values = (
            self.experiment_id,
            self.research_scope,
            self.data_manifest_id,
            self.strategy_digest,
            self.validation_method,
            self.code_revision,
        )
        if not all(values):
            raise ValueError("all experiment identity fields are required")

    @property
    def experiment_digest(self) -> str:
        self.validate()
        return digest(asdict(self))
