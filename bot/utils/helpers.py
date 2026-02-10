"""共用工具函數。"""

import math
from datetime import datetime, timezone


def round_step_size(quantity: float, step_size: float) -> float:
    """將數量依交易所步進值取整。"""
    if step_size == 0:
        return quantity
    precision = int(round(-math.log10(step_size)))
    return round(math.floor(quantity * 10**precision) / 10**precision, precision)


def round_price(price: float, tick_size: float) -> float:
    """將價格依交易所最小價格單位取整。"""
    if tick_size == 0:
        return price
    precision = int(round(-math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)


def timestamp_to_datetime(ts_ms: int) -> datetime:
    """毫秒時間戳轉 datetime (UTC)。"""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def datetime_to_timestamp(dt: datetime) -> int:
    """datetime 轉毫秒時間戳。"""
    return int(dt.timestamp() * 1000)


def format_pct(value: float) -> str:
    """格式化為百分比字串。"""
    return f"{value * 100:.2f}%"
