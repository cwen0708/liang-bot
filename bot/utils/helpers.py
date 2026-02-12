"""共用工具函數。"""

import json
import math
import re
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


def parse_json_response(response: str) -> dict | None:
    """從 LLM 回覆文字中提取第一個 JSON 物件。

    支援三種格式：
    1. ```json ... ``` markdown code block
    2. ``` ... ``` 無語言標記的 code block
    3. 裸 JSON（直接 { ... }）

    Returns:
        解析後的 dict，或 None（解析失敗）。
    """
    # 1. 嘗試 markdown code block（```json ... ``` 或 ``` ... ```）
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # 2. 嘗試找裸 JSON 物件
    m = re.search(r'\{[^{}]*\}', response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    return None
