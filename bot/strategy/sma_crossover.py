"""SMA 交叉策略 — 簡單移動平均線黃金/死亡交叉。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.sma_crossover")


class SMACrossoverStrategy(BaseStrategy):
    """
    雙均線交叉策略。

    買入訊號: 快線上穿慢線（黃金交叉）
    賣出訊號: 快線下穿慢線（死亡交叉）
    """

    @property
    def name(self) -> str:
        return "sma_crossover"

    @property
    def required_candles(self) -> int:
        return self.params.get("slow_period", 30) + 2

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        fast = self.params.get("fast_period", 10)
        slow = self.params.get("slow_period", 30)

        df = df.copy()
        df["sma_fast"] = df["close"].rolling(window=fast).mean()
        df["sma_slow"] = df["close"].rolling(window=slow).mean()
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        df = self.calculate_indicators(df)

        if len(df) < self.required_candles:
            return Signal.HOLD

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # 確認指標有值
        if pd.isna(latest["sma_fast"]) or pd.isna(prev["sma_fast"]):
            return Signal.HOLD

        # 黃金交叉: 快線從下方穿越慢線
        if prev["sma_fast"] <= prev["sma_slow"] and latest["sma_fast"] > latest["sma_slow"]:
            logger.debug(
                "SMA 黃金交叉: SMA(%d)=%.2f 上穿 SMA(%d)=%.2f",
                self.params["fast_period"], latest["sma_fast"],
                self.params["slow_period"], latest["sma_slow"],
            )
            return Signal.BUY

        # 死亡交叉: 快線從上方穿越慢線
        if prev["sma_fast"] >= prev["sma_slow"] and latest["sma_fast"] < latest["sma_slow"]:
            logger.debug(
                "SMA 死亡交叉: SMA(%d)=%.2f 下穿 SMA(%d)=%.2f",
                self.params["fast_period"], latest["sma_fast"],
                self.params["slow_period"], latest["sma_slow"],
            )
            return Signal.SELL

        return Signal.HOLD

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        df_calc = self.calculate_indicators(df)
        signal = self.generate_signal(df)

        latest = df_calc.iloc[-1]
        fast_period = self.params.get("fast_period", 10)
        slow_period = self.params.get("slow_period", 30)
        sma_fast = latest["sma_fast"] if not pd.isna(latest["sma_fast"]) else 0
        sma_slow = latest["sma_slow"] if not pd.isna(latest["sma_slow"]) else 0

        # 信心度：兩線距離越大，信心越高
        spread = abs(sma_fast - sma_slow) / sma_slow if sma_slow > 0 else 0
        if signal != Signal.HOLD:
            confidence = min(1.0, max(0.3, spread * 50))
        else:
            confidence = 0.0

        cross = "金叉" if sma_fast > sma_slow else "死叉" if sma_fast < sma_slow else "持平"

        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"SMA({fast_period})={sma_fast:.2f} {'>' if sma_fast > sma_slow else '<'} SMA({slow_period})={sma_slow:.2f} | {cross} 差距{spread:.2%}",
            timeframe=self.timeframe,
            indicators={"sma_fast": round(sma_fast, 2), "sma_slow": round(sma_slow, 2), "spread": round(spread, 4)},
        )
