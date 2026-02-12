"""借貸監控模組 — 4 層 LTV 判定 + AI 審核。

從 app.py 拆分而來，負責：
- check_loan_health → check()
- loan_protect → _loan_protect()
- loan_take_profit → _loan_take_profit()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.config.settings import LoanGuardConfig
from bot.logging_config import get_logger
from bot.utils.helpers import parse_json_response

if TYPE_CHECKING:
    from bot.db.supabase_client import SupabaseWriter
    from bot.exchange.binance_client import BinanceClient
    from bot.llm.client import ClaudeCLIClient

logger = get_logger("loan_guardian")

_L1 = "  "
_L2 = "    "


def _parse_ai_json(response: str) -> dict | None:
    """從 AI 回覆中提取 JSON（委派給共用解析器）。"""
    return parse_json_response(response)


class LoanGuardian:
    """借貸再平衡守衛。

    5 層 LTV 判定：
    - >= danger_ltv           → _loan_protect()（買入 + 質押，目標回到 target_ltv）
    - >= danger_ltv - 5%      → 警告（接近危險）
    - <= low_ltv              → _loan_take_profit()（減質押 + 賣出，目標回到 target_ltv）
    - <= low_ltv + 5%         → 提醒（接近低閾值）
    - 中間                    → 安全
    """

    def __init__(
        self,
        exchange: BinanceClient,
        db: SupabaseWriter,
        llm_client: ClaudeCLIClient,
        config: LoanGuardConfig,
    ) -> None:
        self._exchange = exchange
        self._db = db
        self._llm_client = llm_client
        self._config = config
        self._last_ltv: dict[str, float] = {}

    @property
    def config(self) -> LoanGuardConfig:
        return self._config

    @config.setter
    def config(self, value: LoanGuardConfig) -> None:
        self._config = value

    def check(self) -> None:
        """檢查借款 LTV，超過危險閾值時提交 AI 審核後買入質押物。"""
        lg = self._config
        orders = self._exchange.fetch_loan_ongoing_orders()
        if not orders:
            return

        for o in orders:
            loan_coin = o.get("loanCoin", "?")
            collateral_coin = o.get("collateralCoin", "?")
            ltv = float(o.get("currentLTV", 0))
            debt = float(o.get("totalDebt", 0))
            collateral_amt = float(o.get("collateralAmount", 0))
            label = f"{collateral_coin}→{loan_coin}"
            pair_key = f"{collateral_coin}/{loan_coin}"

            ltv_rounded = round(ltv, 4)
            if self._last_ltv.get(pair_key) == ltv_rounded:
                logger.debug("%s[借款] %s LTV=%.1f%% 無變化，跳過", _L1, label, ltv * 100)
                continue
            self._last_ltv[pair_key] = ltv_rounded

            lh_row_id = self._db.insert_loan_health({
                "loan_coin": loan_coin,
                "collateral_coin": collateral_coin,
                "ltv": ltv,
                "total_debt": debt,
                "collateral_amount": collateral_amt,
                "action_taken": "none",
            }, mode="live")

            warn_high = lg.danger_ltv - 0.05
            warn_low = lg.low_ltv + 0.05

            if ltv >= lg.danger_ltv:
                logger.warning(
                    "%s[借款] %s LTV=%.1f%% 超過 %.0f%%！啟動保護流程",
                    _L1, label, ltv * 100, lg.danger_ltv * 100,
                )
                self._loan_protect(o, lh_row_id)
            elif ltv >= warn_high:
                logger.warning(
                    "%s[借款] %s LTV=%.1f%% 接近危險閾值 %.0f%%",
                    _L1, label, ltv * 100, lg.danger_ltv * 100,
                )
            elif ltv <= lg.low_ltv:
                logger.info(
                    "%s[借款] %s LTV=%.1f%% 低於 %.0f%%，啟動獲利了結",
                    _L1, label, ltv * 100, lg.low_ltv * 100,
                )
                self._loan_take_profit(o, lh_row_id)
            elif ltv <= warn_low:
                logger.info(
                    "%s[借款] %s LTV=%.1f%% 接近低閾值 %.0f%%",
                    _L1, label, ltv * 100, lg.low_ltv * 100,
                )
            else:
                logger.info("%s[借款] %s LTV=%.1f%% 安全", _L1, label, ltv * 100)

            try:
                history = self._exchange.fetch_loan_adjust_history(loan_coin, collateral_coin)
                if history:
                    count = self._db.sync_loan_adjustments(history)
                    if count:
                        logger.info("%s[借款] 同步 %s 調整歷史: 新增 %d 筆", _L1, label, count)
            except Exception as e:
                logger.debug("%s[借款] 同步 %s 調整歷史失敗: %s", _L1, label, e)

    def _loan_protect(self, order: dict, lh_row_id: int | None = None) -> None:
        """借款保護：計算所需質押物 → AI 審核 → 買入現貨 → 增加質押。"""
        lg = self._config
        loan_coin = order.get("loanCoin", "?")
        collateral_coin = order.get("collateralCoin", "?")
        ltv = float(order.get("currentLTV", 0))
        debt = float(order.get("totalDebt", 0))
        collateral_amt = float(order.get("collateralAmount", 0))

        collateral_value = debt / ltv if ltv > 0 else 0
        target_collateral_value = debt / lg.target_ltv
        additional_value_usdt = target_collateral_value - collateral_value

        if additional_value_usdt <= 0:
            return

        pair = f"{collateral_coin}/USDT"
        try:
            ticker = self._exchange.get_ticker(pair)
            coin_price = ticker["last"]
        except Exception as e:
            logger.error("%s[借款] 無法取得 %s 報價: %s", _L2, pair, e)
            return

        additional_qty = additional_value_usdt / coin_price
        buy_cost_usdt = additional_qty * coin_price

        try:
            balance = self._exchange.get_balance()
            usdt_available = balance.get("USDT", 0.0) + balance.get("LDUSDT", 0.0)
        except Exception:
            usdt_available = 0.0

        summary = (
            f"# 借款保護 — AI 審核請求\n\n"
            f"## 現況\n"
            f"- 借款: {debt:.2f} {loan_coin}\n"
            f"- 質押: {collateral_amt:.8f} {collateral_coin} (≈ {collateral_value:.2f} USDT)\n"
            f"- 當前 LTV: {ltv:.1%}（危險閾值: {lg.danger_ltv:.0%}）\n"
            f"- {collateral_coin} 現價: {coin_price:.4f} USDT\n\n"
            f"## 提議操作\n"
            f"1. 市價買入 {additional_qty:.8f} {collateral_coin} (≈ {buy_cost_usdt:.2f} USDT)\n"
            f"2. 將買入的 {collateral_coin} 追加為質押物\n"
            f"3. 預期 LTV 降至 ≈ {lg.target_ltv:.0%}\n\n"
            f"## 帳戶狀態\n"
            f"- 可用 USDT: {usdt_available:.2f}\n"
            f"- 買入所需: {buy_cost_usdt:.2f} USDT\n"
            f"- 餘額{'充足' if usdt_available >= buy_cost_usdt else '不足！'}\n\n"
            f"## 請回覆 JSON\n"
            f'回覆格式: {{"approved": true/false, "reason": "理由"}}\n'
            f"只回覆 JSON，不要其他文字。\n"
            f"如果餘額不足、價格異常、或風險過高，請回覆 approved=false。\n"
        )

        logger.info(
            "%s[借款] 需增加 %.8f %s (≈%.2f USDT)，送 AI 審核",
            _L2, additional_qty, collateral_coin, buy_cost_usdt,
        )

        try:
            ai_response = self._llm_client.call_sync(summary)
            logger.info("%s[借款] AI 回覆: %s", _L2, ai_response[:200])
        except Exception as e:
            logger.error("%s[借款] AI 審核失敗: %s，不執行操作", _L2, e)
            return

        decision = _parse_ai_json(ai_response)
        if decision is None:
            logger.warning("%s[借款] AI 回覆非 JSON，視為拒絕: %s", _L2, ai_response[:100])
            return

        approved = decision.get("approved", False)
        reason = decision.get("reason", "無理由")

        if not approved:
            logger.info("%s[借款] AI 拒絕操作: %s", _L2, reason)
            return

        logger.info("%s[借款] AI 同意: %s", _L2, reason)

        if lg.dry_run:
            logger.info(
                "%s[借款] [模擬] 將買入 %.8f %s 並追加質押（未實際執行）",
                _L2, additional_qty, collateral_coin,
            )
            return

        try:
            pre_balance = self._exchange.get_balance()
            existing = pre_balance.get(collateral_coin, 0.0)
        except Exception:
            existing = 0.0

        need_to_buy = additional_qty - existing
        filled_qty = 0.0
        if need_to_buy > 0:
            try:
                buy_order = self._exchange.place_market_order(pair, "buy", need_to_buy)
                filled_qty = buy_order.get("filled", need_to_buy)
                fill_price = buy_order.get("price", coin_price)
                logger.info(
                    "%s[借款] 已買入 %.8f %s @ %.4f",
                    _L2, filled_qty, collateral_coin, fill_price,
                )
            except Exception as e:
                logger.error("%s[借款] 買入 %s 失敗: %s", _L2, collateral_coin, e)
                if existing <= 0:
                    return
        else:
            logger.info(
                "%s[借款] 現貨已有 %.8f %s，無需購買",
                _L2, existing, collateral_coin,
            )

        try:
            post_balance = self._exchange.get_balance()
            actual_available = post_balance.get(collateral_coin, 0.0)
        except Exception:
            actual_available = existing + (filled_qty * 0.999 if need_to_buy > 0 else 0)

        pledge_qty = min(additional_qty, actual_available)

        try:
            self._exchange.loan_adjust_ltv(
                loan_coin, collateral_coin, pledge_qty, direction="ADDITIONAL"
            )
            logger.info(
                "%s[借款] 已追加質押 %.8f %s，LTV 應下降",
                _L2, pledge_qty, collateral_coin,
            )
            self._db.update_loan_health_action(lh_row_id, "protect")
        except Exception as e:
            logger.error(
                "%s[借款] 追加質押失敗: %s（已買入的 %s 留在現貨錢包）",
                _L2, e, collateral_coin,
            )

    def _loan_take_profit(self, order: dict, lh_row_id: int | None = None) -> None:
        """低 LTV 獲利了結：質押物升值過多 → AI 審核 → 減少質押 → 賣出現貨。"""
        lg = self._config
        loan_coin = order.get("loanCoin", "?")
        collateral_coin = order.get("collateralCoin", "?")
        ltv = float(order.get("currentLTV", 0))
        debt = float(order.get("totalDebt", 0))
        collateral_amt = float(order.get("collateralAmount", 0))

        collateral_value = debt / ltv if ltv > 0 else 0
        target_collateral_value = debt / lg.target_ltv
        removable_value_usdt = collateral_value - target_collateral_value

        if removable_value_usdt <= 0:
            return

        pair = f"{collateral_coin}/USDT"
        try:
            ticker = self._exchange.get_ticker(pair)
            coin_price = ticker["last"]
        except Exception as e:
            logger.error("%s[借款] 無法取得 %s 報價: %s", _L2, pair, e)
            return

        removable_qty = removable_value_usdt / coin_price
        sell_revenue_usdt = removable_qty * coin_price

        new_collateral_value = collateral_value - removable_value_usdt
        expected_ltv = debt / new_collateral_value if new_collateral_value > 0 else 1.0

        summary = (
            f"# 借款獲利了結 — AI 審核請求\n\n"
            f"## 現況\n"
            f"- 借款: {debt:.2f} {loan_coin}\n"
            f"- 質押: {collateral_amt:.8f} {collateral_coin} (≈ {collateral_value:.2f} USDT)\n"
            f"- 當前 LTV: {ltv:.1%}（低 LTV 閾值: {lg.low_ltv:.0%}）\n"
            f"- {collateral_coin} 現價: {coin_price:.4f} USDT\n\n"
            f"## 分析\n"
            f"- LTV 偏低代表質押物大幅升值，可取回部分獲利\n"
            f"- 目標 LTV: {lg.target_ltv:.0%}\n\n"
            f"## 提議操作\n"
            f"1. 減少質押 {removable_qty:.8f} {collateral_coin} (≈ {sell_revenue_usdt:.2f} USDT)\n"
            f"2. 市價賣出取回的 {collateral_coin}\n"
            f"3. 預期 LTV 從 {ltv:.1%} 升至 ≈ {expected_ltv:.1%}\n\n"
            f"## 請回覆 JSON\n"
            f'回覆格式: {{"approved": true/false, "reason": "理由"}}\n'
            f"只回覆 JSON，不要其他文字。\n"
            f"如果市場波動劇烈、或質押物可能繼續升值，請回覆 approved=false。\n"
        )

        logger.info(
            "%s[借款] LTV=%.1f%% 低於 %.0f%%，可減少 %.8f %s (≈%.2f USDT)，送 AI 審核",
            _L2, ltv * 100, lg.low_ltv * 100, removable_qty, collateral_coin, sell_revenue_usdt,
        )

        try:
            ai_response = self._llm_client.call_sync(summary)
            logger.info("%s[借款] AI 回覆: %s", _L2, ai_response[:200])
        except Exception as e:
            logger.error("%s[借款] AI 審核失敗: %s，不執行操作", _L2, e)
            return

        decision = _parse_ai_json(ai_response)
        if decision is None:
            logger.warning("%s[借款] AI 回覆非 JSON，視為拒絕: %s", _L2, ai_response[:100])
            return

        approved = decision.get("approved", False)
        reason = decision.get("reason", "無理由")

        if not approved:
            logger.info("%s[借款] AI 拒絕獲利了結: %s", _L2, reason)
            return

        logger.info("%s[借款] AI 同意獲利了結: %s", _L2, reason)

        if lg.dry_run:
            logger.info(
                "%s[借款] [模擬] 將減少質押 %.8f %s 並賣出（未實際執行）",
                _L2, removable_qty, collateral_coin,
            )
            return

        try:
            self._exchange.loan_adjust_ltv(
                loan_coin, collateral_coin, removable_qty, direction="REDUCED"
            )
            logger.info(
                "%s[借款] 已減少質押 %.8f %s",
                _L2, removable_qty, collateral_coin,
            )
        except Exception as e:
            logger.error("%s[借款] 減少質押失敗: %s", _L2, e)
            return

        try:
            sell_order = self._exchange.place_market_order(pair, "sell", removable_qty)
            filled_qty = sell_order.get("filled", removable_qty)
            fill_price = sell_order.get("price", coin_price)
            logger.info(
                "%s[借款] 已賣出 %.8f %s @ %.4f (≈ %.2f USDT)",
                _L2, filled_qty, collateral_coin, fill_price, filled_qty * fill_price,
            )
            self._db.update_loan_health_action(lh_row_id, "take_profit")
        except Exception as e:
            logger.error(
                "%s[借款] 賣出 %s 失敗: %s（取回的幣留在現貨錢包）",
                _L2, collateral_coin, e,
            )
