"""訂單執行器 — 將風控通過的交易訊號轉換為實際訂單。"""

from bot.config.constants import TradingMode
from bot.exchange.base import BaseExchange
from bot.logging_config import get_logger
from bot.risk.manager import RiskOutput
from bot.strategy.signals import Signal

logger = get_logger("execution.executor")


class OrderExecutor:
    """負責下單與回報。"""

    def __init__(self, exchange: BaseExchange, mode: TradingMode = TradingMode.PAPER) -> None:
        self._exchange = exchange
        self._mode = mode

    @property
    def is_live(self) -> bool:
        return self._mode == TradingMode.LIVE

    def execute(
        self, signal: Signal, symbol: str, risk_output: RiskOutput
    ) -> dict | None:
        """
        執行交易。

        Returns:
            訂單資訊 dict，或 None（模擬模式 / 失敗時）
        """
        side = "buy" if signal == Signal.BUY else "sell"
        quantity = risk_output.quantity

        # 檢查最小下單量
        min_amount = self._exchange.get_min_order_amount(symbol)
        if quantity < min_amount:
            logger.warning(
                "數量 %.8f 低於最小下單量 %.8f，跳過", quantity, min_amount
            )
            return None

        if self._mode == TradingMode.PAPER:
            return self._paper_execute(side, symbol, quantity)
        else:
            return self._live_execute(side, symbol, quantity)

    def place_sl_tp(
        self,
        symbol: str,
        quantity: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> dict | None:
        """
        買入成交後掛 SL/TP 單。

        Returns:
            OCO 訂單資訊 dict（含 tp_order_id, sl_order_id），
            paper 模式回傳模擬資訊，失敗回傳 None。
        """
        if self._mode == TradingMode.PAPER:
            logger.info(
                "[模擬] 掛 SL/TP: %s TP=%.2f SL=%.2f",
                symbol, take_profit_price, stop_loss_price,
            )
            return {
                "oco_id": "paper_oco",
                "tp_order_id": None,
                "sl_order_id": None,
                "symbol": symbol,
                "amount": quantity,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }

        # Live mode — 在交易所掛 OCO 賣單
        try:
            return self._exchange.place_oco_sell(
                symbol, quantity, take_profit_price, stop_loss_price
            )
        except Exception as e:
            logger.error("掛 OCO 賣單失敗: %s（將使用輪詢停損停利）", e)
            return None

    def cancel_sl_tp(self, symbol: str, tp_order_id: str | None, sl_order_id: str | None) -> None:
        """取消掛單中的 SL/TP 單（手動賣出前呼叫）。"""
        if self._mode == TradingMode.PAPER:
            return

        for order_id in [tp_order_id, sl_order_id]:
            if order_id:
                try:
                    self._exchange.cancel_order(order_id, symbol)
                except Exception:
                    pass  # 可能已被成交或取消

    def _paper_execute(self, side: str, symbol: str, quantity: float) -> dict:
        """模擬交易（不實際下單）。"""
        ticker = self._exchange.get_ticker(symbol)
        price = ticker["last"]

        order = {
            "id": "paper_" + str(ticker["timestamp"]),
            "symbol": symbol,
            "side": side,
            "type": "market",
            "amount": quantity,
            "price": price,
            "filled": quantity,
            "status": "closed",
            "timestamp": ticker["timestamp"],
        }

        logger.info(
            "[模擬] %s %s %.8f @ %.2f (總額=%.2f USDT)",
            side.upper(), symbol, quantity, price, quantity * price,
        )
        return order

    def _live_execute(self, side: str, symbol: str, quantity: float) -> dict | None:
        """實盤交易。"""
        logger.info("[實盤] 下單: %s %s %.8f", side.upper(), symbol, quantity)
        order = self._exchange.place_market_order(symbol, side, quantity)
        logger.info(
            "[實盤] 成交: ID=%s, 成交量=%.8f, 均價=%.2f",
            order["id"], order["filled"], order.get("price", 0),
        )
        return order
