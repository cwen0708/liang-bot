"""訂單流指標單元測試。"""

from datetime import datetime, timezone

import pytest

from bot.data.models import OrderFlowBar
from bot.orderflow.indicators import (
    CVDCalculator,
    CVDZScore,
    DeltaCalculator,
    OrderFlowIndicatorEngine,
)


def _make_bar(
    close: float,
    buy_vol: float,
    sell_vol: float,
    high: float | None = None,
    low: float | None = None,
    ts_epoch: float = 1000.0,
) -> OrderFlowBar:
    if high is None:
        high = close + 10
    if low is None:
        low = close - 10
    return OrderFlowBar(
        timestamp=datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
        open=close - 5,
        high=high,
        low=low,
        close=close,
        volume=buy_vol + sell_vol,
        buy_volume=buy_vol,
        sell_volume=sell_vol,
        trade_count=10,
        vwap=close,
    )


class TestDeltaCalculator:
    def test_positive_delta(self):
        bar = _make_bar(100.0, buy_vol=10.0, sell_vol=3.0)
        assert DeltaCalculator.calculate(bar) == pytest.approx(7.0)

    def test_negative_delta(self):
        bar = _make_bar(100.0, buy_vol=2.0, sell_vol=8.0)
        assert DeltaCalculator.calculate(bar) == pytest.approx(-6.0)

    def test_zero_delta(self):
        bar = _make_bar(100.0, buy_vol=5.0, sell_vol=5.0)
        assert DeltaCalculator.calculate(bar) == pytest.approx(0.0)


class TestCVDCalculator:
    def test_cumulative_values(self):
        cvd = CVDCalculator()
        bars = [
            _make_bar(100, 10, 5),   # delta = +5, cvd = 5
            _make_bar(101, 3, 8),    # delta = -5, cvd = 0
            _make_bar(102, 12, 2),   # delta = +10, cvd = 10
        ]
        results = [cvd.update(b) for b in bars]
        assert results == pytest.approx([5.0, 0.0, 10.0])

    def test_current_property(self):
        cvd = CVDCalculator()
        bar = _make_bar(100, 10, 3)
        cvd.update(bar)
        assert cvd.current == pytest.approx(7.0)

    def test_values_list(self):
        cvd = CVDCalculator()
        cvd.update(_make_bar(100, 10, 5))
        cvd.update(_make_bar(101, 3, 8))
        assert len(cvd.values) == 2

    def test_reset(self):
        cvd = CVDCalculator()
        cvd.update(_make_bar(100, 10, 5))
        cvd.reset()
        assert cvd.current == 0.0
        assert len(cvd.values) == 0

    def test_max_history(self):
        cvd = CVDCalculator(max_history=5)
        for i in range(10):
            cvd.update(_make_bar(100 + i, 10, 5))
        assert len(cvd.values) == 5


class TestCVDZScore:
    def test_insufficient_data_returns_zero(self):
        zscore = CVDZScore(lookback=5)
        result = zscore.update([1.0, 2.0])
        assert result == 0.0

    def test_constant_values_returns_zero(self):
        zscore = CVDZScore(lookback=5)
        result = zscore.update([10.0] * 10)
        assert result == pytest.approx(0.0)

    def test_extreme_value_high_zscore(self):
        zscore = CVDZScore(lookback=10)
        # 前 9 個值都是 0，最後一個是 100
        values = [0.0] * 9 + [100.0]
        result = zscore.update(values)
        assert result > 2.0  # 應為極端正值


class TestOrderFlowIndicatorEngine:
    def test_on_bar_returns_all_indicators(self):
        engine = OrderFlowIndicatorEngine()
        bar = _make_bar(100, 10, 5)
        result = engine.on_bar(bar)

        assert "delta" in result
        assert "cvd" in result
        assert "cvd_zscore" in result
        assert "buy_volume" in result
        assert "sell_volume" in result
        assert "vwap" in result
        assert "delta_pct" in result
        assert result["delta"] == pytest.approx(5.0)

    def test_multiple_bars_accumulate(self):
        engine = OrderFlowIndicatorEngine()
        engine.on_bar(_make_bar(100, 10, 5))  # delta=5, cvd=5
        result = engine.on_bar(_make_bar(101, 3, 8))  # delta=-5, cvd=0

        assert result["delta"] == pytest.approx(-5.0)
        assert result["cvd"] == pytest.approx(0.0)

    def test_prices_tracked(self):
        engine = OrderFlowIndicatorEngine()
        engine.on_bar(_make_bar(100, 10, 5))
        engine.on_bar(_make_bar(105, 10, 5))

        assert engine.prices == [100.0, 105.0]

    def test_reset(self):
        engine = OrderFlowIndicatorEngine()
        engine.on_bar(_make_bar(100, 10, 5))
        engine.reset()
        assert len(engine.prices) == 0
        assert engine.cvd.current == 0.0
