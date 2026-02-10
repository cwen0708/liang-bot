"""Footprint 分析器 — POC、價值區間、失衡層級。"""

from dataclasses import dataclass

from bot.data.models import FootprintLevel, OrderFlowBar
from bot.logging_config import get_logger

logger = get_logger("orderflow.footprint")


@dataclass
class FootprintProfile:
    """Footprint 分析結果。"""

    poc_price: float                    # Point of Control（最大成交量層級）
    poc_volume: float
    value_area_high: float              # 價值區間上限（70% 成交量）
    value_area_low: float               # 價值區間下限
    imbalance_levels: list[tuple[float, str]]  # (價格, "buy"/"sell") 失衡層級
    total_levels: int


class FootprintAnalyzer:
    """
    Footprint 圖分析器。

    分析 OrderFlowBar 的 footprint（量價分佈）：
    - POC：成交量最大的價格層級
    - 價值區間（Value Area）：涵蓋 70% 成交量的價格區間
    - 失衡層級（Imbalance）：買/賣量差異極大的層級
    """

    def __init__(
        self,
        value_area_pct: float = 0.70,
        imbalance_ratio: float = 3.0,
    ) -> None:
        """
        Args:
            value_area_pct: 價值區間佔總成交量的百分比。
            imbalance_ratio: 判斷失衡的買/賣量比例閾值。
        """
        self.value_area_pct = value_area_pct
        self.imbalance_ratio = imbalance_ratio

    def analyze(self, bar: OrderFlowBar) -> FootprintProfile | None:
        """
        分析單根 K 線的 footprint。

        Returns:
            FootprintProfile 或 None（若無 footprint 數據）。
        """
        if not bar.footprint:
            return None

        levels = sorted(bar.footprint.values(), key=lambda x: x.price)

        # POC — 成交量最大的層級
        poc = max(levels, key=lambda x: x.total_volume)

        # 價值區間 — 從 POC 向外擴展直到涵蓋 value_area_pct 的成交量
        total_volume = sum(lv.total_volume for lv in levels)
        if total_volume == 0:
            return None

        va_high, va_low = self._calc_value_area(levels, poc, total_volume)

        # 失衡層級 — 買/賣量比例超過閾值
        imbalances = self._find_imbalances(levels)

        return FootprintProfile(
            poc_price=poc.price,
            poc_volume=poc.total_volume,
            value_area_high=va_high,
            value_area_low=va_low,
            imbalance_levels=imbalances,
            total_levels=len(levels),
        )

    def _calc_value_area(
        self,
        levels: list[FootprintLevel],
        poc: FootprintLevel,
        total_volume: float,
    ) -> tuple[float, float]:
        """計算價值區間（從 POC 向外擴展）。"""
        target_volume = total_volume * self.value_area_pct
        accumulated = poc.total_volume
        va_high = poc.price
        va_low = poc.price

        poc_idx = next(i for i, lv in enumerate(levels) if lv.price == poc.price)
        upper = poc_idx + 1
        lower = poc_idx - 1

        while accumulated < target_volume and (upper < len(levels) or lower >= 0):
            upper_vol = levels[upper].total_volume if upper < len(levels) else -1
            lower_vol = levels[lower].total_volume if lower >= 0 else -1

            if upper_vol >= lower_vol:
                accumulated += upper_vol
                va_high = levels[upper].price
                upper += 1
            else:
                accumulated += lower_vol
                va_low = levels[lower].price
                lower -= 1

        return va_high, va_low

    def _find_imbalances(
        self, levels: list[FootprintLevel]
    ) -> list[tuple[float, str]]:
        """找出買/賣量嚴重不對稱的層級。"""
        imbalances = []
        for lv in levels:
            if lv.sell_volume > 0 and lv.buy_volume / lv.sell_volume >= self.imbalance_ratio:
                imbalances.append((lv.price, "buy"))
            elif lv.buy_volume > 0 and lv.sell_volume / lv.buy_volume >= self.imbalance_ratio:
                imbalances.append((lv.price, "sell"))
        return imbalances
