"""策略路由器 — 收集各策略結論，分派至 LLM 或加權投票。"""

from bot.logging_config import get_logger
from bot.strategy.signals import Signal, StrategyVerdict

logger = get_logger("strategy.router")


class StrategyRouter:
    """
    多策略路由器。

    收集各策略的 StrategyVerdict，支援兩種決策模式：
    1. LLM 模式：將結論傳給 LLMDecisionEngine
    2. 加權投票模式：根據設定的權重做加權平均

    LLM 模式由外部 app 層協調（Router 只負責收集和加權投票 fallback）。
    """

    def __init__(
        self,
        fallback_weights: dict[str, float] | None = None,
    ) -> None:
        self.fallback_weights = fallback_weights or {}
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

    def weighted_vote(self) -> StrategyVerdict:
        """
        加權投票決策（LLM 不可用時的 fallback）。

        計算加權分數，選出信心最高的方向。
        """
        if not self._verdicts:
            return StrategyVerdict(
                strategy_name="router_weighted_vote",
                signal=Signal.HOLD,
                confidence=0.0,
                reasoning="無策略結論可用",
            )

        buy_score = 0.0
        sell_score = 0.0
        total_weight = 0.0
        evidence: list[str] = []

        for verdict in self._verdicts:
            weight = self.fallback_weights.get(verdict.strategy_name, 1.0)
            total_weight += weight

            if verdict.signal == Signal.BUY:
                buy_score += verdict.confidence * weight
                evidence.append(f"{verdict.strategy_name}: BUY ({verdict.confidence:.2f}) × {weight:.1f}")
            elif verdict.signal == Signal.SELL:
                sell_score += verdict.confidence * weight
                evidence.append(f"{verdict.strategy_name}: SELL ({verdict.confidence:.2f}) × {weight:.1f}")
            else:
                evidence.append(f"{verdict.strategy_name}: HOLD ({verdict.confidence:.2f})")

        if total_weight > 0:
            buy_score /= total_weight
            sell_score /= total_weight

        if buy_score > sell_score and buy_score > 0:
            signal = Signal.BUY
            confidence = buy_score
        elif sell_score > buy_score and sell_score > 0:
            signal = Signal.SELL
            confidence = sell_score
        else:
            signal = Signal.HOLD
            confidence = 0.0

        return StrategyVerdict(
            strategy_name="router_weighted_vote",
            signal=signal,
            confidence=confidence,
            reasoning=f"加權投票: 買={buy_score:.3f}, 賣={sell_score:.3f}",
            key_evidence=evidence,
        )
