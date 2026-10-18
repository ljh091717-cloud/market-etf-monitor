import math
from collector import ema_last, indicator_summary, price_band, rsi_wilder_last, TARGET_BANDS


def test_ema_constant_series():
    assert math.isclose(ema_last([100] * 300, 200), 100.0, abs_tol=1e-9)


def test_ema_insufficient_data():
    assert ema_last([1.0] * 19, 20) is None


def test_rsi_constant_series():
    assert rsi_wilder_last([100.0] * 40, 14) == 50.0


def test_rsi_strictly_increasing():
    assert math.isclose(rsi_wilder_last(list(range(50)), 14), 100.0)


def test_rsi_strictly_decreasing():
    assert math.isclose(rsi_wilder_last(list(range(50, 0, -1)), 14), 0.0)


def test_buy_zone_gap():
    assert price_band(292.26, TARGET_BANDS['QQQM'])['status'] == 'below_second_above_third'
    assert price_band(302.0, TARGET_BANDS['QQQM'])['status'] == 'inside_first'
    assert price_band(279.0, TARGET_BANDS['QQQM'])['status'] == 'below_third'
    assert price_band(710.0, TARGET_BANDS['VOO'])['status'] == 'above_first'


def test_weekly_indicator_output():
    out = indicator_summary([100 + i * 0.1 for i in range(260)])
    assert out['sample_weeks'] == 260
    assert out['ema_200'] is not None
    assert out['rsi_14'] is not None
