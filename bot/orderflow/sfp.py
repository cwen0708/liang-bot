"""Swing Failure Pattern (SFP) 偵測器。"""

from dataclasses import dataclass
from enum import Enum

from bot.logging_config import get_logger

logger = get_logger("orderflow.sfp")


class SFPDirection(str, Enum):
    BULLISH = "bullish"   # 刺穿前低後收回 → 看漲
    BEARISH = "bearish"   # 刺穿前高後收回 → 看跌


@dataclass
class SFPEvent:
    """偵測到的 SFP 事件。"""

    direction: SFPDirection
    swing_price: float        # 被刺穿的前期擺動點價格
    wick_price: float         # 影線最遠端（high/low）
    close_price: float        # 收盤價（收回在擺動點之內）
    bar_index: int
    strength: float           # 0~1，影線越長且收回越深越強


class SwingDetector:
    """偵測價格序列的擺動高點（swing high）和擺動低點（swing low）。"""

    def __init__(self, lookback: int = 5) -> None:
        self.lookback = lookback

    def find_swing_highs(
        self, highs: list[float], closes: list[float] | None = None
    ) -> list[tuple[int, float]]:
        """回傳 (索引, 價格) 的擺動高點列表。"""
        swings = []
        for i in range(self.lookback, len(highs) - self.lookback):
            window_left = highs[i - self.lookback: i]
            window_right = highs[i + 1: i + self.lookback + 1]
            if highs[i] >= max(window_left) and highs[i] >= max(window_right):
                swings.append((i, highs[i]))
        return swings

    def find_swing_lows(
        self, lows: list[float], closes: list[float] | None = None
    ) -> list[tuple[int, float]]:
        """回傳 (索引, 價格) 的擺動低點列表。"""
        swings = []
        for i in range(self.lookback, len(lows) - self.lookback):
            window_left = lows[i - self.lookback: i]
            window_right = lows[i + 1: i + self.lookback + 1]
            if lows[i] <= min(window_left) and lows[i] <= min(window_right):
                swings.append((i, lows[i]))
        return swings


class SFPDetector:
    """
    Swing Failure Pattern 偵測器。

    看漲 SFP：影線（low）刺穿前期擺動低點，但收盤回到擺動點上方
    看跌 SFP：影線（high）刺穿前期擺動高點，但收盤回到擺動點下方
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        wick_threshold: float = 0.001,
    ) -> None:
        """
        Args:
            swing_lookback: 擺動點偵測的回望期間。
            wick_threshold: 最小影線刺穿幅度（相對於擺動點價格的比例）。
        """
        self.swing_detector = SwingDetector(lookback=swing_lookback)
        self.wick_threshold = wick_threshold

    def detect(
        self,
        highs: list[float],
        lows: list[float],
        closes: list[float],
        recency: int = 10,
    ) -> list[SFPEvent]:
        """
        偵測近期的 SFP 事件。

        Args:
            highs: 最高價序列。
            lows: 最低價序列。
            closes: 收盤價序列。
            recency: 只回傳 bar_index 在最近 N 根內的事件。

        Returns:
            SFPEvent 列表（只含近期事件）。
        """
        if len(highs) < self.swing_detector.lookback * 2 + 2:
            return []

        n = len(highs)
        min_idx = n - recency  # SFP 觸發 bar 必須在此索引之後

        results: list[SFPEvent] = []

        # 偵測看漲 SFP（刺穿前低後收回）
        swing_lows = self.swing_detector.find_swing_lows(lows)
        for i in range(len(swing_lows) - 1, -1, -1):
            swing_idx, swing_price = swing_lows[i]
            for j in range(max(swing_idx + 1, min_idx), n):
                if lows[j] < swing_price:
                    penetration = (swing_price - lows[j]) / swing_price
                    if penetration >= self.wick_threshold and closes[j] > swing_price:
                        strength = self._calc_strength(
                            swing_price, lows[j], closes[j], "bullish"
                        )
                        results.append(SFPEvent(
                            direction=SFPDirection.BULLISH,
                            swing_price=swing_price,
                            wick_price=lows[j],
                            close_price=closes[j],
                            bar_index=j,
                            strength=strength,
                        ))
                        break
            # swing point 太舊（遠在 recency 之前），後面的更舊，不用再找
            if swing_idx < min_idx - recency:
                break

        # 偵測看跌 SFP（刺穿前高後收回）
        swing_highs = self.swing_detector.find_swing_highs(highs)
        for i in range(len(swing_highs) - 1, -1, -1):
            swing_idx, swing_price = swing_highs[i]
            for j in range(max(swing_idx + 1, min_idx), n):
                if highs[j] > swing_price:
                    penetration = (highs[j] - swing_price) / swing_price
                    if penetration >= self.wick_threshold and closes[j] < swing_price:
                        strength = self._calc_strength(
                            swing_price, highs[j], closes[j], "bearish"
                        )
                        results.append(SFPEvent(
                            direction=SFPDirection.BEARISH,
                            swing_price=swing_price,
                            wick_price=highs[j],
                            close_price=closes[j],
                            bar_index=j,
                            strength=strength,
                        ))
                        break
            if swing_idx < min_idx - recency:
                break

        return results

    @staticmethod
    def _calc_strength(
        swing_price: float,
        wick_price: float,
        close_price: float,
        direction: str,
    ) -> float:
        """
        SFP 強度：影線刺穿深度 × 收回程度。

        刺穿越深且收回越深 → 強度越高。
        """
        if swing_price == 0:
            return 0.0

        penetration = abs(wick_price - swing_price) / swing_price
        recovery = abs(close_price - swing_price) / swing_price

        # 刺穿深度（越大越好，但不超過 1）
        pen_score = min(penetration * 50, 1.0)
        # 收回程度也是好的，但不超過 1
        rec_score = min(recovery * 20, 1.0)

        return min(pen_score * 0.6 + rec_score * 0.4, 1.0)
