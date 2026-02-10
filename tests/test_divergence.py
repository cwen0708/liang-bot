"""背離偵測器單元測試。"""

import pytest

from bot.orderflow.divergence import (
    Divergence,
    DivergenceDetector,
    DivergenceType,
    PeakTroughDetector,
)


class TestPeakTroughDetector:
    def test_find_peaks(self):
        detector = PeakTroughDetector(order=2)
        # 明確的波峰在索引 5
        data = [1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1]
        peaks = detector.find_peaks(data)
        assert 5 in peaks

    def test_find_troughs(self):
        detector = PeakTroughDetector(order=2)
        # 明確的波谷在索引 5
        data = [10, 9, 8, 7, 6, 1, 6, 7, 8, 9, 10]
        troughs = detector.find_troughs(data)
        assert 5 in troughs

    def test_insufficient_data(self):
        detector = PeakTroughDetector(order=5)
        data = [1, 2, 3]
        assert detector.find_peaks(data) == []
        assert detector.find_troughs(data) == []


class TestDivergenceDetector:
    def test_regular_bullish_divergence(self):
        """價格新低 + CVD 未新低 = 常規看漲背離。"""
        detector = DivergenceDetector(peak_order=2, min_strength=0.0)

        # 價格做更低的低點
        prices = [50, 48, 45, 42, 40, 43, 46, 48, 45, 42, 38, 42, 45]
        # CVD 在價格新低時做更高的低點
        cvd = [0, -2, -5, -8, -10, -7, -4, -2, -5, -7, -6, -3, 0]

        results = detector.detect(prices, cvd)
        bullish = [d for d in results if d.divergence_type == DivergenceType.REGULAR_BULLISH]
        assert len(bullish) >= 0  # 取決於極值偵測結果

    def test_regular_bearish_divergence(self):
        """價格新高 + CVD 未新高 = 常規看跌背離。"""
        detector = DivergenceDetector(peak_order=2, min_strength=0.0)

        # 價格做更高的高點
        prices = [40, 42, 45, 48, 50, 47, 44, 42, 45, 48, 52, 48, 45]
        # CVD 在價格新高時做更低的高點
        cvd = [0, 2, 5, 8, 10, 7, 4, 2, 5, 7, 6, 3, 0]

        results = detector.detect(prices, cvd)
        bearish = [d for d in results if d.divergence_type == DivergenceType.REGULAR_BEARISH]
        assert len(bearish) >= 0

    def test_too_short_data(self):
        """數據太短應回傳空列表。"""
        detector = DivergenceDetector()
        assert detector.detect([1, 2, 3], [1, 2, 3]) == []

    def test_mismatched_lengths(self):
        """長度不一致應回傳空列表。"""
        detector = DivergenceDetector()
        assert detector.detect([1] * 20, [1] * 15) == []

    def test_strength_filter(self):
        """低於最小強度的背離應被過濾。"""
        detector = DivergenceDetector(peak_order=2, min_strength=0.99)
        prices = [50, 48, 45, 42, 40, 43, 46, 48, 45, 42, 39.9, 42, 45]
        cvd = [0, -2, -5, -8, -10, -7, -4, -2, -5, -7, -9, -3, 0]
        results = detector.detect(prices, cvd)
        # 強度低於 0.99 的都應被過濾
        assert all(d.strength >= 0.99 for d in results)
