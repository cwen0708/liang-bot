"""合約交易所抽象基底類別。"""

from abc import ABC, abstractmethod

import pandas as pd


class BaseFuturesExchange(ABC):
    """USDT-M 永續合約交易所介面。"""

    @abstractmethod
    def get_ticker(self, symbol: str) -> dict:
        """取得即時報價。"""

    @abstractmethod
    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """取得 K 線數據。"""

    @abstractmethod
    def get_futures_balance(self) -> dict:
        """取得合約帳戶餘額。

        Returns:
            包含 total_wallet_balance, available_balance,
            total_unrealized_pnl, total_margin_balance 的 dict。
        """

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """取得所有持倉。"""

    @abstractmethod
    def set_leverage(self, symbol: str, leverage: int) -> None:
        """設定槓桿倍數。"""

    @abstractmethod
    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        """設定保證金模式（cross / isolated）。"""

    @abstractmethod
    def place_market_order(
        self, symbol: str, side: str, amount: float,
        reduce_only: bool = False,
    ) -> dict:
        """市價單。"""

    @abstractmethod
    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float,
        reduce_only: bool = False,
    ) -> dict:
        """限價單。"""

    @abstractmethod
    def place_stop_market(
        self, symbol: str, side: str, amount: float,
        stop_price: float, reduce_only: bool = True,
    ) -> dict:
        """停損市價單（Stop Market）。"""

    @abstractmethod
    def place_take_profit_market(
        self, symbol: str, side: str, amount: float,
        stop_price: float, reduce_only: bool = True,
    ) -> dict:
        """停利市價單（Take Profit Market）。"""

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消訂單。"""

    @abstractmethod
    def get_order_status(self, order_id: str, symbol: str) -> dict:
        """查詢訂單狀態。"""

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> dict:
        """取得當前資金費率。"""

    @abstractmethod
    def get_margin_ratio(self) -> float:
        """取得帳戶保證金比率（0~1）。"""

    @abstractmethod
    def get_liquidation_price(self, symbol: str) -> float | None:
        """取得持倉的清算價。"""

    @abstractmethod
    def get_min_order_amount(self, symbol: str) -> float:
        """取得最小下單數量。"""
