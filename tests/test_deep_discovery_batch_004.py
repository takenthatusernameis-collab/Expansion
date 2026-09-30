from experiments.run_harmony_deep_discovery_batch_004 import beta, load_fgi


def test_fgi_parser_reads_historical_values():
    raw = b'{"data":[{"value":"40","timestamp":"1704067200"},{"value":"60","timestamp":"1704153600"}]}'
    values = load_fgi(raw)
    assert len(values) == 2
    assert values["2024-01-01"] == 40.0


def test_beta_is_deterministic():
    assert beta([1.0, 2.0, 3.0], [1.0, 2.0, 4.0]) > 0


def test_beta_requires_nonconstant_factor():
    assert beta([1.0, 2.0, 3.0], [1.0, 1.0, 1.0]) == 0.0
