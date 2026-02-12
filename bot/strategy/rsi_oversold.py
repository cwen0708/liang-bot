"""RSI 超買超賣策略 — 均值回歸型策略。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.rsi_oversold")


class RSIOversoldStrategy(BaseStrategy):
    """
    RSI 超買超賣策略。

    買入訊號: RSI 從超賣區（< oversold）回升
    賣出訊號: RSI 從超買區（> overbought）回落
    """

    @property
    def name(self) -> str:
        return "rsi_oversold"

    @property
    def required_candles(self) -> int:
        return self.params.get("period", 14) + 2

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        period = self.params.get("period", 14)

        df = df.copy()
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.rolling(window=period).mean()
        avg_loss = loss.rolling(window=period).mean()

        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        df = self.calculate_indicators(df)

        if len(df) < self.required_candles:
            return Signal.HOLD

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(latest["rsi"]) or pd.isna(prev["rsi"]):
            return Signal.HOLD

        oversold = self.params.get("oversold", 30)
        overbought = self.params.get("overbought", 70)

        # 從超賣區回升 → 買入
        if prev["rsi"] < oversold and latest["rsi"] >= oversold:
            logger.debug(
                "RSI 從超賣回升: %.1f → %.1f (閾值 %d)",
                prev["rsi"], latest["rsi"], oversold,
            )
            return Signal.BUY

        # 從超買區回落 → 賣出
        if prev["rsi"] > overbought and latest["rsi"] <= overbought:
            logger.debug(
                "RSI 從超買回落: %.1f → %.1f (閾值 %d)",
                prev["rsi"], latest["rsi"], overbought,
            )
            return Signal.SELL

        return Signal.HOLD

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        df_calc = self.calculate_indicators(df)
        signal = self.generate_signal(df)

        rsi_val = df_calc.iloc[-1]["rsi"] if not pd.isna(df_calc.iloc[-1]["rsi"]) else 50.0
        oversold = self.params.get("oversold", 30)
        overbought = self.params.get("overbought", 70)

        # 信心度：RSI 越極端，信心越高
        if signal == Signal.BUY:
            confidence = min(1.0, (oversold - rsi_val + 10) / oversold)
        elif signal == Signal.SELL:
            confidence = min(1.0, (rsi_val - overbought + 10) / (100 - overbought))
        else:
            confidence = 0.0

        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=max(0.0, confidence),
            reasoning=f"RSI({self.params.get('period', 14)})={rsi_val:.1f} | 超賣<{oversold} 超買>{overbought}",
            timeframe=self.timeframe,
            indicators={"rsi": round(rsi_val, 2)},
        )
