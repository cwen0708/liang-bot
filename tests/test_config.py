"""配置模組測試。"""

import os

import pytest

from bot.config.constants import TradingMode, VALID_TIMEFRAMES


class TestConstants:
    def test_trading_modes(self):
        assert TradingMode.PAPER.value == "paper"
        assert TradingMode.LIVE.value == "live"

    def test_valid_timeframes(self):
        assert "1h" in VALID_TIMEFRAMES
        assert "1d" in VALID_TIMEFRAMES
        assert "5m" in VALID_TIMEFRAMES
        assert "2s" not in VALID_TIMEFRAMES
