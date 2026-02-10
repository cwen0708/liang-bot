"""背離偵測器 — 偵測價格與 CVD 之間的常規/隱藏背離。"""

from dataclasses import dataclass
from enum import Enum

import numpy as np
from scipy.signal import argrelextrema

from bot.logging_config import get_logger

logger = get_logger("orderflow.divergence")


class DivergenceType(str, Enum):
    REGULAR_BULLISH = "regular_bullish"    # 價格新低，CVD 未新低 → 賣壓衰竭
    REGULAR_BEARISH = "regular_bearish"    # 價格新高，CVD 未新高 → 買壓衰竭
    HIDDEN_BULLISH = "hidden_bullish"      # 價格較高低點，CVD 較低低點 → 趨勢延續
    HIDDEN_BEARISH = "hidden_bearish"      # 價格較低高點，CVD 較高高點 → 趨勢延續


@dataclass
class Divergence:
    """偵測到的背離事件。"""

    divergence_type: DivergenceType
    price_points: tuple[float, float]      # (前一個極值, 當前極值)
    cvd_points: tuple[float, float]        # (前一個極值, 當前極值)
    strength: float                         # 背離強度 0~1
    bar_indices: tuple[int, int]           # 極值所在的 bar 索引


class PeakTroughDetector:
    """偵測價格/CVD 序列的波峰（peak）和波谷（trough）。"""

    def __init__(self, order: int = 5) -> None:
        """
        Args:
            order: argrelextrema 的鄰域大小，越大則越平滑。
        """
        self.order = order

    def find_peaks(self, data: list[float]) -> list[int]:
        """回傳波峰的索引列表。"""
        if len(data) < self.order * 2 + 1:
            return []
        arr = np.array(data, dtype=np.float64)
        indices = argrelextrema(arr, np.greater_equal, order=self.order)[0]
        return indices.tolist()

    def find_troughs(self, data: list[float]) -> list[int]:
        """回傳波谷的索引列表。"""
        if len(data) < self.order * 2 + 1:
            return []
        arr = np.array(data, dtype=np.float64)
        indices = argrelextrema(arr, np.less_equal, order=self.order)[0]
        return indices.tolist()


class DivergenceDetector:
    """
    偵測價格與 CVD 之間的背離。

    常規看漲背離：價格做出更低的低點，但 CVD 做出更高的低點（賣壓衰竭）
    常規看跌背離：價格做出更高的高點，但 CVD 做出更低的高點（買壓衰竭）
    隱藏看漲背離：價格做出更高的低點，但 CVD 做出更低的低點（趨勢延續向上）
    隱藏看跌背離：價格做出更低的高點，但 CVD 做出更高的高點（趨勢延續向下）
    """

    def __init__(self, peak_order: int = 5, min_strength: float = 0.1) -> None:
        self.detector = PeakTroughDetector(order=peak_order)
        self.min_strength = min_strength

    def detect(
        self,
        prices: list[float],
        cvd_values: list[float],
    ) -> list[Divergence]:
        """
        掃描整個序列，回傳所有偵測到的背離。

        Args:
            prices: 收盤價序列。
            cvd_values: CVD 值序列（長度須與 prices 一致）。

        Returns:
            Divergence 列表。
        """
        if len(prices) != len(cvd_values) or len(prices) < 11:
            return []

        results: list[Divergence] = []

        # 偵測波峰（看跌背離）
        price_peaks = self.detector.find_peaks(prices)
        cvd_peaks = self.detector.find_peaks(cvd_values)

        if len(price_peaks) >= 2 and len(cvd_peaks) >= 2:
            results.extend(
                self._check_bearish_divergences(prices, cvd_values, price_peaks, cvd_peaks)
            )

        # 偵測波谷（看漲背離）
        price_troughs = self.detector.find_troughs(prices)
        cvd_troughs = self.detector.find_troughs(cvd_values)

        if len(price_troughs) >= 2 and len(cvd_troughs) >= 2:
            results.extend(
                self._check_bullish_divergences(prices, cvd_values, price_troughs, cvd_troughs)
            )

        return [d for d in results if d.strength >= self.min_strength]

    def _check_bullish_divergences(
        self,
        prices: list[float],
        cvd_values: list[float],
        price_troughs: list[int],
        cvd_troughs: list[int],
    ) -> list[Divergence]:
        results = []
        # 取最近兩個波谷比較
        pt = price_troughs[-2:]
        ct = cvd_troughs[-2:]

        p1, p2 = prices[pt[0]], prices[pt[1]]
        c1, c2 = cvd_values[ct[0]], cvd_values[ct[1]]

        # 常規看漲：價格更低低，CVD 更高低
        if p2 < p1 and c2 > c1:
            strength = self._calc_strength(p1, p2, c1, c2)
            results.append(Divergence(
                divergence_type=DivergenceType.REGULAR_BULLISH,
                price_points=(p1, p2),
                cvd_points=(c1, c2),
                strength=strength,
                bar_indices=(pt[0], pt[1]),
            ))

        # 隱藏看漲：價格更高低，CVD 更低低
        if p2 > p1 and c2 < c1:
            strength = self._calc_strength(p1, p2, c1, c2)
            results.append(Divergence(
                divergence_type=DivergenceType.HIDDEN_BULLISH,
                price_points=(p1, p2),
                cvd_points=(c1, c2),
                strength=strength,
                bar_indices=(pt[0], pt[1]),
            ))

        return results

    def _check_bearish_divergences(
        self,
        prices: list[float],
        cvd_values: list[float],
        price_peaks: list[int],
        cvd_peaks: list[int],
    ) -> list[Divergence]:
        results = []
        pp = price_peaks[-2:]
        cp = cvd_peaks[-2:]

        p1, p2 = prices[pp[0]], prices[pp[1]]
        c1, c2 = cvd_values[cp[0]], cvd_values[cp[1]]

        # 常規看跌：價格更高高，CVD 更低高
        if p2 > p1 and c2 < c1:
            strength = self._calc_strength(p1, p2, c1, c2)
            results.append(Divergence(
                divergence_type=DivergenceType.REGULAR_BEARISH,
                price_points=(p1, p2),
                cvd_points=(c1, c2),
                strength=strength,
                bar_indices=(pp[0], pp[1]),
            ))

        # 隱藏看跌：價格更低高，CVD 更高高
        if p2 < p1 and c2 > c1:
            strength = self._calc_strength(p1, p2, c1, c2)
            results.append(Divergence(
                divergence_type=DivergenceType.HIDDEN_BEARISH,
                price_points=(p1, p2),
                cvd_points=(c1, c2),
                strength=strength,
                bar_indices=(pp[0], pp[1]),
            ))

        return results

    @staticmethod
    def _calc_strength(
        p1: float, p2: float, c1: float, c2: float
    ) -> float:
        """
        計算背離強度：兩個序列方向相反的幅度差異越大，強度越高。

        正規化到 0~1 之間。
        """
        if p1 == 0 or c1 == 0:
            return 0.0

        price_change = abs(p2 - p1) / abs(p1)
        cvd_change = abs(c2 - c1) / (abs(c1) + 1e-10)

        # 取兩個變化幅度的幾何平均，再用 tanh 壓縮到 0~1
        raw = (price_change * cvd_change) ** 0.5
        return float(np.tanh(raw * 5))
