"""常數定義。"""

from enum import Enum


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class DataFeedType(str, Enum):
    """策略數據來源類型。"""
    OHLCV = "ohlcv"              # 傳統 K 線
    ORDER_FLOW = "order_flow"    # 訂單流（aggTrade 聚合）


# 支援的時間框架
VALID_TIMEFRAMES = [
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
]

class MarketType(str, Enum):
    """交易市場類型。"""
    SPOT = "spot"
    FUTURES = "futures"


class PositionSide(str, Enum):
    """持倉方向。"""
    LONG = "long"
    SHORT = "short"


# 幣安現貨最低交易額（USDT）
MIN_NOTIONAL_USDT = 10.0

# 幣安 USDT-M 合約最低交易額
MIN_NOTIONAL_FUTURES_USDT = 5.0

# API 重試設定
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
