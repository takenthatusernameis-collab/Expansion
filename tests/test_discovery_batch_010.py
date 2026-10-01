from pathlib import Path

def test_batch_010_frozen():
 c=Path("research/HARMONY-DISCOVERY-BATCH-010.yaml").read_text()
 assert "HARMONY-FIN-0056" in c
 assert "deep_capacity: 1" in c
 assert "no_parameter_search: true" in c
