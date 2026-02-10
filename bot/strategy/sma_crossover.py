"""SMA 交叉策略 — 簡單移動平均線黃金/死亡交叉。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal

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
            logger.info(
                "買入訊號 — 黃金交叉: SMA(%d)=%.2f 上穿 SMA(%d)=%.2f",
                self.params["fast_period"], latest["sma_fast"],
                self.params["slow_period"], latest["sma_slow"],
            )
            return Signal.BUY

        # 死亡交叉: 快線從上方穿越慢線
        if prev["sma_fast"] >= prev["sma_slow"] and latest["sma_fast"] < latest["sma_slow"]:
            logger.info(
                "賣出訊號 — 死亡交叉: SMA(%d)=%.2f 下穿 SMA(%d)=%.2f",
                self.params["fast_period"], latest["sma_fast"],
                self.params["slow_period"], latest["sma_slow"],
            )
            return Signal.SELL

        return Signal.HOLD
