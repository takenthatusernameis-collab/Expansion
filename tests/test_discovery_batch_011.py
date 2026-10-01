from pathlib import Path

def test_batch_011_frozen():
 c=Path("research/HARMONY-DISCOVERY-BATCH-011.yaml").read_text()
 assert "HARMONY-FIN-0057" in c
 assert "deep_capacity: 1" in c
 assert "no_parameter_search: true" in c
