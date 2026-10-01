from experiments.run_harmony_discovery_batch_014 import rank_weights, S

def test_fixed_gross_exposure():
    w=rank_weights([(float(i),s) for i,s in enumerate(S)])
    assert abs(sum(abs(w[s]) for s in S)-1.0)<1e-12
    assert sum(v>0 for v in w.values())==3
    assert sum(v<0 for v in w.values())==3
