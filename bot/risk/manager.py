"""風險管理器 — 在策略訊號和訂單執行之間做風控把關。"""

from dataclasses import dataclass
from datetime import date

from bot.config.settings import SpotConfig
from bot.logging_config import get_logger
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
    2. 停損 / 停利價位
    3. 最大持倉數檢查
    4. 每日虧損限制
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

    def evaluate(
        self, signal: Signal, symbol: str, price: float, balance: float,
    ) -> RiskOutput:
        """評估交易訊號是否通過風控。每日虧損限制只阻止買入，不阻止賣出。"""
        self._reset_daily_pnl_if_needed()

        if signal == Signal.BUY:
            return self._evaluate_buy(symbol, price, balance)
        elif signal == Signal.SELL:
            return self._evaluate_sell(symbol, price)
        else:
            return RiskOutput(approved=False, reason="HOLD 訊號")

    def _evaluate_buy(self, symbol: str, price: float, balance: float) -> RiskOutput:
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

        stop_loss = price * (1 - self.config.stop_loss_pct)
        take_profit = price * (1 + self.config.take_profit_pct)

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

    def check_stop_loss_take_profit(self, symbol: str, current_price: float) -> Signal:
        """檢查是否觸發停損或停利（paper 模式用輪詢價格判斷）。"""
        if symbol not in self._open_positions:
            return Signal.HOLD

        position = self._open_positions[symbol]
        entry = position["entry_price"]

        stop_loss = entry * (1 - self.config.stop_loss_pct)
        take_profit = entry * (1 + self.config.take_profit_pct)

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
