"""EMA 帶狀策略 — 多均線排列趨勢偵測。"""

import pandas as pd

from bot.logging_config import get_logger
from bot.strategy.base import BaseStrategy
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.ema_ribbon")


class EMARibbonStrategy(BaseStrategy):
    """
    EMA 帶狀趨勢策略。

    計算 4 條 EMA（8, 13, 21, 34）。
    買入訊號: 均線從非多頭排列 → 多頭排列（EMA8 > EMA13 > EMA21 > EMA34）
    賣出訊號: 均線從非空頭排列 → 空頭排列（EMA8 < EMA13 < EMA21 < EMA34）
    """

    @property
    def name(self) -> str:
        return "ema_ribbon"

    @property
    def required_candles(self) -> int:
        periods = self.params.get("periods", [8, 13, 21, 34])
        return max(periods) + 10  # EMA 暖機緩衝

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        periods = self.params.get("periods", [8, 13, 21, 34])

        df = df.copy()
        for p in periods:
            df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()

        # 排列判定（按 period 升序：短期 → 長期）
        ema_cols = [f"ema_{p}" for p in sorted(periods)]

        # 多頭排列：短 > 中 > 長
        df["bullish_aligned"] = True
        for i in range(len(ema_cols) - 1):
            df["bullish_aligned"] = df["bullish_aligned"] & (df[ema_cols[i]] > df[ema_cols[i + 1]])

        # 空頭排列：短 < 中 < 長
        df["bearish_aligned"] = True
        for i in range(len(ema_cols) - 1):
            df["bearish_aligned"] = df["bearish_aligned"] & (df[ema_cols[i]] < df[ema_cols[i + 1]])

        # 展幅：(最快 EMA - 最慢 EMA) / 最慢 EMA
        df["ribbon_spread"] = (df[ema_cols[0]] - df[ema_cols[-1]]) / df[ema_cols[-1]].replace(0, 1e-10)

        return df

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        df = self.calculate_indicators(df)

        if len(df) < self.required_candles:
            return Signal.HOLD

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        periods = self.params.get("periods", [8, 13, 21, 34])
        if pd.isna(latest[f"ema_{periods[0]}"]) or pd.isna(prev[f"ema_{periods[0]}"]):
            return Signal.HOLD

        # 從非多頭 → 多頭排列
        if not prev["bullish_aligned"] and latest["bullish_aligned"]:
            logger.debug(
                "EMA Ribbon 多頭排列: %s",
                " > ".join(f"EMA{p}={latest[f'ema_{p}']:.2f}" for p in sorted(periods)),
            )
            return Signal.BUY

        # 從非空頭 → 空頭排列
        if not prev["bearish_aligned"] and latest["bearish_aligned"]:
            logger.debug(
                "EMA Ribbon 空頭排列: %s",
                " < ".join(f"EMA{p}={latest[f'ema_{p}']:.2f}" for p in sorted(periods)),
            )
            return Signal.SELL

        return Signal.HOLD

    def generate_verdict(self, df: pd.DataFrame) -> StrategyVerdict:
        df_calc = self.calculate_indicators(df)
        signal = self.generate_signal(df)

        latest = df_calc.iloc[-1]
        periods = self.params.get("periods", [8, 13, 21, 34])

        ema_values = {}
        for p in periods:
            v = latest[f"ema_{p}"]
            ema_values[p] = v if not pd.isna(v) else 0

        spread = latest["ribbon_spread"] if not pd.isna(latest["ribbon_spread"]) else 0
        is_bullish = bool(latest["bullish_aligned"]) if not pd.isna(latest["bullish_aligned"]) else False
        is_bearish = bool(latest["bearish_aligned"]) if not pd.isna(latest["bearish_aligned"]) else False

        # 信心度：展幅越大，趨勢越強
        if signal != Signal.HOLD:
            confidence = min(1.0, max(0.3, abs(spread) * 100))
        else:
            confidence = 0.0

        alignment = "多頭排列" if is_bullish else "空頭排列" if is_bearish else "無排列"
        ema_str = " | ".join(f"EMA{p}={ema_values[p]:.2f}" for p in sorted(periods))

        return StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=f"EMA({','.join(str(p) for p in periods)}) {ema_str} | {alignment} 展幅={spread:.4f}",
            timeframe=self.timeframe,
            indicators={
                **{f"ema_{p}": round(ema_values[p], 2) for p in periods},
                "ribbon_spread": round(spread, 6),
            },
        )
