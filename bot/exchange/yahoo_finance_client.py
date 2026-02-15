"""Yahoo Finance 數據 adapter — 純分析用途，不支援交易。

透過 yfinance 抓取台灣加權指數（^TWII）等標的的 OHLCV 數據，
格式與 BaseExchange.get_ohlcv() 一致，可直接接入 DataFetcher。
"""

from __future__ import annotations

import math

import pandas as pd
import yfinance as yf

from bot.exchange.base import BaseExchange
from bot.logging_config import get_logger

logger = get_logger("exchange.yahoo_finance")

# yfinance interval mapping
_TF_MAP: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "60m", "4h": "60m",  # 4h 需從 1h resample
    "1d": "1d",
}

# yfinance period 對照（根據 limit 推算需要的歷史長度）
_PERIOD_MAP: dict[str, str] = {
    "1m": "7d", "5m": "60d", "15m": "60d", "30m": "60d",
    "60m": "60d", "1d": "1y",
}


def _calculate_period(tf: str, limit: int) -> str:
    """根據 timeframe 和 limit 推算 yfinance period 參數。"""
    yf_interval = _TF_MAP.get(tf, "60m")
    default = _PERIOD_MAP.get(yf_interval, "60d")

    # 粗估需要的天數
    tf_minutes = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "1d": 1440}
    mins = tf_minutes.get(yf_interval, 60)
    # 台股一天只有 5 小時 = 300 分鐘，加上倍數安全係數
    trading_mins_per_day = 300
    days_needed = math.ceil(limit * mins / trading_mins_per_day) + 5

    if days_needed <= 7:
        return "7d" if yf_interval in ("1m",) else default
    if days_needed <= 30:
        return "1mo"
    if days_needed <= 90:
        return "3mo"
    if days_needed <= 180:
        return "6mo"
    return default


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """將 1h DataFrame resample 為 4h。"""
    if df.empty:
        return df
    df = df.set_index("timestamp")
    resampled = df.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])
    resampled = resampled.reset_index()
    return resampled


class YahooFinanceClient(BaseExchange):
    """Yahoo Finance 數據 client（唯讀，僅實作 get_ohlcv）。"""

    def __init__(self, symbol: str = "^TWII") -> None:
        self._yf_symbol = symbol
        self._ticker = yf.Ticker(symbol)
        logger.info("Yahoo Finance client initialized: %s", symbol)

    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100, **kwargs
    ) -> pd.DataFrame:
        """從 Yahoo Finance 抓取 OHLCV，回傳標準 DataFrame。"""
        yf_interval = _TF_MAP.get(timeframe, "60m")
        period = _calculate_period(timeframe, limit)

        try:
            df = self._ticker.history(period=period, interval=yf_interval)
        except Exception:
            logger.exception("Yahoo Finance fetch failed: %s %s", self._yf_symbol, timeframe)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        if df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        # yfinance 回傳大寫欄名 + index 為 DatetimeIndex
        df = df.reset_index()
        # 辨識 timestamp 欄位（日內用 Datetime，日線用 Date）
        ts_col = "Datetime" if "Datetime" in df.columns else "Date"
        df = df.rename(columns={
            ts_col: "timestamp",
            "Open": "open", "High": "high", "Low": "low",
            "Close": "close", "Volume": "volume",
        })
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        # 4h resample
        if timeframe == "4h" and yf_interval == "60m":
            df = _resample_4h(df)

        cols = ["timestamp", "open", "high", "low", "close", "volume"]
        return df[cols].tail(limit).reset_index(drop=True)

    # ── 以下為 BaseExchange 必要方法，分析模式不支援 ──

    def get_ticker(self, symbol: str) -> dict:
        raise NotImplementedError("YahooFinanceClient is analysis-only")

    def get_balance(self) -> dict[str, float]:
        raise NotImplementedError("YahooFinanceClient is analysis-only")

    def place_market_order(self, symbol: str, side: str, amount: float) -> dict:
        raise NotImplementedError("YahooFinanceClient is analysis-only")

    def place_limit_order(self, symbol: str, side: str, amount: float, price: float) -> dict:
        raise NotImplementedError("YahooFinanceClient is analysis-only")

    def cancel_order(self, order_id: str, symbol: str) -> bool:
        raise NotImplementedError("YahooFinanceClient is analysis-only")

    def get_order_status(self, order_id: str, symbol: str) -> dict:
        raise NotImplementedError("YahooFinanceClient is analysis-only")

    def get_min_order_amount(self, symbol: str) -> float:
        raise NotImplementedError("YahooFinanceClient is analysis-only")
