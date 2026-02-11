"""風險管理器 — 在策略訊號和訂單執行之間做風控把關。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from bot.config.settings import SpotConfig
from bot.logging_config import get_logger
from bot.risk.metrics import RiskMetrics
from bot.risk.position_sizer import PercentageSizer
from bot.strategy.signals import Signal

logger = get_logger("risk.manager")


@dataclass
class RiskOutput:
    """風控評估結果。"""
    approved: bool
    quantity: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    reason: str = ""


class RiskManager:
    """
    風控管理器。

    職責:
    1. 部位大小計算
    2. 停損 / 停利價位（支援 ATR 動態或固定百分比）
    3. 最大持倉數檢查
    4. 每日虧損限制
    5. 預計算風控指標供 LLM 參考
    """

    def __init__(self, config: SpotConfig) -> None:
        self.config = config
        self.sizer = PercentageSizer(config.max_position_pct)
        self._open_positions: dict[str, dict] = {}
        self._daily_pnl: float = 0.0
        self._pnl_date: date = date.today()

    @property
    def open_position_count(self) -> int:
        return len(self._open_positions)

    # ------------------------------------------------------------------
    # 預計算風控指標（LLM 決策前呼叫）
    # ------------------------------------------------------------------

    def pre_calculate_metrics(
        self,
        signal: Signal,
        symbol: str,
        price: float,
        balance: float,
        ohlcv: pd.DataFrame | None = None,
    ) -> RiskMetrics | None:
        """預先計算多種風控指標，供 LLM 決策參考。

        只在 BUY 信號時計算（SELL/HOLD 不需要開倉指標）。
        任何單一指標計算失敗不會影響其他指標。
        """
        if signal != Signal.BUY:
            return None

        self._reset_daily_pnl_if_needed()

        # 基本風控檢查（僅標記，不阻止預計算）
        reason = ""
        if self._daily_pnl < -(balance * self.config.max_daily_loss_pct):
            reason = f"已達每日虧損限制 ({self.config.max_daily_loss_pct * 100:.1f}%)"
        elif self.open_position_count >= self.config.max_open_positions:
            reason = f"已達最大持倉數 ({self.config.max_open_positions})"
        elif symbol in self._open_positions:
            reason = f"已持有 {symbol}"

        # ATR + SL/TP
        sl_distance, tp_distance, atr_val = self._calc_sl_tp_distance(price, ohlcv)
        sl_price = price - sl_distance
        tp_price = price + tp_distance
        rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0.0
        passes_rr = rr_ratio >= self.config.min_risk_reward

        if not passes_rr and not reason:
            reason = f"R:R {rr_ratio:.2f} < {self.config.min_risk_reward:.1f}"

        metrics = RiskMetrics(
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            sl_distance=sl_distance,
            tp_distance=tp_distance,
            risk_reward_ratio=rr_ratio,
            atr_value=atr_val,
            atr_used=atr_val > 0 and self.config.atr.enabled,
            passes_min_rr=passes_rr,
            reason=reason,
        )

        # Fibonacci
        try:
            from bot.utils.indicators import compute_fibonacci_levels
            fib = compute_fibonacci_levels(ohlcv)
            if fib:
                metrics.fib_levels = fib
        except Exception:
            logger.debug("Fibonacci 計算失敗", exc_info=True)

        # 支撐壓力位
        try:
            from bot.utils.indicators import compute_support_resistance
            sr = compute_support_resistance(ohlcv)
            metrics.support_levels = sr["support"]
            metrics.resistance_levels = sr["resistance"]
        except Exception:
            logger.debug("支撐壓力位計算失敗", exc_info=True)

        # 布林帶
        try:
            from bot.utils.indicators import compute_bollinger_bands
            bb = compute_bollinger_bands(ohlcv)
            if bb:
                metrics.bb_upper = bb["upper"]
                metrics.bb_mid = bb["mid"]
                metrics.bb_lower = bb["lower"]
                metrics.bb_pct_b = bb["pct_b"]
        except Exception:
            logger.debug("布林帶計算失敗", exc_info=True)

        return metrics

    # ------------------------------------------------------------------
    # SL/TP 計算（ATR 動態 + fallback 固定百分比）
    # ------------------------------------------------------------------

    def _calc_sl_tp_distance(
        self, price: float, ohlcv: pd.DataFrame | None,
    ) -> tuple[float, float, float]:
        """計算 SL/TP 距離。ATR 啟用且數據充足時使用動態計算，否則 fallback。

        Returns:
            (sl_distance, tp_distance, atr_value)
        """
        from bot.utils.indicators import compute_atr

        atr = 0.0
        if self.config.atr.enabled and ohlcv is not None:
            atr = compute_atr(ohlcv, self.config.atr.period)

        if atr > 0:
            sl_distance = atr * self.config.atr.sl_multiplier
            tp_distance = atr * self.config.atr.tp_multiplier
            logger.debug(
                "ATR=%.2f, SL距離=%.2f (×%.1f), TP距離=%.2f (×%.1f)",
                atr, sl_distance, self.config.atr.sl_multiplier,
                tp_distance, self.config.atr.tp_multiplier,
            )
        else:
            sl_distance = price * self.config.stop_loss_pct
            tp_distance = price * self.config.take_profit_pct

        return sl_distance, tp_distance, atr

    # ------------------------------------------------------------------
    # 風控評估（最終把關，LLM 決策後呼叫）
    # ------------------------------------------------------------------

    def evaluate(
        self, signal: Signal, symbol: str, price: float, balance: float,
        ohlcv: pd.DataFrame | None = None,
    ) -> RiskOutput:
        """評估交易訊號是否通過風控。每日虧損限制只阻止買入，不阻止賣出。"""
        self._reset_daily_pnl_if_needed()

        if signal == Signal.BUY:
            return self._evaluate_buy(symbol, price, balance, ohlcv)
        elif signal == Signal.SELL:
            return self._evaluate_sell(symbol, price)
        else:
            return RiskOutput(approved=False, reason="HOLD 訊號")

    def _evaluate_buy(
        self, symbol: str, price: float, balance: float,
        ohlcv: pd.DataFrame | None = None,
    ) -> RiskOutput:
        # 每日虧損限制（只阻止開新倉，不阻止賣出）
        if self._daily_pnl < -(balance * self.config.max_daily_loss_pct):
            reason = f"已達每日虧損限制 ({self.config.max_daily_loss_pct * 100:.1f}%)"
            logger.warning(reason)
            return RiskOutput(approved=False, reason=reason)

        # 最大持倉數
        if self.open_position_count >= self.config.max_open_positions:
            reason = f"已達最大持倉數 ({self.config.max_open_positions})"
            logger.warning(reason)
            return RiskOutput(approved=False, reason=reason)

        # 已持有該幣
        if symbol in self._open_positions:
            reason = f"已持有 {symbol}"
            logger.info(reason)
            return RiskOutput(approved=False, reason=reason)

        # 計算部位
        quantity = self.sizer.calculate(balance, price)
        if quantity <= 0:
            return RiskOutput(approved=False, reason="計算數量為 0")

        # 動態 SL/TP
        sl_distance, tp_distance, _ = self._calc_sl_tp_distance(price, ohlcv)
        stop_loss = price - sl_distance
        take_profit = price + tp_distance

        logger.info(
            "風控通過 BUY %s: 數量=%.8f, 停損=%.2f, 停利=%.2f",
            symbol, quantity, stop_loss, take_profit,
        )

        return RiskOutput(
            approved=True,
            quantity=quantity,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
        )

    def _evaluate_sell(self, symbol: str, price: float) -> RiskOutput:
        if symbol not in self._open_positions:
            reason = f"未持有 {symbol}，無法賣出"
            logger.info(reason)
            return RiskOutput(approved=False, reason=reason)

        position = self._open_positions[symbol]
        quantity = position["quantity"]

        logger.info("風控通過 SELL %s: 數量=%.8f", symbol, quantity)
        return RiskOutput(approved=True, quantity=quantity)

    def add_position(
        self,
        symbol: str,
        quantity: float,
        entry_price: float,
        tp_order_id: str | None = None,
        sl_order_id: str | None = None,
    ) -> None:
        """記錄新持倉（含 SL/TP 掛單 ID）。"""
        self._open_positions[symbol] = {
            "quantity": quantity,
            "entry_price": entry_price,
            "tp_order_id": tp_order_id,
            "sl_order_id": sl_order_id,
        }
        logger.info("新增持倉: %s qty=%.8f entry=%.2f", symbol, quantity, entry_price)

    def remove_position(self, symbol: str, exit_price: float) -> float:
        """移除持倉並記錄損益。"""
        if symbol not in self._open_positions:
            return 0.0

        position = self._open_positions.pop(symbol)
        pnl = (exit_price - position["entry_price"]) * position["quantity"]
        self._daily_pnl += pnl

        logger.info(
            "移除持倉: %s PnL=%.2f USDT (入場=%.2f, 出場=%.2f)",
            symbol, pnl, position["entry_price"], exit_price,
        )
        return pnl

    def check_stop_loss_take_profit(
        self, symbol: str, current_price: float,
        ohlcv: pd.DataFrame | None = None,
    ) -> Signal:
        """檢查是否觸發停損或停利（paper 模式用輪詢價格判斷）。"""
        if symbol not in self._open_positions:
            return Signal.HOLD

        position = self._open_positions[symbol]
        entry = position["entry_price"]

        sl_distance, tp_distance, _ = self._calc_sl_tp_distance(entry, ohlcv)
        stop_loss = entry - sl_distance
        take_profit = entry + tp_distance

        if current_price <= stop_loss:
            logger.warning("觸發停損: %s 現價=%.2f <= 停損=%.2f", symbol, current_price, stop_loss)
            return Signal.SELL

        if current_price >= take_profit:
            logger.info("觸發停利: %s 現價=%.2f >= 停利=%.2f", symbol, current_price, take_profit)
            return Signal.SELL

        return Signal.HOLD

    def get_sl_tp_order_ids(self, symbol: str) -> tuple[str | None, str | None]:
        """取得持倉的 SL/TP 掛單 ID。"""
        pos = self._open_positions.get(symbol)
        if not pos:
            return None, None
        return pos.get("tp_order_id"), pos.get("sl_order_id")

    def has_exchange_sl_tp(self, symbol: str) -> bool:
        """該持倉是否有交易所掛單中的 SL/TP。"""
        tp_id, sl_id = self.get_sl_tp_order_ids(symbol)
        return bool(tp_id or sl_id)

    def _reset_daily_pnl_if_needed(self) -> None:
        today = date.today()
        if today != self._pnl_date:
            self._daily_pnl = 0.0
            self._pnl_date = today
