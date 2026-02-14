"""Binance 現貨交易客戶端 — 使用幣安官方 SDK (binance-connector-python)。"""

from __future__ import annotations

import math

import pandas as pd
from binance.error import ClientError, ServerError
from binance.spot import Spot

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

# Binance error_code → 自訂例外映射
_ERROR_MAP: dict[int, type[ExchangeError]] = {
    -2014: AuthenticationError,   # API-key format invalid
    -2015: AuthenticationError,   # Invalid API-key, IP, or permissions
    -1015: RateLimitError,        # Too many requests
    -2010: InsufficientBalanceError,  # Insufficient balance
    -2013: OrderError,            # Order does not exist
}


def _map_error(e: ClientError, default_msg: str = "") -> ExchangeError:
    """將 SDK ClientError 映射到自訂例外。"""
    exc_cls = _ERROR_MAP.get(e.error_code, ExchangeError)
    msg = f"{default_msg}: [{e.error_code}] {e.error_message}" if default_msg else f"[{e.error_code}] {e.error_message}"
    return exc_cls(msg)


class BinanceClient(BaseExchange):
    """Binance 現貨交易客戶端（官方 SDK）。"""

    def __init__(self, config: ExchangeConfig, *, force_production: bool = False) -> None:
        if not force_production and config.testnet and config.testnet_api_key:
            api_key = config.testnet_api_key
            api_secret = config.testnet_api_secret
            base_url = "https://testnet.binance.vision"
            env_label = "Testnet"
        else:
            api_key = config.api_key
            api_secret = config.api_secret
            base_url = "https://api.binance.com"
            env_label = "生產環境"

        self._client = Spot(api_key=api_key, api_secret=api_secret, base_url=base_url)

        # 載入交易對資訊（symbol 格式轉換 + 最小下單量）
        self._market_info: dict[str, dict] = {}  # "BTC/USDT" → {native: "BTCUSDT", min_qty, ...}
        self._native_map: dict[str, str] = {}    # "BTCUSDT" → "BTC/USDT"
        self._load_market_info()
        logger.info(
            "Binance 現貨客戶端初始化完成（%s），載入 %d 個交易對",
            env_label, len(self._market_info),
        )

    # ─── Symbol 轉換 ───

    def _to_native(self, symbol: str) -> str:
        """'BTC/USDT' → 'BTCUSDT'"""
        return symbol.replace("/", "")

    def _from_native(self, native: str) -> str:
        """'BTCUSDT' → 'BTC/USDT'"""
        return self._native_map.get(native, native)

    def _load_market_info(self) -> None:
        """從 exchange_info 載入交易對資訊。"""
        try:
            info = self._client.exchange_info()
        except (ClientError, ServerError) as e:
            raise ExchangeError(f"載入交易對資訊失敗: {e}") from e

        for s in info.get("symbols", []):
            native = s["symbol"]           # "BTCUSDT"
            base = s.get("baseAsset", "")  # "BTC"
            quote = s.get("quoteAsset", "")  # "USDT"
            if not base or not quote:
                continue
            slash = f"{base}/{quote}"      # "BTC/USDT"

            # 解析最小下單量
            min_qty = 0.0
            min_notional = 0.0
            step_size = 0.0
            tick_size = 0.0
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    min_qty = float(f.get("minQty", 0))
                    step_size = float(f.get("stepSize", 0))
                elif f["filterType"] == "NOTIONAL":
                    min_notional = float(f.get("minNotional", 0))
                elif f["filterType"] == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0))

            self._market_info[slash] = {
                "native": native,
                "min_qty": min_qty,
                "min_notional": min_notional,
                "step_size": step_size,
                "tick_size": tick_size,
                "status": s.get("status"),
            }
            self._native_map[native] = slash

    # ─── 報價 ───

    @retry(max_retries=3, delay=1.0)
    def get_ticker(self, symbol: str) -> dict:
        try:
            ticker = self._client.ticker_24hr(symbol=self._to_native(symbol))
            return {
                "symbol": symbol,
                "bid": float(ticker["bidPrice"]),
                "ask": float(ticker["askPrice"]),
                "last": float(ticker["lastPrice"]),
                "volume": float(ticker["volume"]),
                "timestamp": int(ticker.get("closeTime", 0)),
            }
        except ClientError as e:
            raise _map_error(e, "取得報價失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得報價失敗（伺服器錯誤）: {e}") from e

    # ─── K 線 ───

    @retry(max_retries=3, delay=1.0)
    def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100,
        since: int | None = None,
    ) -> pd.DataFrame:
        try:
            kwargs: dict = {"limit": limit}
            if since is not None:
                kwargs["startTime"] = since
            raw = self._client.klines(self._to_native(symbol), timeframe, **kwargs)
            # 原始回傳: [[open_time, open, high, low, close, volume, ...], ...]
            df = pd.DataFrame(
                [row[:6] for row in raw],
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = df[col].astype(float)
            return df
        except ClientError as e:
            raise _map_error(e, "取得 K 線失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得 K 線失敗（伺服器錯誤）: {e}") from e

    # ─── 餘額 ───

    @retry(max_retries=3, delay=1.0, no_retry_on=(AuthenticationError,))
    def get_balance(self) -> dict[str, float]:
        try:
            account = self._client.account()
            return {
                b["asset"]: float(b["free"])
                for b in account.get("balances", [])
                if float(b.get("free", 0)) > 0
            }
        except ClientError as e:
            raise _map_error(e, "取得餘額失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得餘額失敗（伺服器錯誤）: {e}") from e

    # ─── 下單 ───

    @retry(max_retries=2, delay=0.5)
    def place_market_order(self, symbol: str, side: str, amount: float) -> dict:
        amount = self._round_quantity(symbol, amount)
        logger.info("下市價單: %s %s %.8f", side.upper(), symbol, amount)
        try:
            order = self._client.new_order(
                symbol=self._to_native(symbol),
                side=side.upper(),
                type="MARKET",
                quantity=amount,
            )
            logger.info("市價單回應: ID=%s, 狀態=%s", order["orderId"], order["status"])

            # Testnet 市價單可能回傳 status=NEW, executedQty=0
            filled = float(order.get("executedQty", 0))
            if filled == 0 and order.get("status") not in ("CANCELED", "REJECTED", "EXPIRED"):
                import time as _time
                _time.sleep(0.5)
                try:
                    order = self._client.get_order(
                        symbol=self._to_native(symbol),
                        orderId=order["orderId"],
                    )
                    logger.info(
                        "市價單查詢確認: filled=%.8f, status=%s",
                        float(order.get("executedQty", 0)), order.get("status"),
                    )
                except Exception as e:
                    logger.warning("查詢市價單成交狀態失敗: %s", e)

            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "下單失敗") from e
        except ServerError as e:
            raise OrderError(f"下單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float,
    ) -> dict:
        amount = self._round_quantity(symbol, amount)
        price = self._round_price(symbol, price)
        logger.info("下限價單: %s %s %.8f @ %.8f", side.upper(), symbol, amount, price)
        try:
            order = self._client.new_order(
                symbol=self._to_native(symbol),
                side=side.upper(),
                type="LIMIT",
                quantity=amount,
                price=price,
                timeInForce="GTC",
            )
            logger.info("限價單已提交: ID=%s", order["orderId"])
            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "下單失敗") from e
        except ServerError as e:
            raise OrderError(f"下單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self._client.cancel_order(
                symbol=self._to_native(symbol), orderId=int(order_id),
            )
            logger.info("已取消訂單: %s", order_id)
            return True
        except ClientError as e:
            if e.error_code == -2011:  # Unknown order
                logger.warning("訂單不存在: %s", order_id)
                return False
            raise _map_error(e, "取消訂單失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取消訂單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_order_status(self, order_id: str, symbol: str) -> dict:
        try:
            order = self._client.get_order(
                symbol=self._to_native(symbol), orderId=int(order_id),
            )
            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "查詢訂單失敗") from e
        except ServerError as e:
            raise ExchangeError(f"查詢訂單失敗（伺服器錯誤）: {e}") from e

    # ─── OCO 賣單 ───

    @retry(max_retries=2, delay=0.5)
    def place_oco_sell(
        self,
        symbol: str,
        amount: float,
        take_profit_price: float,
        stop_loss_price: float,
    ) -> dict:
        """掛 OCO 賣單（停利 + 停損同時掛）。"""
        amount = self._round_quantity(symbol, amount)
        take_profit_price = self._round_price(symbol, take_profit_price)
        stop_loss_price = self._round_price(symbol, stop_loss_price)
        stop_limit_price = self._round_price(symbol, stop_loss_price * 0.998)

        logger.info(
            "掛 OCO 賣單: %s qty=%.8f TP=%.2f SL=%.2f",
            symbol, amount, take_profit_price, stop_loss_price,
        )
        try:
            result = self._client.new_oco_order(
                symbol=self._to_native(symbol),
                side="SELL",
                quantity=amount,
                aboveType="LIMIT_MAKER",
                belowType="STOP_LOSS_LIMIT",
                abovePrice=take_profit_price,
                belowPrice=stop_loss_price,
                belowStopPrice=stop_loss_price,
                belowTrailingDelta=None,
                belowTimeInForce="GTC",
            )

            # 解析 OCO 回應
            orders = result.get("orderReports", [])
            tp_id, sl_id = None, None
            for o in orders:
                otype = o.get("type", "").upper()
                if otype in ("LIMIT_MAKER", "LIMIT"):
                    tp_id = o.get("orderId")
                elif otype in ("STOP_LOSS_LIMIT", "STOP_LOSS"):
                    sl_id = o.get("orderId")

            if not tp_id and not sl_id:
                tp_id = result.get("orderListId")

            oco_info = {
                "oco_id": result.get("orderListId"),
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

        except ClientError as e:
            raise _map_error(e, "掛 OCO 賣單失敗") from e
        except ServerError as e:
            raise OrderError(f"掛 OCO 賣單失敗（伺服器錯誤）: {e}") from e

    # ─── AggTrades ───

    @retry(max_retries=3, delay=1.0)
    def fetch_agg_trades(self, symbol: str, limit: int = 1000) -> list[dict]:
        """取得最近的 aggTrade 數據（REST API）。"""
        try:
            trades = self._client.agg_trades(self._to_native(symbol), limit=limit)
            return [
                {
                    "trade_id": t["a"],          # Aggregate tradeId
                    "price": float(t["p"]),       # Price
                    "quantity": float(t["q"]),    # Quantity
                    "timestamp": t["T"],          # Timestamp
                    "is_buyer_maker": t["m"],     # Was the buyer the maker?
                }
                for t in trades
            ]
        except ClientError as e:
            raise _map_error(e, "取得 aggTrade 失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得 aggTrade 失敗（伺服器錯誤）: {e}") from e

    # ─── 最小下單量 ───

    def get_min_order_amount(self, symbol: str) -> float:
        info = self._market_info.get(symbol)
        if info:
            return info["min_qty"]
        return 0.0

    def _round_step(self, value: float, step: float) -> float:
        """根據 step_size/tick_size 截斷數值（向下取整，避免超出精度）。"""
        if step <= 0:
            return value
        precision = max(0, round(-math.log10(step)))
        factor = 10 ** precision
        return math.floor(value * factor) / factor

    def _round_quantity(self, symbol: str, amount: float) -> float:
        """根據交易對的 step_size 截斷下單數量。"""
        info = self._market_info.get(symbol)
        if info and info["step_size"] > 0:
            return self._round_step(amount, info["step_size"])
        return amount

    def _round_price(self, symbol: str, price: float) -> float:
        """根據交易對的 tick_size 截斷價格。"""
        info = self._market_info.get(symbol)
        if info and info["tick_size"] > 0:
            return self._round_step(price, info["tick_size"])
        return price

    # ─── Loan 相關（Flexible Loan v2，使用 sign_request） ───

    def fetch_loan_ongoing_orders(self, limit: int = 20) -> list[dict]:
        """查詢進行中的借款訂單（Flexible Loan v2）。"""
        try:
            result = self._client.sign_request(
                "GET", "/sapi/v2/loan/flexible/ongoing/orders",
                {"limit": limit},
            )
            return result.get("rows", [])
        except ClientError as e:
            raise _map_error(e, "查詢借款失敗") from e
        except ServerError as e:
            raise ExchangeError(f"查詢借款失敗（伺服器錯誤）: {e}") from e

    def loan_repay(self, loan_coin: str, collateral_coin: str, repay_amount: float) -> dict:
        """還款（Flexible Loan v2）。"""
        try:
            return self._client.sign_request(
                "POST", "/sapi/v2/loan/flexible/repay",
                {
                    "loanCoin": loan_coin,
                    "collateralCoin": collateral_coin,
                    "repayAmount": str(repay_amount),
                },
            )
        except ClientError as e:
            raise _map_error(e, "還款失敗") from e
        except ServerError as e:
            raise ExchangeError(f"還款失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def fetch_loan_adjust_history(
        self, loan_coin: str, collateral_coin: str, limit: int = 100,
    ) -> list[dict]:
        """查詢借貸 LTV 調整歷史（Flexible Loan v2）。"""
        try:
            result = self._client.sign_request(
                "GET", "/sapi/v2/loan/flexible/ltv/adjustment/history",
                {
                    "loanCoin": loan_coin,
                    "collateralCoin": collateral_coin,
                    "limit": limit,
                },
            )
            return result.get("rows", [])
        except ClientError as e:
            raise _map_error(e, "查詢借貸調整歷史失敗") from e
        except ServerError as e:
            raise ExchangeError(f"查詢借貸調整歷史失敗（伺服器錯誤）: {e}") from e

    def loan_adjust_ltv(
        self, loan_coin: str, collateral_coin: str,
        adjustment_amount: float, direction: str = "ADDITIONAL",
    ) -> dict:
        """調整 LTV（增加或減少質押物）。direction: ADDITIONAL / REDUCED"""
        try:
            return self._client.sign_request(
                "POST", "/sapi/v2/loan/flexible/adjust/ltv",
                {
                    "loanCoin": loan_coin,
                    "collateralCoin": collateral_coin,
                    "adjustmentAmount": f"{adjustment_amount:.8f}",
                    "direction": direction,
                },
            )
        except ClientError as e:
            raise _map_error(e, "調整 LTV 失敗") from e
        except ServerError as e:
            raise ExchangeError(f"調整 LTV 失敗（伺服器錯誤）: {e}") from e

    # ─── Simple Earn ───

    @retry(max_retries=2, delay=1.0)
    def get_flexible_earn_position(self, asset: str) -> list[dict]:
        """查詢 Simple Earn Flexible 持倉（特定幣種）。"""
        try:
            result = self._client.get_flexible_product_position(asset=asset)
            return result.get("rows", [])
        except ClientError as e:
            raise _map_error(e, "查詢 Simple Earn 持倉失敗") from e
        except ServerError as e:
            raise ExchangeError(f"查詢 Simple Earn 持倉失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def redeem_flexible_earn(self, product_id: str, amount: float | None = None) -> dict:
        """贖回 Simple Earn Flexible 產品。"""
        try:
            kwargs: dict = {}
            if amount is not None:
                kwargs["amount"] = amount
            else:
                kwargs["redeemAll"] = True
            result = self._client.redeem_flexible_product(productId=product_id, **kwargs)
            logger.info("Simple Earn 贖回成功: productId=%s, amount=%s", product_id, amount or "ALL")
            return result
        except ClientError as e:
            raise _map_error(e, "Simple Earn 贖回失敗") from e
        except ServerError as e:
            raise ExchangeError(f"Simple Earn 贖回失敗（伺服器錯誤）: {e}") from e

    def redeem_all_usdt_earn(self) -> float:
        """自動贖回所有 USDT Simple Earn Flexible 持倉。"""
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

    # ─── 格式化 ───

    def _format_order(self, order: dict, symbol: str) -> dict:
        """將原生 SDK 訂單格式正規化為與舊版 ccxt 一致的格式。"""
        # 成交均價：先用 cummulativeQuoteQty / executedQty 計算
        filled = float(order.get("executedQty", 0))
        cum_quote = float(order.get("cummulativeQuoteQty", 0))
        avg_price = (cum_quote / filled) if filled > 0 else float(order.get("price", 0))

        return {
            "id": str(order["orderId"]),
            "symbol": symbol,
            "side": order["side"].lower(),
            "type": order["type"].lower(),
            "amount": float(order.get("origQty", 0)),
            "price": avg_price,
            "filled": filled,
            "status": order["status"].lower(),
            "timestamp": order.get("transactTime") or order.get("time") or order.get("updateTime"),
        }
