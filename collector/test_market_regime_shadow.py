from market_regime_shadow import calculate_regime, verify_krx


def test_replay_20260917():
    closes = [
        6717.97, 6627.26, 6684.37, 6909.91, 7033.92, 7051.64, 6954.52,
        6995.39, 6687.21, 6579.48, 6562.72, 6835.80, 6820.02, 6788.88,
        6912.37, 6808.21, 6742.74, 6696.96, 6912.95, 6852.58, 6471.17,
    ]
    result = calculate_regime(closes)
    assert result["regime_code"] == "RED"
    assert abs(result["return_20d"] - 0.0381383891939171) < 1e-12


def test_krx_is_never_kill_switch():
    assert verify_krx(6717.97, None)[0] == "PENDING"
    assert verify_krx(6717.97, 6718.00)[0] == "WARN"
