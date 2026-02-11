"""布林帶突破策略 — 波動率突破型策略。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.bollinger_breakout")


class BollingerBreakoutStrategy(BaseStrategy):
    """
    布林帶突破策略。

    買入訊號: 價格從下軌下方回升突破下軌（超跌反彈）
    賣出訊號: 價格從上軌上方回落跌破上軌（超漲回調）
    """

    @property
    def name(self) -> str:
        return "bollinger_breakout"

    @property
    def required_candles(self) -> int:
        return self.params.get("period", 20) + 2

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        period = self.params.get("period", 20)
        std_dev = self.params.get("std_dev", 2.0)

        df = df.copy()
        df["bb_mid"] = df["close"].rolling(window=period).mean()
        rolling_std = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_mid"] + std_dev * rolling_std
        df["bb_lower"] = df["bb_mid"] - std_dev * rolling_std
        # %B 指標：(price - lower) / (upper - lower)
        band_width = df["bb_upper"] - df["bb_lower"]
        df["bb_pct_b"] = (df["close"] - df["bb_lower"]) / band_width.replace(0, 1e-10)
        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        df = self.calculate_indicators(df)

        if len(df) < self.required_candles:
            return Signal.HOLD

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if pd.isna(latest["bb_upper"]) or pd.isna(prev["bb_lower"]):
            return Signal.HOLD

        # 從下軌下方回升突破 → 買入
        if prev["close"] < prev["bb_lower"] and latest["close"] >= latest["bb_lower"]:
            logger.info(
                "買入訊號 — 突破下軌: 價格 %.2f 回升至下軌 %.2f 上方",
                latest["close"], latest["bb_lower"],
            )
            return Signal.BUY

        # 從上軌上方回落跌破 → 賣出
        if prev["close"] > prev["bb_upper"] and latest["close"] <= latest["bb_upper"]:
            logger.info(
                "賣出訊號 — 跌破上軌: 價格 %.2f 回落至上軌 %.2f 下方",
                latest["close"], latest["bb_upper"],
            )
            return Signal.SELL

        return Signal.HOLD

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        df_calc = self.calculate_indicators(df)
        signal = self.generate_signal(df)

        latest = df_calc.iloc[-1]
        pct_b = latest["bb_pct_b"] if not pd.isna(latest["bb_pct_b"]) else 0.5

        # 信心度：%B 越極端，信心越高
        if signal == Signal.BUY:
            confidence = min(1.0, max(0.3, 1.0 - pct_b))
        elif signal == Signal.SELL:
            confidence = min(1.0, max(0.3, pct_b))
        else:
            confidence = 0.0

        bb_mid = latest["bb_mid"] if not pd.isna(latest["bb_mid"]) else 0
        bb_upper = latest["bb_upper"] if not pd.isna(latest["bb_upper"]) else 0
        bb_lower = latest["bb_lower"] if not pd.isna(latest["bb_lower"]) else 0

        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=(
                f"BB({self.params.get('period', 20)},{self.params.get('std_dev', 2.0)}) "
                f"價格={latest['close']:.2f} | 上軌={bb_upper:.2f} 中軌={bb_mid:.2f} 下軌={bb_lower:.2f} | %B={pct_b:.2f}"
            ),
            indicators={
                "bb_upper": round(bb_upper, 2),
                "bb_mid": round(bb_mid, 2),
                "bb_lower": round(bb_lower, 2),
                "bb_pct_b": round(pct_b, 4),
            },
        )
