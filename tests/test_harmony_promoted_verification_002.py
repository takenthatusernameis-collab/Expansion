from experiments.run_harmony_promoted_verification_002 import close
def test_close():
    assert close(1.0,1.0)
    assert not close(1.0,1.0+1e-8)
