"""策略路由器 — 收集各策略結論，交由 LLM 決策。"""

from bot.logging_config import get_logger
from bot.strategy.signals import StrategyVerdict

logger = get_logger("strategy.router")


class StrategyRouter:
    """
    多策略路由器。

    收集各策略的 StrategyVerdict，所有決策交由 LLM 處理。
    LLM 失敗時直接 HOLD（不做 fallback 加權投票）。
    """

    def __init__(self) -> None:
        self._verdicts: list[StrategyVerdict] = []

    def collect(self, verdict: StrategyVerdict) -> None:
        """收集一個策略的結論。"""
        self._verdicts.append(verdict)

    def get_verdicts(self) -> list[StrategyVerdict]:
        """取得所有收集到的結論。"""
        return list(self._verdicts)

    def clear(self) -> None:
        """清空收集的結論。"""
        self._verdicts.clear()
