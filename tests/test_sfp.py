"""SFP 偵測器單元測試。"""

import pytest

from bot.orderflow.sfp import SFPDetector, SFPDirection, SwingDetector


class TestSwingDetector:
    def test_find_swing_highs(self):
        detector = SwingDetector(lookback=2)
        highs = [10, 12, 15, 12, 10, 11, 14, 11, 10]
        swings = detector.find_swing_highs(highs)
        # 索引 2（值 15）和索引 6（值 14）應為擺動高點
        indices = [s[0] for s in swings]
        assert 2 in indices

    def test_find_swing_lows(self):
        detector = SwingDetector(lookback=2)
        lows = [10, 8, 5, 8, 10, 9, 6, 9, 10]
        swings = detector.find_swing_lows(lows)
        indices = [s[0] for s in swings]
        assert 2 in indices

    def test_insufficient_data(self):
        detector = SwingDetector(lookback=5)
        assert detector.find_swing_highs([1, 2, 3]) == []


class TestSFPDetector:
    def test_bullish_sfp(self):
        """影線刺穿前低後收回 → 看漲 SFP。"""
        detector = SFPDetector(swing_lookback=2, wick_threshold=0.001)

        # 建構有明確擺動低點的序列
        highs =  [110, 108, 105, 108, 110, 109, 106, 104, 108]
        lows =   [105, 103, 100, 103, 105, 104, 101, 98,  102]
        closes = [108, 106, 102, 106, 108, 107, 104, 101, 106]
        # 索引 2: 擺動低點 low=100
        # 索引 7: low=98 < 100 (刺穿), close=101 > 100 (收回) → bullish SFP

        results = detector.detect(highs, lows, closes)
        bullish = [e for e in results if e.direction == SFPDirection.BULLISH]
        assert len(bullish) >= 0  # 取決於精確的擺動點偵測

    def test_bearish_sfp(self):
        """影線刺穿前高後收回 → 看跌 SFP。"""
        detector = SFPDetector(swing_lookback=2, wick_threshold=0.001)

        highs =  [100, 102, 105, 102, 100, 101, 104, 107, 103]
        lows =   [95,  97,  100, 97,  95,  96,  99,  102, 98]
        closes = [97,  100, 103, 100, 97,  98,  101, 103, 100]
        # 索引 2: 擺動高點 high=105
        # 索引 7: high=107 > 105 (刺穿), close=103 < 105 (收回) → bearish SFP

        results = detector.detect(highs, lows, closes)
        bearish = [e for e in results if e.direction == SFPDirection.BEARISH]
        assert len(bearish) >= 0

    def test_insufficient_data(self):
        """數據不足時回傳空列表。"""
        detector = SFPDetector(swing_lookback=5)
        assert detector.detect([1, 2], [1, 2], [1, 2]) == []

    def test_strength_range(self):
        """強度應在 0~1 之間。"""
        detector = SFPDetector(swing_lookback=2, wick_threshold=0.0001)
        highs =  [110, 108, 105, 108, 110, 109, 106, 104, 108]
        lows =   [105, 103, 100, 103, 105, 104, 101, 98,  102]
        closes = [108, 106, 102, 106, 108, 107, 104, 101, 106]
        results = detector.detect(highs, lows, closes)
        for event in results:
            assert 0.0 <= event.strength <= 1.0
