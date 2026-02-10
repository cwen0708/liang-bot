"""受困交易者分析器 — 偵測高量反轉區域 + 清算磁鐵效應。"""

from dataclasses import dataclass
from enum import Enum

import numpy as np

from bot.data.models import OrderFlowBar
from bot.logging_config import get_logger

logger = get_logger("orderflow.trapped")


class TrappedSide(str, Enum):
    TRAPPED_LONGS = "trapped_longs"    # 做多者被困 → 看跌
    TRAPPED_SHORTS = "trapped_shorts"  # 做空者被困 → 看漲


@dataclass
class TrappedTraderEvent:
    """受困交易者事件。"""

    side: TrappedSide
    trap_price: float          # 受困區域價格
    volume_at_trap: float      # 受困區域的成交量
    strength: float            # 0~1
    bar_index: int
    magnet_price: float | None = None  # 清算磁鐵價格


class TrappedTraderAnalyzer:
    """
    受困交易者分析器。

    偵測邏輯：
    1. 在某根 K 線出現高成交量（表示大量交易者進場）
    2. 隨後價格反轉（進場者被困在錯誤方向）
    3. 受困者的停損/清算價形成「磁鐵效應」，吸引價格朝該方向運動

    高量區 + 方向反轉 → 受困交易者 → 磁鐵效應
    """

    def __init__(
        self,
        volume_lookback: int = 20,
        volume_threshold_pct: float = 1.5,
        reversal_bars: int = 3,
    ) -> None:
        """
        Args:
            volume_lookback: 計算平均成交量的回望期間。
            volume_threshold_pct: 高量判定閾值（平均成交量的倍數）。
            reversal_bars: 判定反轉需要幾根確認 K 線。
        """
        self.volume_lookback = volume_lookback
        self.volume_threshold_pct = volume_threshold_pct
        self.reversal_bars = reversal_bars

    def detect(self, bars: list[OrderFlowBar]) -> list[TrappedTraderEvent]:
        """
        分析 K 線序列，偵測受困交易者。

        Args:
            bars: OrderFlowBar 序列。

        Returns:
            TrappedTraderEvent 列表。
        """
        if len(bars) < self.volume_lookback + self.reversal_bars + 1:
            return []

        results: list[TrappedTraderEvent] = []
        volumes = [b.volume for b in bars]

        for i in range(self.volume_lookback, len(bars) - self.reversal_bars):
            bar = bars[i]

            # 計算平均成交量
            avg_vol = np.mean(volumes[i - self.volume_lookback: i])
            if avg_vol == 0:
                continue

            # 檢查高量
            vol_ratio = bar.volume / avg_vol
            if vol_ratio < self.volume_threshold_pct:
                continue

            # 判斷高量 K 線方向
            is_bullish_bar = bar.close > bar.open

            # 檢查後續是否反轉
            reversal_bars = bars[i + 1: i + 1 + self.reversal_bars]
            if is_bullish_bar:
                # 買方進場 → 檢查是否後續下跌（做多者被困）
                reversal_count = sum(1 for b in reversal_bars if b.close < b.open)
                if reversal_count >= self.reversal_bars - 1:
                    # 磁鐵價格 = 高量 K 線的低點（多方停損位）
                    magnet = bar.low
                    strength = min(vol_ratio / (self.volume_threshold_pct * 2), 1.0)
                    results.append(TrappedTraderEvent(
                        side=TrappedSide.TRAPPED_LONGS,
                        trap_price=bar.close,
                        volume_at_trap=bar.volume,
                        strength=strength,
                        bar_index=i,
                        magnet_price=magnet,
                    ))
            else:
                # 賣方進場 → 檢查是否後續上漲（做空者被困）
                reversal_count = sum(1 for b in reversal_bars if b.close > b.open)
                if reversal_count >= self.reversal_bars - 1:
                    magnet = bar.high
                    strength = min(vol_ratio / (self.volume_threshold_pct * 2), 1.0)
                    results.append(TrappedTraderEvent(
                        side=TrappedSide.TRAPPED_SHORTS,
                        trap_price=bar.close,
                        volume_at_trap=bar.volume,
                        strength=strength,
                        bar_index=i,
                        magnet_price=magnet,
                    ))

        return results
