from harmony_backtest.experiment import (
    DataManifest,
    ExperimentSpec,
    StrategySpec,
)


def test_manifest_digest_is_order_stable() -> None:
    manifest_a = DataManifest(
        source="fixture",
        symbol="BTCUSD",
        timeframe="1d",
        start="2024-01-01",
        end="2024-12-31",
        source_locator="fixture://btc.csv",
        content_sha256="0" * 64,
        row_count=365,
    )
    manifest_b = DataManifest(
        row_count=365,
        content_sha256="0" * 64,
        source_locator="fixture://btc.csv",
        end="2024-12-31",
        start="2024-01-01",
        timeframe="1d",
        symbol="BTCUSD",
        source="fixture",
    )
    assert manifest_a.manifest_id == manifest_b.manifest_id


def test_strategy_digest_changes_when_parameters_change() -> None:
    common = dict(
        strategy_id="TEST-001",
        name="moving-average",
        signal_rule="close > sma",
        execution_rule="next eligible close",
        fee_rate=0.001,
        slippage_rate=0.001,
    )
    a = StrategySpec(parameters={"fast": 20, "slow": 50}, **common)
    b = StrategySpec(parameters={"fast": 21, "slow": 50}, **common)
    assert a.strategy_digest != b.strategy_digest


def test_experiment_digest_is_stable_for_same_identity() -> None:
    kwargs = dict(
        experiment_id="HARMONY-EXP-0001",
        research_scope="contract acceptance",
        data_manifest_id="a" * 64,
        strategy_digest="b" * 64,
        validation_method="walk-forward",
        code_revision="c" * 40,
    )
    a = ExperimentSpec(**kwargs)
    b = ExperimentSpec(**kwargs)
    assert a.experiment_digest == b.experiment_digest
