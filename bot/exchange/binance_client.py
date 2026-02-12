"""Binance 交易所實作 — 封裝 ccxt。"""

import ccxt
import pandas as pd

from bot.config.settings import ExchangeConfig
from bot.exchange.base import BaseExchange
from bot.exchange.exceptions import (
    AuthenticationError,
    ExchangeError,
    InsufficientBalanceError,
    OrderError,
    RateLimitError,
)
from bot.logging_config import get_logger
from bot.utils.decorators import retry

logger = get_logger("exchange.binance")


class BinanceClient(BaseExchange):
    """Binance 現貨交易客戶端。"""

    def __init__(self, config: ExchangeConfig, *, force_production: bool = False) -> None:
        # force_production: 無視 testnet 設定，強制使用生產 key
        if not force_production and config.testnet and config.testnet_api_key:
            api_key = config.testnet_api_key
            api_secret = config.testnet_api_secret
            use_sandbox = True
            env_label = "Testnet"
        else:
            api_key = config.api_key
            api_secret = config.api_secret
            use_sandbox = False
            env_label = "生產環境"

        options = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
        if use_sandbox:
            options["sandbox"] = True

        self._exchange = ccxt.binance(options)
        self._exchange.load_markets()
        logger.info("Binance 現貨客戶端初始化完成（%s），載入 %d 個交易對", env_label, len(self._exchange.markets))

    @retry(max_retries=3, delay=1.0)
    def get_ticker(self, symbol: str) -> dict:
        try:
            ticker = self._exchange.fetch_ticker(symbol)
            return {
                "symbol": symbol,
                "bid": ticker["bid"],
                "ask": ticker["ask"],
                "last": ticker["last"],
                "volume": ticker["baseVolume"],
                "timestamp": ticker["timestamp"],
            }
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"認證失敗: {e}") from e
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(f"頻率限制: {e}") from e
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得報價失敗: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100, since: int | None = None
    ) -> pd.DataFrame:
        try:
            ohlcv = self._exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit, since=since
            )
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得 K 線失敗: {e}") from e

    @retry(max_retries=3, delay=1.0, no_retry_on=(AuthenticationError,))
    def get_balance(self) -> dict[str, float]:
        try:
            balance = self._exchange.fetch_balance()
            return {
                currency: float(amount)
                for currency, amount in balance["free"].items()
                if isinstance(amount, (int, float)) and float(amount) > 0
            }
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"認證失敗: {e}") from e
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得餘額失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_market_order(self, symbol: str, side: str, amount: float) -> dict:
        logger.info("下市價單: %s %s %.8f", side.upper(), symbol, amount)
        try:
            order = self._exchange.create_order(
                symbol=symbol, type="market", side=side, amount=amount
            )
            logger.info("市價單成交: ID=%s, 狀態=%s", order["id"], order["status"])
            return self._format_order(order)
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"餘額不足: {e}") from e
        except ccxt.BaseError as e:
            raise OrderError(f"下單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float
    ) -> dict:
        logger.info("下限價單: %s %s %.8f @ %.8f", side.upper(), symbol, amount, price)
        try:
            order = self._exchange.create_order(
                symbol=symbol, type="limit", side=side, amount=amount, price=price
            )
            logger.info("限價單已提交: ID=%s", order["id"])
            return self._format_order(order)
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"餘額不足: {e}") from e
        except ccxt.BaseError as e:
            raise OrderError(f"下單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self._exchange.cancel_order(order_id, symbol)
            logger.info("已取消訂單: %s", order_id)
            return True
        except ccxt.OrderNotFound:
            logger.warning("訂單不存在: %s", order_id)
            return False
        except ccxt.BaseError as e:
            raise ExchangeError(f"取消訂單失敗: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_order_status(self, order_id: str, symbol: str) -> dict:
        try:
            order = self._exchange.fetch_order(order_id, symbol)
            return self._format_order(order)
        except ccxt.BaseError as e:
            raise ExchangeError(f"查詢訂單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_oco_sell(
        self,
        symbol: str,
        amount: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> dict:
        """
        掛 OCO 賣單（停利 + 停損同時掛）。

        Returns:
            包含 tp_order_id 和 sl_order_id 的 dict。
        """
        # 停損觸發後的限價：略低於停損價以確保成交
        stop_limit_price = stop_loss_price * 0.998

        logger.info(
            "掛 OCO 賣單: %s qty=%.8f TP=%.2f SL=%.2f",
            symbol, amount, take_profit_price, stop_loss_price,
        )
        try:
            result = self._exchange.create_oco_order(
                symbol=symbol,
                type="limit",
                side="sell",
                amount=amount,
                price=take_profit_price,
                stopPrice=stop_loss_price,
                params={"stopLimitPrice": stop_limit_price},
            )

            # ccxt 回傳格式：包含 orders 陣列或 orderReports
            orders = result.get("orders") or result.get("orderReports") or []
            tp_id, sl_id = None, None
            for o in orders:
                otype = o.get("type", "").upper()
                if otype in ("LIMIT_MAKER", "LIMIT"):
                    tp_id = o.get("id") or o.get("orderId")
                elif otype in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                    sl_id = o.get("id") or o.get("orderId")

            # fallback：如果解析不出，用 result 本身的 id
            if not tp_id and not sl_id:
                tp_id = result.get("id")

            oco_info = {
                "oco_id": result.get("id") or result.get("orderListId"),
                "tp_order_id": str(tp_id) if tp_id else None,
                "sl_order_id": str(sl_id) if sl_id else None,
                "symbol": symbol,
                "amount": amount,
                "take_profit_price": take_profit_price,
                "stop_loss_price": stop_loss_price,
            }
            logger.info(
                "OCO 賣單已掛: TP_ID=%s, SL_ID=%s",
                oco_info["tp_order_id"], oco_info["sl_order_id"],
            )
            return oco_info

        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"餘額不足，無法掛 OCO: {e}") from e
        except ccxt.BaseError as e:
            raise OrderError(f"掛 OCO 賣單失敗: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def fetch_agg_trades(self, symbol: str, limit: int = 1000) -> list[dict]:
        """取得最近的 aggTrade 數據（REST API）。"""
        try:
            trades = self._exchange.fetch_trades(symbol, limit=limit)
            return [
                {
                    "trade_id": t["id"],
                    "price": float(t["price"]),
                    "quantity": float(t["amount"]),
                    "timestamp": t["timestamp"],
                    "is_buyer_maker": t["side"] == "sell",
                }
                for t in trades
            ]
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得 aggTrade 失敗: {e}") from e

    def get_min_order_amount(self, symbol: str) -> float:
        market = self._exchange.market(symbol)
        return float(market.get("limits", {}).get("amount", {}).get("min", 0))

    def fetch_loan_ongoing_orders(self, limit: int = 20) -> list[dict]:
        """查詢進行中的借款訂單（Flexible Loan v2）。"""
        try:
            result = self._exchange.request(
                "loan/flexible/ongoing/orders",
                "sapiV2", "GET", {"limit": limit},
            )
            return result.get("rows", [])
        except ccxt.BaseError as e:
            raise ExchangeError(f"查詢借款失敗: {e}") from e

    def loan_repay(self, loan_coin: str, collateral_coin: str, repay_amount: float) -> dict:
        """還款（Flexible Loan v2）。"""
        try:
            return self._exchange.request(
                "loan/flexible/repay",
                "sapiV2", "POST", {
                    "loanCoin": loan_coin,
                    "collateralCoin": collateral_coin,
                    "repayAmount": str(repay_amount),
                },
            )
        except ccxt.BaseError as e:
            raise ExchangeError(f"還款失敗: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def fetch_loan_adjust_history(
        self, loan_coin: str, collateral_coin: str, limit: int = 100
    ) -> list[dict]:
        """查詢借貸 LTV 調整歷史（Flexible Loan v2）。"""
        try:
            result = self._exchange.request(
                "loan/flexible/ltv/adjustment/history",
                "sapiV2", "GET", {
                    "loanCoin": loan_coin,
                    "collateralCoin": collateral_coin,
                    "limit": limit,
                },
            )
            return result.get("rows", [])
        except ccxt.BaseError as e:
            raise ExchangeError(f"查詢借貸調整歷史失敗: {e}") from e

    def loan_adjust_ltv(
        self, loan_coin: str, collateral_coin: str, adjustment_amount: float, direction: str = "ADDITIONAL"
    ) -> dict:
        """調整 LTV（增加或減少質押物）。direction: ADDITIONAL / REDUCED"""
        try:
            return self._exchange.request(
                "loan/flexible/adjust/ltv",
                "sapiV2", "POST", {
                    "loanCoin": loan_coin,
                    "collateralCoin": collateral_coin,
                    "adjustmentAmount": f"{adjustment_amount:.8f}",
                    "direction": direction,
                },
            )
        except ccxt.BaseError as e:
            raise ExchangeError(f"調整 LTV 失敗: {e}") from e

    @retry(max_retries=2, delay=1.0)
    def get_flexible_earn_position(self, asset: str) -> list[dict]:
        """查詢 Simple Earn Flexible 持倉（特定幣種）。"""
        try:
            result = self._exchange.request(
                "simple-earn/flexible/position",
                "sapi", "GET", {"asset": asset},
            )
            return result.get("rows", [])
        except ccxt.BaseError as e:
            raise ExchangeError(f"查詢 Simple Earn 持倉失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def redeem_flexible_earn(self, product_id: str, amount: float | None = None) -> dict:
        """
        贖回 Simple Earn Flexible 產品。

        Args:
            product_id: 產品 ID（從 get_flexible_earn_position 取得）。
            amount: 贖回數量，None 表示全部贖回。
        """
        params: dict = {"productId": product_id}
        if amount is not None:
            params["amount"] = str(amount)
        else:
            params["redeemAll"] = True

        try:
            result = self._exchange.request(
                "simple-earn/flexible/redeem",
                "sapi", "POST", params,
            )
            logger.info("Simple Earn 贖回成功: productId=%s, amount=%s", product_id, amount or "ALL")
            return result
        except ccxt.BaseError as e:
            raise ExchangeError(f"Simple Earn 贖回失敗: {e}") from e

    def redeem_all_usdt_earn(self) -> float:
        """
        自動贖回所有 USDT Simple Earn Flexible 持倉。

        Returns:
            贖回的總金額（USDT）。若無持倉或贖回失敗則回傳 0。
        """
        try:
            positions = self.get_flexible_earn_position("USDT")
        except Exception as e:
            logger.warning("查詢 USDT Earn 持倉失敗: %s", e)
            return 0.0

        total_redeemed = 0.0
        for pos in positions:
            total_amount = float(pos.get("totalAmount", 0))
            if total_amount <= 0:
                continue

            product_id = pos.get("productId", "")
            if not product_id:
                continue

            try:
                self.redeem_flexible_earn(product_id)
                total_redeemed += total_amount
                logger.info(
                    "已贖回 USDT Earn: productId=%s, 金額=%.4f",
                    product_id, total_amount,
                )
            except Exception as e:
                logger.warning("贖回 USDT Earn 失敗 (productId=%s): %s", product_id, e)

        return total_redeemed

    @staticmethod
    def _format_order(order: dict) -> dict:
        return {
            "id": order["id"],
            "symbol": order["symbol"],
            "side": order["side"],
            "type": order["type"],
            "amount": order["amount"],
            "price": order.get("average") or order.get("price"),
            "filled": order["filled"],
            "status": order["status"],
            "timestamp": order["timestamp"],
        }
