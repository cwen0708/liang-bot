"""共用技術指標計算工具 — 供風控預計算使用。"""

from __future__ import annotations

from dataclasses import dataclass

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


# ---------------------------------------------------------------------------
# Multi-Timeframe Summary
# ---------------------------------------------------------------------------

@dataclass
class TimeframeSummary:
    """單一時間框架的技術指標快照。"""
    timeframe: str
    close: float
    change_pct: float
    trend: str              # "bullish" / "bearish" / "neutral"
    sma_20: float
    sma_50: float | None
    price_vs_sma20: str     # "above" / "below"
    rsi_14: float
    macd_histogram: float
    macd_direction: str     # "bullish" / "bearish" / "neutral"
    bb_pct_b: float
    volume_trend: str       # "increasing" / "decreasing" / "flat"
    atr_14: float
    atr_pct: float


def compute_mtf_summary(df: pd.DataFrame, timeframe: str) -> TimeframeSummary | None:
    """從 OHLCV DataFrame 計算緊湊的技術指標快照。

    至少需要 20 根 K 線。每個子指標獨立 try/except，部分失敗仍可產出結果。
    """
    if df is None or len(df) < 20:
        return None

    close = df["close"]
    latest_close = float(close.iloc[-1])
    first_close = float(close.iloc[0])
    change_pct = (latest_close - first_close) / first_close if first_close > 0 else 0.0

    # SMA 20 / 50
    sma_20 = float(close.rolling(20).mean().iloc[-1])
    sma_50 = float(close.rolling(50).mean().iloc[-1]) if len(df) >= 50 else None
    price_vs_sma20 = "above" if latest_close > sma_20 else "below"

    # Trend: price vs SMA20 + SMA20 slope
    sma_20_5ago = float(close.rolling(20).mean().iloc[-5]) if len(df) >= 24 else sma_20
    if latest_close > sma_20 and sma_20 > sma_20_5ago:
        trend = "bullish"
    elif latest_close < sma_20 and sma_20 < sma_20_5ago:
        trend = "bearish"
    else:
        trend = "neutral"

    # RSI 14
    rsi_val = 50.0
    try:
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi_series = 100 - (100 / (1 + rs))
        v = rsi_series.iloc[-1]
        rsi_val = float(v) if pd.notna(v) else 50.0
    except Exception:
        pass

    # MACD (12, 26, 9)
    macd_hist = 0.0
    macd_dir = "neutral"
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - signal_line
        macd_hist = float(hist.iloc[-1]) if pd.notna(hist.iloc[-1]) else 0.0
        prev_hist = float(hist.iloc[-2]) if pd.notna(hist.iloc[-2]) else 0.0
        if macd_hist > 0 and macd_hist > prev_hist:
            macd_dir = "bullish"
        elif macd_hist < 0 and macd_hist < prev_hist:
            macd_dir = "bearish"
    except Exception:
        pass

    # Bollinger %B
    bb = compute_bollinger_bands(df)
    bb_pct_b = bb.get("pct_b", 0.5)

    # Volume trend (最近 5 根 vs 前 5 根)
    vol_trend = "flat"
    try:
        recent_vol = float(df["volume"].iloc[-5:].mean())
        prev_vol = float(df["volume"].iloc[-10:-5].mean())
        if prev_vol > 0:
            if recent_vol > prev_vol * 1.2:
                vol_trend = "increasing"
            elif recent_vol < prev_vol * 0.8:
                vol_trend = "decreasing"
    except Exception:
        pass

    # ATR
    atr_val = compute_atr(df, 14)
    atr_pct = atr_val / latest_close if latest_close > 0 else 0.0

    return TimeframeSummary(
        timeframe=timeframe,
        close=latest_close,
        change_pct=change_pct,
        trend=trend,
        sma_20=sma_20,
        sma_50=sma_50,
        price_vs_sma20=price_vs_sma20,
        rsi_14=rsi_val,
        macd_histogram=macd_hist,
        macd_direction=macd_dir,
        bb_pct_b=bb_pct_b,
        volume_trend=vol_trend,
        atr_14=atr_val,
        atr_pct=atr_pct,
    )
