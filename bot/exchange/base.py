"""交易所抽象基底類別。"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseExchange(ABC):
    """所有交易所實作必須繼承此介面。"""

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        """取得即時報價。回傳包含 bid, ask, last 的 dict。"""

    @abstractmethod
    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """取得 K 線數據，回傳 DataFrame (columns: timestamp, open, high, low, close, volume)。"""

    @abstractmethod
    def get_balance(self) -> dict[str, float]:
        """取得帳戶餘額，回傳 {幣種: 可用餘額}。"""

    @abstractmethod
    def place_market_order(self, symbol: str, side: str, amount: float) -> dict:
        """市價單。回傳訂單資訊 dict。"""

    @abstractmethod
    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        """限價單。回傳訂單資訊 dict。"""

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消訂單。"""

    @abstractmethod
    def get_order_status(self, order_id: str, symbol: str) -> dict:
        """查詢訂單狀態。"""

    @abstractmethod
    def get_min_order_amount(self, symbol: str) -> float:
        """取得最小下單數量。"""
