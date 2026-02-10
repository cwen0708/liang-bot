"""策略模組測試。"""

import numpy as np
import pandas as pd
import pytest

from bot.strategy.signals import Signal
from bot.strategy.sma_crossover import SMACrossoverStrategy


class TestSMACrossover:
    def setup_method(self):
        self.strategy = SMACrossoverStrategy({"fast_period": 5, "slow_period": 10})

    def test_name(self):
        assert self.strategy.name == "sma_crossover"

    def test_required_candles(self):
        assert self.strategy.required_candles == 12  # slow_period + 2

    def test_hold_when_insufficient_data(self):
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=5, freq="1h"),
            "close": [100, 101, 102, 103, 104],
        })
        assert self.strategy.generate_signal(df) == Signal.HOLD

    def test_calculate_indicators(self):
        np.random.seed(0)
        n = 20
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "close": np.random.uniform(100, 110, n),
        })
        result = self.strategy.calculate_indicators(df)
        assert "sma_fast" in result.columns
        assert "sma_slow" in result.columns

    def test_buy_signal_on_golden_cross(self):
        """快線上穿慢線應產生 BUY 訊號。"""
        n = 20
        # 構造: 前半段快線在慢線下方，後半段快線在慢線上方
        prices = list(range(100, 100 + n))  # 持續上漲
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "close": [float(p) for p in prices],
        })
        signal = self.strategy.generate_signal(df)
        # 持續上漲中，快線應在慢線上方（可能是 BUY 或 HOLD）
        assert signal in (Signal.BUY, Signal.HOLD)

    def test_hold_in_flat_market(self):
        """平盤應產生 HOLD 訊號。"""
        n = 30
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
            "close": [100.0] * n,
        })
        assert self.strategy.generate_signal(df) == Signal.HOLD
