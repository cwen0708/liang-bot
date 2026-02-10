"""市場數據模型。"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    volume: float
    timestamp: int


# ── 訂單流數據模型 ──────────────────────────────────────────


@dataclass
class AggTrade:
    """單筆聚合成交（Binance aggTrade）。"""

    trade_id: int
    price: float
    quantity: float
    timestamp: datetime
    is_buyer_maker: bool

    @property
    def signed_volume(self) -> float:
        """帶正負號的成交量：taker buy (+) / taker sell (-)。"""
        return -self.quantity if self.is_buyer_maker else self.quantity


@dataclass
class FootprintLevel:
    """單一價格層級的 footprint 買/賣量。"""

    price: float
    buy_volume: float = 0.0
    sell_volume: float = 0.0

    @property
    def delta(self) -> float:
        return self.buy_volume - self.sell_volume

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume


@dataclass
class OrderFlowBar:
    """含訂單流資訊的 K 線。"""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    trade_count: int
    vwap: float
    footprint: dict[float, FootprintLevel] = field(default_factory=dict)

    @property
    def delta(self) -> float:
        """淨買壓 = buy_volume - sell_volume。"""
        return self.buy_volume - self.sell_volume

    @property
    def delta_pct(self) -> float:
        """Delta 佔總成交量的百分比。"""
        if self.volume == 0:
            return 0.0
        return self.delta / self.volume

    def to_candle(self) -> Candle:
        """轉換為基礎 Candle 物件。"""
        return Candle(
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )
