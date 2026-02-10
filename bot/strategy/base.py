"""策略抽象基底類別。"""

from abc import ABC, abstractmethod

import pandas as pd

from bot.config.constants import DataFeedType
from bot.data.models import OrderFlowBar
from bot.strategy.signals import Signal, StrategyVerdict


class BaseStrategy(ABC):
    """所有 OHLCV 交易策略必須繼承此介面。"""

    data_feed_type: DataFeedType = DataFeedType.OHLCV

    def __init__(self, params: dict) -> None:
        self.params = params

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名稱。"""

    @property
    def required_candles(self) -> int:
        """策略產生訊號所需的最少 K 線數量。"""
        return 50

    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算技術指標，新增欄位至 DataFrame。"""

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """根據當前指標產生交易訊號。"""

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        """產生策略結論報告（預設實作：包裝 generate_signal 結果）。"""
        signal = self.generate_signal(df)
        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=1.0 if signal != Signal.HOLD else 0.0,
            reasoning=f"{self.name} 訊號: {signal.value}",
        )


class OrderFlowStrategy(ABC):
    """訂單流策略抽象基底 — 接收 OrderFlowBar，輸出 StrategyVerdict。"""

    data_feed_type: DataFeedType = DataFeedType.ORDER_FLOW

    def __init__(self, params: dict) -> None:
        self.params = params

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名稱。"""

    @property
    def required_bars(self) -> int:
        """策略需要的最少 K 線歷史。"""
        return 50

    @abstractmethod
    def on_bar(self, symbol: str, bar: OrderFlowBar) -> StrategyVerdict:
        """接收新 K 線，產生策略結論報告。"""

    def latest_verdict(self, symbol: str) -> StrategyVerdict | None:
        """回傳最近一次的結論（無新 bar 時用）。預設回傳 None。"""
        return None

    @abstractmethod
    def reset(self) -> None:
        """重置策略內部狀態。"""
