"""LLM 決策引擎 — 彙整各策略結論後呼叫 LLM 做最終判斷。"""

from bot.config.settings import LLMConfig
from bot.llm.client import ClaudeCLIClient
from bot.llm.prompts import build_decision_prompt
from bot.llm.schemas import LLMDecision, PortfolioState
from bot.llm.summarizer import summarize_portfolio, summarize_risk_metrics, summarize_verdicts
from bot.logging_config import get_logger
from bot.risk.metrics import RiskMetrics
from bot.strategy.signals import Signal, StrategyVerdict
from bot.utils.helpers import parse_json_response

logger = get_logger("llm.decision_engine")


class LLMDecisionEngine:
    """
    LLM 決策引擎 — 核心決策協調器。

    流程：
    1. 收集所有策略的 StrategyVerdict
    2. 摘要化各策略結論 + 倉位狀態為 Markdown
    3. 透過 Claude CLI 呼叫 LLM
    4. 解析 JSON 回傳 → LLMDecision
    5. 若 LLM 失敗，fallback 為加權投票
    """

    VALID_ACTIONS = {"BUY", "SELL", "HOLD", "SHORT", "COVER"}

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.enabled = config.enabled
        self._client = ClaudeCLIClient(config) if config.enabled else None

    async def decide(
        self,
        verdicts: list[StrategyVerdict],
        portfolio: PortfolioState,
        symbol: str,
        current_price: float,
        market_type: str = "spot",
        risk_metrics: RiskMetrics | None = None,
        mtf_summary: str = "",
    ) -> LLMDecision:
        """
        根據策略結論和倉位狀態做出 LLM 決策。

        Args:
            verdicts: 各策略的結論報告。
            portfolio: 當前投資組合狀態。
            symbol: 交易對。
            current_price: 當前價格。
            market_type: "spot" 或 "futures"。

        Returns:
            LLMDecision 決策結果。
        """
        if not self.enabled or self._client is None:
            logger.info("LLM 未啟用，使用 fallback 決策")
            return self._fallback_decision(verdicts)

        try:
            # 1. 摘要化
            strategy_summary = summarize_verdicts(verdicts)
            portfolio_summary = summarize_portfolio(portfolio)
            risk_summary = ""
            if risk_metrics is not None:
                risk_summary = summarize_risk_metrics(risk_metrics, symbol, current_price)

            # 2. 組建提示詞
            prompt = build_decision_prompt(
                strategy_summaries=strategy_summary,
                portfolio_state=portfolio_summary,
                symbol=symbol,
                current_price=current_price,
                market_type=market_type,
                risk_metrics_summary=risk_summary,
                mtf_summary=mtf_summary,
            )

            # 3. 呼叫 LLM
            response = await self._client.call(prompt)

            # 4. 解析決策
            decision = self._parse_decision(response)
            logger.info(
                "LLM 決策: %s (信心 %.2f) — %s",
                decision.action, decision.confidence, decision.reasoning[:100],
            )
            return decision

        except Exception as e:
            logger.warning("LLM 決策失敗，使用 fallback: %s", e)
            return self._fallback_decision(verdicts)

    def decide_sync(
        self,
        verdicts: list[StrategyVerdict],
        portfolio: PortfolioState,
        symbol: str,
        current_price: float,
        market_type: str = "spot",
        risk_metrics: RiskMetrics | None = None,
        mtf_summary: str = "",
    ) -> LLMDecision:
        """同步版本的 decide。"""
        import asyncio
        return asyncio.run(
            self.decide(verdicts, portfolio, symbol, current_price, market_type, risk_metrics, mtf_summary),
        )

    @staticmethod
    def _parse_decision(response: str) -> LLMDecision:
        """從 LLM 回傳文字中解析 JSON 決策。"""
        data = parse_json_response(response)
        if data is None:
            logger.warning("無法從 LLM 回傳中解析 JSON，使用 HOLD")
            return LLMDecision(action="HOLD", confidence=0.0, reasoning="無法解析 LLM 回傳")

        try:
            decision = LLMDecision(**data)

            # action 白名單驗證
            if decision.action not in LLMDecisionEngine.VALID_ACTIONS:
                logger.warning(
                    "LLM 回傳無效 action '%s'，改為 HOLD", decision.action,
                )
                decision.action = "HOLD"
                decision.confidence = 0.0

            return decision
        except (TypeError, ValueError) as e:
            logger.warning("JSON 解析失敗: %s", e)
            return LLMDecision(action="HOLD", confidence=0.0, reasoning=f"JSON 解析失敗: {e}")

    def _fallback_decision(self, verdicts: list[StrategyVerdict]) -> LLMDecision:
        """加權投票 fallback 決策。"""
        if not verdicts:
            return LLMDecision(action="HOLD", confidence=0.0, reasoning="無策略結論")

        buy_score = 0.0
        sell_score = 0.0
        total_weight = 0.0

        for v in verdicts:
            w = 1.0
            total_weight += w
            if v.signal == Signal.BUY:
                buy_score += v.confidence * w
            elif v.signal == Signal.SELL:
                sell_score += v.confidence * w

        if total_weight > 0:
            buy_score /= total_weight
            sell_score /= total_weight

        if buy_score > sell_score and buy_score > 0.3:
            action = "BUY"
            confidence = buy_score
        elif sell_score > buy_score and sell_score > 0.3:
            action = "SELL"
            confidence = sell_score
        else:
            action = "HOLD"
            confidence = 0.0

        return LLMDecision(
            action=action,
            confidence=confidence,
            reasoning=f"Fallback 加權投票: 買={buy_score:.3f}, 賣={sell_score:.3f}",
        )
