"""合約訂單執行器 — 將風控通過的合約訊號轉換為實際訂單。"""

from bot.config.constants import TradingMode
from bot.exchange.base_futures import BaseFuturesExchange
from bot.logging_config import get_logger
from bot.risk.futures_manager import FuturesRiskOutput
from bot.strategy.signals import Signal

logger = get_logger("execution.futures_executor")

# Signal → (side, reduce_only) 映射
_SIGNAL_MAP = {
    Signal.BUY: ("buy", False),      # 開多
    Signal.SHORT: ("sell", False),    # 開空
    Signal.SELL: ("sell", True),      # 平多
    Signal.COVER: ("buy", True),     # 平空
}


class FuturesOrderExecutor:
    """合約訂單執行器。"""

    def __init__(
        self, exchange: BaseFuturesExchange,
        mode: TradingMode = TradingMode.PAPER,
        is_testnet: bool = False,
    ) -> None:
        self._exchange = exchange
        self._mode = mode
        self._is_testnet = is_testnet
        # paper + testnet → 走真實 API（testnet 環境）
        self._use_testnet_live = (mode == TradingMode.PAPER and is_testnet)

    @property
    def is_live(self) -> bool:
        return self._mode == TradingMode.LIVE or self._use_testnet_live

    def execute(
        self, signal: Signal, symbol: str,
        risk_output: FuturesRiskOutput,
    ) -> dict | None:
        """執行合約交易。"""
        mapping = _SIGNAL_MAP.get(signal)
        if not mapping:
            logger.warning("不支援的合約訊號: %s", signal)
            return None

        side, reduce_only = mapping
        quantity = risk_output.quantity

        # 檢查最小下單量
        min_amount = self._exchange.get_min_order_amount(symbol)
        if quantity < min_amount:
            logger.warning(
                "數量 %.8f 低於最小下單量 %.8f，跳過", quantity, min_amount,
            )
            return None

        # 檢查最小名義金額（notional = qty × price）
        min_notional = self._exchange.get_min_notional(symbol)
        if min_notional > 0:
            ticker = self._exchange.get_ticker(symbol)
            price = ticker["last"]
            notional = quantity * price
            if notional < min_notional:
                logger.warning(
                    "%s 名義金額 %.2f 低於最小 %.0f (qty=%.8f, price=%.2f)，跳過",
                    symbol, notional, min_notional, quantity, price,
                )
                return None

        if self._use_testnet_live:
            return self._live_execute(side, symbol, quantity, reduce_only, label="Testnet合約")
        elif self._mode == TradingMode.PAPER:
            return self._paper_execute(side, symbol, quantity, reduce_only)
        else:
            return self._live_execute(side, symbol, quantity, reduce_only, label="實盤合約")

    def place_sl_tp(
        self, symbol: str, quantity: float, position_side: str,
        take_profit_price: float, stop_loss_price: float,
    ) -> dict | None:
        """開倉後掛 SL/TP 單（分別為 Stop Market + Take Profit Market）。"""
        # 平倉方向：多倉用 sell，空倉用 buy
        close_side = "sell" if position_side == "long" else "buy"

        if self._mode == TradingMode.PAPER and not self._use_testnet_live:
            logger.info(
                "[模擬] 合約掛 SL/TP: %s %s TP=%.2f SL=%.2f",
                symbol, position_side, take_profit_price, stop_loss_price,
            )
            return {
                "tp_order_id": None,
                "sl_order_id": None,
            }

        # Live / Testnet mode
        tp_id, sl_id = None, None
        try:
            tp_order = self._exchange.place_take_profit_market(
                symbol, close_side, quantity, take_profit_price,
                reduce_only=True,
            )
            tp_id = str(tp_order["id"])
        except Exception as e:
            logger.error("掛合約停利單失敗: %s", e)

        try:
            sl_order = self._exchange.place_stop_market(
                symbol, close_side, quantity, stop_loss_price,
                reduce_only=True,
            )
            sl_id = str(sl_order["id"])
        except Exception as e:
            logger.error("掛合約停損單失敗: %s", e)

        return {
            "tp_order_id": tp_id,
            "sl_order_id": sl_id,
        }

    def cancel_sl_tp(
        self, symbol: str,
        tp_order_id: str | None,
        sl_order_id: str | None,
    ) -> None:
        """取消掛單中的 SL/TP 單。"""
        if self._mode == TradingMode.PAPER and not self._use_testnet_live:
            return

        for order_id in [tp_order_id, sl_order_id]:
            if order_id:
                try:
                    self._exchange.cancel_order(order_id, symbol)
                except Exception:
                    pass  # 可能已被成交或取消

    def _paper_execute(
        self, side: str, symbol: str, quantity: float,
        reduce_only: bool,
    ) -> dict:
        """模擬合約交易。"""
        ticker = self._exchange.get_ticker(symbol)
        price = ticker["last"]

        order = {
            "id": "paper_futures_" + str(ticker["timestamp"]),
            "symbol": symbol,
            "side": side,
            "type": "market",
            "amount": quantity,
            "price": price,
            "filled": quantity,
            "status": "closed",
            "timestamp": ticker["timestamp"],
        }

        ro_label = " (reduce_only)" if reduce_only else ""
        logger.info(
            "[模擬合約] %s %s %.8f @ %.2f (名義=%.2f USDT)%s",
            side.upper(), symbol, quantity, price, quantity * price, ro_label,
        )
        return order

    def _live_execute(
        self, side: str, symbol: str, quantity: float,
        reduce_only: bool, label: str = "實盤合約",
    ) -> dict | None:
        """實盤 / Testnet 合約交易。"""
        ro_label = " (reduce_only)" if reduce_only else ""
        logger.info("[%s] 下單: %s %s %.8f%s", label, side.upper(), symbol, quantity, ro_label)
        try:
            order = self._exchange.place_market_order(
                symbol, side, quantity, reduce_only=reduce_only,
            )
        except Exception as e:
            logger.error("[%s] %s 下單失敗: %s", label, symbol, e)
            return None
        logger.info(
            "[%s] 成交: ID=%s, 成交量=%.8f, 均價=%.2f",
            label, order["id"], order["filled"], order.get("price", 0),
        )
        return order
