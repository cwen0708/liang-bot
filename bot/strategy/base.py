"""策略抽象基底類別。"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import pandas as pd

from bot.config.constants import DataFeedType
from bot.data.bar_aggregator import BarAggregator
from bot.data.models import AggTrade, OrderFlowBar
from bot.logging_config import get_logger
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.base")


class Strategy(ABC):
    """所有策略的共同祖先 — 定義統一介面。"""

    def __init__(self, params: dict) -> None:
        self.params = params

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名稱。"""

    @property
    @abstractmethod
    def data_feed_type(self) -> DataFeedType:
        """策略數據來源類型。"""

    @property
    def timeframe(self) -> str:
        """策略的 K 線時間框架。OrderFlow 回傳空字串。"""
        return self.params.get("_timeframe", "")


class BaseStrategy(Strategy):
    """所有 OHLCV 交易策略必須繼承此介面。"""

    data_feed_type: DataFeedType = DataFeedType.OHLCV

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
            timeframe=self.timeframe,
        )


class OrderFlowStrategy(Strategy):
    """訂單流策略抽象基底 — 接收 OrderFlowBar，輸出 StrategyVerdict。"""

    data_feed_type: DataFeedType = DataFeedType.ORDER_FLOW

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

    def feed_trades(
        self,
        symbol: str,
        raw_trades: list[dict],
        aggregator: BarAggregator,
        last_trade_id: int,
    ) -> tuple[StrategyVerdict | None, int]:
        """
        接收原始 trades，過濾 → 聚合為 bars → 產生 verdict。

        Args:
            symbol: 交易對。
            raw_trades: exchange.fetch_agg_trades() 回傳的原始交易列表。
            aggregator: 此 symbol 的 BarAggregator（跨輪保留）。
            last_trade_id: 上次處理的最後 trade ID。

        Returns:
            (verdict, new_last_trade_id)。若無新 trade 則 new_last_trade_id = 0。
        """
        # 過濾已處理的 trades
        new_trades = [
            t for t in raw_trades
            if int(t.get("trade_id") or 0) > last_trade_id
        ]

        if not new_trades:
            return self.latest_verdict(symbol), 0

        new_last_id = int(new_trades[-1]["trade_id"] or 0)

        # 聚合為 bars
        new_bars: list[OrderFlowBar] = []
        for t in new_trades:
            trade = AggTrade(
                trade_id=t["trade_id"] or 0,
                price=t["price"],
                quantity=t["quantity"],
                timestamp=datetime.fromtimestamp(
                    t["timestamp"] / 1000, tz=timezone.utc
                ),
                is_buyer_maker=t["is_buyer_maker"],
            )
            bar = aggregator.add_trade(trade)
            if bar is not None:
                new_bars.append(bar)

        if new_bars:
            logger.info("    [%s] aggTrade → %d 根新 K 線", self.name[:3], len(new_bars))

        # 送入策略
        verdict = None
        for bar in new_bars:
            verdict = self.on_bar(symbol, bar)

        # 無新 bar 時用最近結論
        if verdict is None:
            verdict = self.latest_verdict(symbol)

        return verdict, new_last_id
