"""共用技術指標計算工具 — 供風控預計算使用。"""

from __future__ import annotations

import pandas as pd

from bot.logging_config import get_logger

logger = get_logger("utils.indicators")


# ---------------------------------------------------------------------------
# ATR (Average True Range)
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """從 OHLCV DataFrame 計算 ATR。

    需要 columns: high, low, close。
    返回最新一筆 ATR 值；資料不足時返回 0.0。
    """
    if df is None or len(df) < period + 1:
        return 0.0

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(window=period).mean()
    latest = atr.iloc[-1]
    return float(latest) if pd.notna(latest) else 0.0


# ---------------------------------------------------------------------------
# Fibonacci Retracement
# ---------------------------------------------------------------------------

def compute_fibonacci_levels(
    df: pd.DataFrame,
    swing_lookback: int = 5,
) -> dict[str, float]:
    """用 Swing High/Low 計算 Fibonacci 回撤位。

    Returns:
        {"swing_high": ..., "swing_low": ...,
         "0.236": ..., "0.382": ..., "0.500": ..., "0.618": ..., "0.786": ...}
        若資料不足則回傳空 dict。
    """
    from bot.orderflow.sfp import SwingDetector

    if df is None or len(df) < swing_lookback * 2 + 2:
        return {}

    detector = SwingDetector(lookback=swing_lookback)
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    swing_highs = detector.find_swing_highs(highs)
    swing_lows = detector.find_swing_lows(lows)

    if not swing_highs or not swing_lows:
        return {}

    # 取最近的 swing high/low
    sh = max(p for _, p in swing_highs[-5:])
    sl = min(p for _, p in swing_lows[-5:])

    if sh <= sl:
        return {}

    diff = sh - sl
    fib_ratios = [0.236, 0.382, 0.500, 0.618, 0.786]

    result: dict[str, float] = {"swing_high": sh, "swing_low": sl}
    for ratio in fib_ratios:
        # 回撤位 = 高點往下
        result[str(ratio)] = sh - diff * ratio

    return result


# ---------------------------------------------------------------------------
# Support / Resistance
# ---------------------------------------------------------------------------

def compute_support_resistance(
    df: pd.DataFrame,
    swing_lookback: int = 5,
    max_levels: int = 3,
) -> dict[str, list[float]]:
    """用 Swing High/Low 計算支撐壓力位。

    Returns:
        {"support": [price, ...], "resistance": [price, ...]}
    """
    from bot.orderflow.sfp import SwingDetector

    if df is None or len(df) < swing_lookback * 2 + 2:
        return {"support": [], "resistance": []}

    detector = SwingDetector(lookback=swing_lookback)
    highs = df["high"].tolist()
    lows = df["low"].tolist()

    swing_highs = detector.find_swing_highs(highs)
    swing_lows = detector.find_swing_lows(lows)

    current_price = float(df["close"].iloc[-1])

    # 壓力位：高於現價的 swing highs，由近到遠
    resistance = sorted(
        set(p for _, p in swing_highs if p > current_price),
    )[:max_levels]

    # 支撐位：低於現價的 swing lows，由近到遠（高到低）
    support = sorted(
        set(p for _, p in swing_lows if p < current_price),
        reverse=True,
    )[:max_levels]

    return {"support": support, "resistance": resistance}


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def compute_bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, float]:
    """計算 Bollinger Bands。

    Returns:
        {"upper": ..., "mid": ..., "lower": ..., "pct_b": ...}
        若資料不足則回傳空 dict。
    """
    if df is None or len(df) < period + 1:
        return {}

    close = df["close"]
    mid = close.rolling(window=period).mean()
    rolling_std = close.rolling(window=period).std()
    upper = mid + std_dev * rolling_std
    lower = mid - std_dev * rolling_std

    latest_upper = float(upper.iloc[-1]) if pd.notna(upper.iloc[-1]) else 0.0
    latest_lower = float(lower.iloc[-1]) if pd.notna(lower.iloc[-1]) else 0.0
    latest_mid = float(mid.iloc[-1]) if pd.notna(mid.iloc[-1]) else 0.0
    latest_close = float(close.iloc[-1])

    band_width = latest_upper - latest_lower
    pct_b = (latest_close - latest_lower) / band_width if band_width > 0 else 0.5

    return {
        "upper": latest_upper,
        "mid": latest_mid,
        "lower": latest_lower,
        "pct_b": pct_b,
    }
