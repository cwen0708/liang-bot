"""MACD 動量策略 — 趨勢動量型策略。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.macd_momentum")


class MACDMomentumStrategy(BaseStrategy):
    """
    MACD 動量策略。

    買入訊號: MACD 線上穿信號線（動量轉多）
    賣出訊號: MACD 線下穿信號線（動量轉空）
    """

    @property
    def name(self) -> str:
        return "macd_momentum"

    @property
    def required_candles(self) -> int:
        slow = self.params.get("slow_period", 26)
        signal_period = self.params.get("signal_period", 9)
        return slow + signal_period + 2

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = self.params.get("fast_period", 12)
        slow = self.params.get("slow_period", 26)
        signal_period = self.params.get("signal_period", 9)

        df = df.copy()
        df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
        df["macd"] = df["ema_fast"] - df["ema_slow"]
        df["macd_signal"] = df["macd"].ewm(span=signal_period, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        df = self.calculate_indicators(df)

        if len(df) < self.required_candles:
            return Signal.HOLD

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(latest["macd"]) or pd.isna(prev["macd_signal"]):
            return Signal.HOLD

        # MACD 上穿信號線 → 買入
        if prev["macd"] <= prev["macd_signal"] and latest["macd"] > latest["macd_signal"]:
            logger.debug(
                "MACD 上穿信號線: MACD=%.4f Signal=%.4f",
                latest["macd"], latest["macd_signal"],
            )
            return Signal.BUY

        # MACD 下穿信號線 → 賣出
        if prev["macd"] >= prev["macd_signal"] and latest["macd"] < latest["macd_signal"]:
            logger.debug(
                "MACD 下穿信號線: MACD=%.4f Signal=%.4f",
                latest["macd"], latest["macd_signal"],
            )
            return Signal.SELL

        return Signal.HOLD

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        df_calc = self.calculate_indicators(df)
        signal = self.generate_signal(df)

        latest = df_calc.iloc[-1]
        macd_val = latest["macd"] if not pd.isna(latest["macd"]) else 0
        signal_val = latest["macd_signal"] if not pd.isna(latest["macd_signal"]) else 0
        hist_val = latest["macd_hist"] if not pd.isna(latest["macd_hist"]) else 0

        # 信心度：柱狀圖（histogram）的絕對值越大，動量越強
        if signal != Signal.HOLD:
            confidence = min(1.0, max(0.3, abs(hist_val) / max(abs(macd_val), 1e-10)))
        else:
            confidence = 0.0

        fast = self.params.get("fast_period", 12)
        slow = self.params.get("slow_period", 26)
        sig = self.params.get("signal_period", 9)

        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=(
                f"MACD({fast},{slow},{sig}) "
                f"MACD={macd_val:.4f} Signal={signal_val:.4f} Hist={hist_val:.4f}"
            ),
            timeframe=self.timeframe,
            indicators={
                "macd": round(macd_val, 6),
                "macd_signal": round(signal_val, 6),
                "macd_hist": round(hist_val, 6),
            },
        )
