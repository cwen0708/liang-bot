"""aggTrade → OrderFlowBar 聚合器。"""

import math
from datetime import datetime, timezone

from bot.data.models import AggTrade, FootprintLevel, OrderFlowBar
from bot.logging_config import get_logger

logger = get_logger("data.bar_aggregator")


class BarAggregator:
    """
    將 aggTrade 串流聚合為 OrderFlowBar。

    支援時間型 K 線（依秒數切割）和成交量型 K 線（依累積量切割）。
    """

    def __init__(
        self,
        interval_seconds: int = 60,
        tick_size: float = 0.01,
    ) -> None:
        """
        Args:
            interval_seconds: K 線時間間隔（秒）。
            tick_size: footprint 價格層級粒度。
        """
        self.interval_seconds = interval_seconds
        self.tick_size = tick_size
        self._reset()

    def _reset(self) -> None:
        self._trades: list[AggTrade] = []
        self._bar_open_time: datetime | None = None

    def _get_bar_open_time(self, ts: datetime) -> datetime:
        """將時間戳對齊到 K 線開盤時間。"""
        epoch = ts.timestamp()
        aligned = math.floor(epoch / self.interval_seconds) * self.interval_seconds
        return datetime.fromtimestamp(aligned, tz=timezone.utc)

    def _round_price(self, price: float) -> float:
        """將價格對齊到 tick_size 粒度。"""
        return round(round(price / self.tick_size) * self.tick_size, 10)

    def add_trade(self, trade: AggTrade) -> OrderFlowBar | None:
        """
        餵入一筆 aggTrade，若觸發 K 線關閉則回傳 OrderFlowBar。

        Returns:
            完成的 OrderFlowBar（若此 trade 屬於下一根 K 線），否則 None。
        """
        bar_open = self._get_bar_open_time(trade.timestamp)

        if self._bar_open_time is None:
            self._bar_open_time = bar_open

        # 新 K 線開始：先關閉舊 K 線
        if bar_open > self._bar_open_time and self._trades:
            completed_bar = self._build_bar()
            self._reset()
            self._bar_open_time = bar_open
            self._trades.append(trade)
            return completed_bar

        self._trades.append(trade)
        return None

    def flush(self) -> OrderFlowBar | None:
        """強制關閉當前未完成的 K 線（用於回測結束或連線斷線時）。"""
        if not self._trades:
            return None
        bar = self._build_bar()
        self._reset()
        return bar

    def _build_bar(self) -> OrderFlowBar:
        """從累積的 trades 建構 OrderFlowBar。"""
        prices = [t.price for t in self._trades]
        buy_volume = 0.0
        sell_volume = 0.0
        total_pv = 0.0  # price * volume 加總（for VWAP）
        total_vol = 0.0
        footprint: dict[float, FootprintLevel] = {}

        for t in self._trades:
            vol = t.quantity
            total_pv += t.price * vol
            total_vol += vol

            if t.is_buyer_maker:
                sell_volume += vol
            else:
                buy_volume += vol

            # footprint 聚合
            level_price = self._round_price(t.price)
            if level_price not in footprint:
                footprint[level_price] = FootprintLevel(price=level_price)
            fp = footprint[level_price]
            if t.is_buyer_maker:
                fp.sell_volume += vol
            else:
                fp.buy_volume += vol

        vwap = total_pv / total_vol if total_vol > 0 else prices[0]

        return OrderFlowBar(
            timestamp=self._bar_open_time,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=total_vol,
            buy_volume=buy_volume,
            sell_volume=sell_volume,
            trade_count=len(self._trades),
            vwap=vwap,
            footprint=footprint,
        )
