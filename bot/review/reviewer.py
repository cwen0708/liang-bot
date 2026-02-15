"""每日復盤引擎。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from bot.llm.client import ClaudeCLIClient
from bot.llm.prompts import FUTURES_SYSTEM_PROMPT, SYSTEM_PROMPT
from bot.logging_config import get_logger
from bot.review.collector import ReviewDataCollector, ReviewData, WeeklyStats
from bot.review.prompt import build_review_prompt
from bot.utils.helpers import parse_json_response

logger = get_logger("review.reviewer")

TW = timezone(timedelta(hours=8))


class DailyReviewer:
    """每日復盤引擎：收集數據 → 組裝 Prompt → LLM 分析 → 儲存結果。"""

    def __init__(self, db, llm_client: ClaudeCLIClient) -> None:
        self.db = db
        self.llm_client = llm_client
        self.collector = ReviewDataCollector(db)
        self._last_review_date: date | None = None

    def should_run(self) -> bool:
        """檢查今天（UTC+8）是否已執行過復盤。"""
        today = datetime.now(TW).date()
        if self._last_review_date == today:
            return False
        # 也查 DB 確認（處理 Bot 重啟的情況）
        try:
            res = (
                self.db._client.table("daily_reviews")
                .select("id")
                .eq("review_date", str(today))
                .limit(1)
                .execute()
            )
            if res.data:
                self._last_review_date = today
                return False
        except Exception:
            pass
        return True

    def run(self, mode: str = "live", hours: int = 24) -> dict | None:
        """執行復盤。

        Args:
            mode: 交易模式（live / paper）
            hours: 回顧時數（預設 24）

        Returns:
            解析後的結果 dict（含 summary, scores, suggestions），或 None。
        """
        today = datetime.now(TW).date()
        logger.info("開始每日復盤 (%s, mode=%s, hours=%d)", today, mode, hours)

        # 1. 收集數據
        data = self.collector.collect(mode=mode, hours=hours)
        weekly = self.collector.collect_weekly_stats(mode=mode)

        # 2. 選擇交易 Prompt（同時附上現貨和合約）
        trading_prompt = SYSTEM_PROMPT
        if data.margin is not None:
            trading_prompt += "\n\n---\n\n" + FUTURES_SYSTEM_PROMPT

        # 3. 組裝復盤 Prompt
        prompt = build_review_prompt(data, weekly, trading_prompt)
        logger.info("復盤 Prompt 長度: %d 字元", len(prompt))

        # 4. 呼叫 LLM
        try:
            response = self.llm_client.call_sync(prompt)
        except Exception as e:
            logger.error("復盤 LLM 呼叫失敗: %s", e)
            return None

        # 5. 解析回傳
        result = self._parse_response(response)
        if result is None:
            logger.error("復盤結果解析失敗")
            return None

        # 6. 寫入 Supabase
        input_stats = self._build_input_stats(data, weekly)
        try:
            self.db.insert_daily_review(
                review_date=today,
                mode=mode,
                summary=result.get("summary", ""),
                scores=result.get("scores", {}),
                suggestions=result.get("suggestions", []),
                input_stats=input_stats,
                model=self.llm_client.model,
            )
            logger.info(
                "復盤完成，整體評分: %.0f%%",
                result.get("scores", {}).get("overall", 0) * 100,
            )
        except Exception as e:
            logger.error("復盤結果寫入 DB 失敗: %s", e)

        self._last_review_date = today
        return result

    def _parse_response(self, raw: str) -> dict | None:
        """從 LLM 回傳中解析復盤 JSON。"""
        data = parse_json_response(raw)
        if data is None:
            # 嘗試從整段文字中尋找 JSON
            logger.warning("parse_json_response 回傳 None，嘗試 fallback 解析")
            return None

        # 驗證必要欄位
        if "summary" not in data:
            # 如果 LLM 回傳了純文字而非 JSON
            data["summary"] = raw[:5000]

        if "scores" not in data:
            data["scores"] = {
                "strategy_accuracy": 0.5,
                "risk_execution": 0.5,
                "pnl_performance": 0.5,
                "prompt_quality": 0.5,
                "overall": 0.5,
            }

        if "suggestions" not in data:
            data["suggestions"] = []

        # 確保 scores 中的值為 float
        for key in ("strategy_accuracy", "risk_execution", "pnl_performance", "prompt_quality", "overall"):
            val = data["scores"].get(key)
            if val is None:
                data["scores"][key] = 0.5
            else:
                data["scores"][key] = max(0.0, min(1.0, float(val)))

        return data

    @staticmethod
    def _build_input_stats(data: ReviewData, weekly: WeeklyStats) -> dict:
        """構建 input_stats 摘要。"""
        total_closed = weekly.win_count + weekly.loss_count
        return {
            "period": "24h",
            "total_decisions": len(data.decisions),
            "total_verdicts": len(data.verdicts),
            "total_orders": len(data.orders),
            "active_positions": len([p for p in data.positions if p.get("quantity", 0) > 0]),
            "weekly_decisions": weekly.total_decisions,
            "weekly_orders": weekly.total_orders,
            "weekly_pnl": round(weekly.total_pnl, 2),
            "weekly_win_rate": round(weekly.win_count / total_closed, 2) if total_closed > 0 else 0,
        }
