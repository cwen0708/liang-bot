"""吸收偵測器 — 偵測大量被吸收而價格不動的情況。"""

from dataclasses import dataclass
from enum import Enum

import numpy as np

from bot.logging_config import get_logger

logger = get_logger("orderflow.absorption")


class AbsorptionDirection(str, Enum):
    BULLISH = "bullish"   # 大量賣壓被吸收，價格未跌 → 看漲
    BEARISH = "bearish"   # 大量買壓被吸收，價格未漲 → 看跌


@dataclass
class AbsorptionEvent:
    """偵測到的吸收事件。"""

    direction: AbsorptionDirection
    price_slope: float        # 價格斜率
    cvd_slope: float          # CVD 斜率
    slope_ratio: float        # |price_slope / cvd_slope|，越接近 0 吸收越明顯
    strength: float           # 0~1
    bar_index: int


class AbsorptionDetector:
    """
    吸收偵測器：當 CVD 大幅變化但價格幾乎不動時，代表吸收正在發生。

    核心指標：Price/CVD 斜率比
    - 比值趨近 0：大量交易但價格不動 → 有人在吸收
    - CVD 上升但價格不漲 → bearish absorption（有人在賣）
    - CVD 下降但價格不跌 → bullish absorption（有人在買）
    """

    def __init__(
        self,
        lookback: int = 10,
        slope_ratio_threshold: float = 0.3,
        min_cvd_change: float = 0.01,
    ) -> None:
        """
        Args:
            lookback: 計算斜率的回望 K 線數。
            slope_ratio_threshold: Price/CVD 斜率比低於此值視為吸收。
            min_cvd_change: CVD 最小變化幅度（排除無交易量的情況）。
        """
        self.lookback = lookback
        self.slope_ratio_threshold = slope_ratio_threshold
        self.min_cvd_change = min_cvd_change

    def detect(
        self,
        prices: list[float],
        cvd_values: list[float],
    ) -> AbsorptionEvent | None:
        """
        在最近 lookback 根 K 線中偵測吸收。

        Returns:
            AbsorptionEvent 或 None。
        """
        if len(prices) < self.lookback or len(cvd_values) < self.lookback:
            return None

        price_window = np.array(prices[-self.lookback:], dtype=np.float64)
        cvd_window = np.array(cvd_values[-self.lookback:], dtype=np.float64)

        # 計算斜率（線性回歸）
        x = np.arange(self.lookback, dtype=np.float64)
        price_slope = self._linear_slope(x, price_window)
        cvd_slope = self._linear_slope(x, cvd_window)

        # CVD 需要有足夠變化
        cvd_range = abs(cvd_window[-1] - cvd_window[0])
        cvd_mean = np.mean(np.abs(cvd_window)) + 1e-10
        cvd_relative_change = cvd_range / cvd_mean

        if cvd_relative_change < self.min_cvd_change:
            return None

        # 計算斜率比
        abs_cvd_slope = abs(cvd_slope)
        if abs_cvd_slope < 1e-10:
            return None

        price_normalized = abs(price_slope) / (np.mean(price_window) + 1e-10)
        cvd_normalized = abs_cvd_slope / (cvd_mean + 1e-10)

        if cvd_normalized < 1e-10:
            return None

        slope_ratio = price_normalized / cvd_normalized

        if slope_ratio >= self.slope_ratio_threshold:
            return None

        # 判斷方向
        if cvd_slope < 0:
            # CVD 下降但價格沒跌 → bullish absorption
            direction = AbsorptionDirection.BULLISH
        else:
            # CVD 上升但價格沒漲 → bearish absorption
            direction = AbsorptionDirection.BEARISH

        # 強度 = 1 - slope_ratio（比值越小，吸收越強）
        strength = min(1.0 - slope_ratio / self.slope_ratio_threshold, 1.0)

        return AbsorptionEvent(
            direction=direction,
            price_slope=float(price_slope),
            cvd_slope=float(cvd_slope),
            slope_ratio=float(slope_ratio),
            strength=max(strength, 0.0),
            bar_index=len(prices) - 1,
        )

    @staticmethod
    def _linear_slope(x: np.ndarray, y: np.ndarray) -> float:
        """簡易線性回歸斜率。"""
        n = len(x)
        if n < 2:
            return 0.0
        x_mean = np.mean(x)
        y_mean = np.mean(y)
        numerator = np.sum((x - x_mean) * (y - y_mean))
        denominator = np.sum((x - x_mean) ** 2)
        if abs(denominator) < 1e-10:
            return 0.0
        return float(numerator / denominator)
