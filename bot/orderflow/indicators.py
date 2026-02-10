"""訂單流核心指標：Delta、CVD、CVD Z-Score。"""

from collections import deque

import numpy as np

from bot.data.models import OrderFlowBar
from bot.logging_config import get_logger

logger = get_logger("orderflow.indicators")


class DeltaCalculator:
    """計算單根 K 線的 Delta（buy_volume - sell_volume）。"""

    @staticmethod
    def calculate(bar: OrderFlowBar) -> float:
        return bar.delta


class CVDCalculator:
    """
    Cumulative Volume Delta（累積量差）計算器。

    CVD = Σ delta_i，追蹤買賣壓力的累積趨勢。
    """

    def __init__(self, max_history: int = 500) -> None:
        self._deltas: deque[float] = deque(maxlen=max_history)
        self._cvd_values: deque[float] = deque(maxlen=max_history)
        self._cumulative: float = 0.0

    @property
    def values(self) -> list[float]:
        return list(self._cvd_values)

    @property
    def deltas(self) -> list[float]:
        return list(self._deltas)

    @property
    def current(self) -> float:
        return self._cvd_values[-1] if self._cvd_values else 0.0

    def update(self, bar: OrderFlowBar) -> float:
        """加入新 bar 並回傳最新 CVD 值。"""
        delta = bar.delta
        self._deltas.append(delta)
        self._cumulative += delta
        self._cvd_values.append(self._cumulative)
        return self._cumulative

    def reset(self) -> None:
        self._deltas.clear()
        self._cvd_values.clear()
        self._cumulative = 0.0


class CVDZScore:
    """
    CVD 的 Z-Score 標準化。

    z = (cvd - mean) / std，用於判斷 CVD 是否處於極端狀態。
    """

    def __init__(self, lookback: int = 20) -> None:
        self.lookback = lookback
        self._z_scores: deque[float] = deque(maxlen=500)

    @property
    def values(self) -> list[float]:
        return list(self._z_scores)

    @property
    def current(self) -> float:
        return self._z_scores[-1] if self._z_scores else 0.0

    def update(self, cvd_values: list[float]) -> float:
        """根據 CVD 歷史序列計算最新 Z-Score。"""
        if len(cvd_values) < self.lookback:
            self._z_scores.append(0.0)
            return 0.0

        window = cvd_values[-self.lookback:]
        arr = np.array(window, dtype=np.float64)
        mean = np.mean(arr)
        std = np.std(arr)

        if std < 1e-10:
            z = 0.0
        else:
            z = float((arr[-1] - mean) / std)

        self._z_scores.append(z)
        return z


class OrderFlowIndicatorEngine:
    """
    訂單流指標引擎：統一管理 Delta/CVD/Z-Score 的計算。

    每次 on_bar() 被呼叫時更新所有指標。
    """

    def __init__(
        self,
        max_history: int = 500,
        zscore_lookback: int = 20,
    ) -> None:
        self.cvd = CVDCalculator(max_history=max_history)
        self.zscore = CVDZScore(lookback=zscore_lookback)
        self._prices: deque[float] = deque(maxlen=max_history)
        self._highs: deque[float] = deque(maxlen=max_history)
        self._lows: deque[float] = deque(maxlen=max_history)
        self._volumes: deque[float] = deque(maxlen=max_history)

    @property
    def prices(self) -> list[float]:
        return list(self._prices)

    @property
    def highs(self) -> list[float]:
        return list(self._highs)

    @property
    def lows(self) -> list[float]:
        return list(self._lows)

    def on_bar(self, bar: OrderFlowBar) -> dict:
        """
        處理新 K 線，更新所有指標。

        Returns:
            包含所有指標的 dict。
        """
        self._prices.append(bar.close)
        self._highs.append(bar.high)
        self._lows.append(bar.low)
        self._volumes.append(bar.volume)

        cvd_value = self.cvd.update(bar)
        z_score = self.zscore.update(self.cvd.values)

        return {
            "delta": bar.delta,
            "delta_pct": bar.delta_pct,
            "cvd": cvd_value,
            "cvd_zscore": z_score,
            "buy_volume": bar.buy_volume,
            "sell_volume": bar.sell_volume,
            "vwap": bar.vwap,
        }

    def reset(self) -> None:
        self.cvd.reset()
        self._prices.clear()
        self._highs.clear()
        self._lows.clear()
        self._volumes.clear()
