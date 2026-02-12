"""VWAP 均值回歸策略 — 成交量加權平均價偏離回歸。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.vwap_reversion")


class VWAPReversionStrategy(BaseStrategy):
    """
    VWAP 均值回歸策略。

    計算滾動 VWAP 及其標準差帶（±1σ）。
    買入訊號: 價格從 VWAP-1σ 下方回升突破
    賣出訊號: 價格從 VWAP+1σ 上方回落跌破
    """

    @property
    def name(self) -> str:
        return "vwap_reversion"

    @property
    def required_candles(self) -> int:
        return self.params.get("period", 20) + 2

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        period = self.params.get("period", 20)
        band_mult = self.params.get("band_mult", 1.0)

        df = df.copy()
        # Typical price = (high + low + close) / 3
        df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3

        # Rolling VWAP = sum(typical_price * volume) / sum(volume)
        tp_vol = df["typical_price"] * df["volume"]
        vol_sum = df["volume"].rolling(window=period).sum().replace(0, 1e-10)
        df["vwap"] = tp_vol.rolling(window=period).sum() / vol_sum

        # 標準差帶
        df["vwap_std"] = (df["close"] - df["vwap"]).rolling(window=period).std()
        df["vwap_upper"] = df["vwap"] + band_mult * df["vwap_std"]
        df["vwap_lower"] = df["vwap"] - band_mult * df["vwap_std"]

        # Z-score: 價格偏離 VWAP 的標準差倍數
        df["vwap_zscore"] = (df["close"] - df["vwap"]) / df["vwap_std"].replace(0, 1e-10)

        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        df = self.calculate_indicators(df)

        if len(df) < self.required_candles:
            return Signal.HOLD

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(latest["vwap"]) or pd.isna(prev["vwap_lower"]):
            return Signal.HOLD

        # 從下帶下方回升突破 → 買入
        if prev["close"] < prev["vwap_lower"] and latest["close"] >= latest["vwap_lower"]:
            logger.debug(
                "VWAP 下帶回升: 價格 %.2f 回升至下帶 %.2f 上方",
                latest["close"], latest["vwap_lower"],
            )
            return Signal.BUY

        # 從上帶上方回落跌破 → 賣出
        if prev["close"] > prev["vwap_upper"] and latest["close"] <= latest["vwap_upper"]:
            logger.debug(
                "VWAP 上帶回落: 價格 %.2f 回落至上帶 %.2f 下方",
                latest["close"], latest["vwap_upper"],
            )
            return Signal.SELL

        return Signal.HOLD

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        df_calc = self.calculate_indicators(df)
        signal = self.generate_signal(df)

        latest = df_calc.iloc[-1]
        vwap = latest["vwap"] if not pd.isna(latest["vwap"]) else 0
        vwap_upper = latest["vwap_upper"] if not pd.isna(latest["vwap_upper"]) else 0
        vwap_lower = latest["vwap_lower"] if not pd.isna(latest["vwap_lower"]) else 0
        zscore = latest["vwap_zscore"] if not pd.isna(latest["vwap_zscore"]) else 0

        # 信心度：Z-score 越極端，信心越高
        if signal != Signal.HOLD:
            confidence = min(1.0, max(0.3, abs(zscore) / 2.0))
        else:
            confidence = 0.0

        period = self.params.get("period", 20)
        band_mult = self.params.get("band_mult", 1.0)

        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=(
                f"VWAP({period},{band_mult}σ) "
                f"價格={latest['close']:.2f} | VWAP={vwap:.2f} "
                f"上帶={vwap_upper:.2f} 下帶={vwap_lower:.2f} | "
                f"Z-score={zscore:.2f}"
            ),
            timeframe=self.timeframe,
            indicators={
                "vwap": round(vwap, 2),
                "vwap_upper": round(vwap_upper, 2),
                "vwap_lower": round(vwap_lower, 2),
                "vwap_zscore": round(zscore, 4),
            },
        )
