"""Binance USDT-M 永續合約客戶端 — 使用幣安官方 SDK (binance-futures-connector)。"""

from __future__ import annotations

import math

import pandas as pd
from binance.error import ClientError, ServerError
from binance.um_futures import UMFutures

from bot.config.settings import ExchangeConfig, FuturesConfig
from bot.exchange.base_futures import BaseFuturesExchange
from bot.exchange.exceptions import (
    AuthenticationError,
    ExchangeError,
    InsufficientBalanceError,
    OrderError,
    RateLimitError,
    ReduceOnlyError,
)
from bot.logging_config import get_logger
from bot.utils.decorators import retry

logger = get_logger("exchange.futures")

# Binance error_code → 自訂例外映射
_ERROR_MAP: dict[int, type[ExchangeError]] = {
    -2014: AuthenticationError,
    -2015: AuthenticationError,
    -1015: RateLimitError,
    -2010: InsufficientBalanceError,
    -2013: OrderError,
    -2022: ReduceOnlyError,
}


def _map_error(e: ClientError, default_msg: str = "") -> ExchangeError:
    """將 SDK ClientError 映射到自訂例外。"""
    exc_cls = _ERROR_MAP.get(e.error_code, ExchangeError)
    msg = f"{default_msg}: [{e.error_code}] {e.error_message}" if default_msg else f"[{e.error_code}] {e.error_message}"
    return exc_cls(msg)


class FuturesBinanceClient(BaseFuturesExchange):
    """Binance USDT-M 永續合約交易客戶端（官方 SDK）。"""

    def __init__(self, config: ExchangeConfig, futures_config: FuturesConfig) -> None:
        from bot.config.constants import TradingMode

        api_key = config.futures_api_key or config.api_key
        api_secret = config.futures_api_secret or config.api_secret

        if config.testnet:
            base_url = "https://testnet.binancefuture.com"
            key_source = "合約專用" if config.futures_api_key else "共用"
            logger.info("使用幣安合約測試網 (Testnet, %s key)", key_source)
        else:
            base_url = "https://fapi.binance.com"

        self._client = UMFutures(key=api_key, secret=api_secret, base_url=base_url)
        self._default_leverage = futures_config.leverage
        self._margin_type = futures_config.margin_type
        self._is_paper = futures_config.mode == TradingMode.PAPER
        self._is_simulated = self._is_paper and not config.testnet
        self._leverage_set: set[str] = set()

        # 載入交易對資訊
        self._market_info: dict[str, dict] = {}
        self._native_map: dict[str, str] = {}
        self._load_market_info()

        mode_label = "testnet" if (self._is_paper and config.testnet) else (
            "paper" if self._is_paper else "live"
        )
        logger.info(
            "Binance 合約客戶端初始化完成，載入 %d 個交易對，預設槓桿=%dx, 模式=%s",
            len(self._market_info), self._default_leverage, mode_label,
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
            raise ExchangeError(f"載入合約交易對資訊失敗: {e}") from e

        for s in info.get("symbols", []):
            native = s["symbol"]
            base = s.get("baseAsset", "")
            quote = s.get("quoteAsset", "")
            if not base or not quote:
                continue
            slash = f"{base}/{quote}"

            min_qty = 0.0
            step_size = 0.0
            tick_size = 0.0
            min_notional = 0.0
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    min_qty = float(f.get("minQty", 0))
                    step_size = float(f.get("stepSize", 0))
                elif f["filterType"] == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0))
                elif f["filterType"] == "MIN_NOTIONAL":
                    min_notional = float(f.get("notional", 0))

            self._market_info[slash] = {
                "native": native,
                "min_qty": min_qty,
                "step_size": step_size,
                "tick_size": tick_size,
                "min_notional": min_notional,
            }
            self._native_map[native] = slash

    # ─── 槓桿與保證金 ───

    def ensure_leverage_and_margin(self, symbol: str) -> None:
        """確保交易對已設定槓桿和保證金模式（每個交易對只需設定一次）。"""
        if symbol in self._leverage_set:
            return
        if self._is_simulated:
            logger.debug("[模擬] 跳過設定槓桿/保證金模式: %s", symbol)
            self._leverage_set.add(symbol)
            return
        self.set_margin_type(symbol, self._margin_type)
        self.set_leverage(symbol, self._default_leverage)
        self._leverage_set.add(symbol)

    @retry(max_retries=2, delay=0.5)
    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self._client.change_leverage(
                symbol=self._to_native(symbol), leverage=leverage,
            )
            logger.info("已設定 %s 槓桿為 %dx", symbol, leverage)
        except ClientError as e:
            if e.error_code == -4028:  # Leverage not changed
                logger.debug("槓桿未變更: %s", symbol)
            else:
                raise _map_error(e, "設定槓桿失敗") from e
        except ServerError as e:
            raise ExchangeError(f"設定槓桿失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        # Binance API 要求 "CROSSED" / "ISOLATED"，config 可能存 "cross" / "isolated"
        mt = margin_type.upper()
        if mt == "CROSS":
            mt = "CROSSED"
        try:
            self._client.change_margin_type(
                symbol=self._to_native(symbol),
                marginType=mt,
            )
            logger.info("已設定 %s 保證金模式為 %s", symbol, margin_type)
        except ClientError as e:
            if e.error_code == -4046:  # No need to change margin type
                logger.debug("保證金模式未變更: %s", symbol)
            else:
                raise _map_error(e, "設定保證金模式失敗") from e
        except ServerError as e:
            raise ExchangeError(f"設定保證金模式失敗（伺服器錯誤）: {e}") from e

    # ─── 報價與 K 線 ───

    @retry(max_retries=3, delay=1.0)
    def get_ticker(self, symbol: str) -> dict:
        try:
            ticker = self._client.ticker_24hr_price_change(symbol=self._to_native(symbol))
            return {
                "symbol": symbol,
                "bid": float(ticker.get("bidPrice", 0) or 0),
                "ask": float(ticker.get("askPrice", 0) or 0),
                "last": float(ticker["lastPrice"]),
                "volume": float(ticker["volume"]),
                "timestamp": int(ticker.get("closeTime", 0)),
            }
        except ClientError as e:
            raise _map_error(e, "取得報價失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得報價失敗（伺服器錯誤）: {e}") from e

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
            df = pd.DataFrame(
                [row[:6] for row in raw],
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = df[col].astype(float)
            return df
        except ClientError as e:
            raise _map_error(e, "取得合約 K 線失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得合約 K 線失敗（伺服器錯誤）: {e}") from e

    # ─── 帳戶資訊 ───

    PAPER_WALLET_BALANCE = 10000.0

    @retry(max_retries=3, delay=1.0, no_retry_on=(AuthenticationError,))
    def get_futures_balance(self) -> dict:
        if self._is_simulated:
            return {
                "total_wallet_balance": self.PAPER_WALLET_BALANCE,
                "available_balance": self.PAPER_WALLET_BALANCE,
                "total_unrealized_pnl": 0.0,
                "total_margin_balance": self.PAPER_WALLET_BALANCE,
            }
        try:
            account = self._client.account()
            return {
                "total_wallet_balance": float(account.get("totalWalletBalance", 0)),
                "available_balance": float(account.get("availableBalance", 0)),
                "total_unrealized_pnl": float(account.get("totalUnrealizedProfit", 0)),
                "total_margin_balance": float(account.get("totalMarginBalance", 0)),
            }
        except ClientError as e:
            raise _map_error(e, "取得合約餘額失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得合約餘額失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_positions(self) -> list[dict]:
        try:
            positions = self._client.get_position_risk()
            result = []
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt == 0:
                    continue

                native_sym = pos["symbol"]
                symbol = self._from_native(native_sym)
                side = "long" if amt > 0 else "short"
                contracts = abs(amt)
                entry_price = float(pos.get("entryPrice", 0))
                mark_price = float(pos.get("markPrice", 0))
                unrealized_pnl = float(pos.get("unRealizedProfit", 0))
                liq_price = float(pos.get("liquidationPrice", 0))
                leverage = int(pos.get("leverage", 1))
                margin_type = pos.get("marginType", "cross")
                notional = float(pos.get("notional", 0))

                result.append({
                    "symbol": symbol,
                    "side": side,
                    "contracts": contracts,
                    "entry_price": entry_price,
                    "mark_price": mark_price,
                    "unrealized_pnl": unrealized_pnl,
                    "liquidation_price": liq_price,
                    "leverage": leverage,
                    "margin_type": margin_type,
                    "notional": abs(notional),
                })
            return result
        except ClientError as e:
            raise _map_error(e, "取得合約持倉失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得合約持倉失敗（伺服器錯誤）: {e}") from e

    # ─── 下單 ───

    @retry(max_retries=2, delay=0.5, no_retry_on=(ReduceOnlyError,))
    def place_market_order(
        self, symbol: str, side: str, amount: float,
        reduce_only: bool = False,
    ) -> dict:
        amount = self._round_quantity(symbol, amount)
        logger.info(
            "合約市價單: %s %s %.8f reduce_only=%s",
            side.upper(), symbol, amount, reduce_only,
        )
        try:
            kwargs: dict = {
                "symbol": self._to_native(symbol),
                "side": side.upper(),
                "type": "MARKET",
                "quantity": amount,
            }
            if reduce_only:
                kwargs["reduceOnly"] = "true"
            order = self._client.new_order(**kwargs)
            logger.info("合約市價單回應: ID=%s, 狀態=%s", order["orderId"], order["status"])

            # Testnet 市價單可能回傳 status=NEW, executedQty=0
            # 需要查詢訂單取得實際成交資訊
            filled = float(order.get("executedQty", 0))
            if filled == 0 and order.get("status") not in ("CANCELED", "REJECTED", "EXPIRED"):
                import time as _time
                _time.sleep(0.5)
                try:
                    order = self._client.query_order(
                        symbol=self._to_native(symbol),
                        orderId=order["orderId"],
                    )
                    logger.info(
                        "合約市價單查詢確認: filled=%.8f, status=%s",
                        float(order.get("executedQty", 0)), order.get("status"),
                    )
                except Exception as e:
                    logger.warning("查詢市價單成交狀態失敗: %s", e)

            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "合約下單失敗") from e
        except ServerError as e:
            raise OrderError(f"合約下單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float,
        reduce_only: bool = False,
    ) -> dict:
        amount = self._round_quantity(symbol, amount)
        price = self._round_price(symbol, price)
        logger.info(
            "合約限價單: %s %s %.8f @ %.2f reduce_only=%s",
            side.upper(), symbol, amount, price, reduce_only,
        )
        try:
            kwargs: dict = {
                "symbol": self._to_native(symbol),
                "side": side.upper(),
                "type": "LIMIT",
                "quantity": amount,
                "price": price,
                "timeInForce": "GTC",
            }
            if reduce_only:
                kwargs["reduceOnly"] = "true"
            order = self._client.new_order(**kwargs)
            logger.info("合約限價單已提交: ID=%s", order["orderId"])
            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "合約下單失敗") from e
        except ServerError as e:
            raise OrderError(f"合約下單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_stop_market(
        self, symbol: str, side: str, amount: float,
        stop_price: float, reduce_only: bool = True,
    ) -> dict:
        """停損市價單（STOP_MARKET）。"""
        amount = self._round_quantity(symbol, amount)
        stop_price = self._round_price(symbol, stop_price)
        logger.info(
            "合約停損單: %s %s %.8f stop=%.2f",
            side.upper(), symbol, amount, stop_price,
        )
        try:
            kwargs: dict = {
                "symbol": self._to_native(symbol),
                "side": side.upper(),
                "type": "STOP_MARKET",
                "quantity": amount,
                "stopPrice": stop_price,
            }
            if reduce_only:
                kwargs["reduceOnly"] = "true"
            order = self._client.new_order(**kwargs)
            logger.info("停損單已提交: ID=%s", order["orderId"])
            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "停損單失敗") from e
        except ServerError as e:
            raise OrderError(f"停損單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_take_profit_market(
        self, symbol: str, side: str, amount: float,
        stop_price: float, reduce_only: bool = True,
    ) -> dict:
        """停利市價單（TAKE_PROFIT_MARKET）。"""
        amount = self._round_quantity(symbol, amount)
        stop_price = self._round_price(symbol, stop_price)
        logger.info(
            "合約停利單: %s %s %.8f stop=%.2f",
            side.upper(), symbol, amount, stop_price,
        )
        try:
            kwargs: dict = {
                "symbol": self._to_native(symbol),
                "side": side.upper(),
                "type": "TAKE_PROFIT_MARKET",
                "quantity": amount,
                "stopPrice": stop_price,
            }
            if reduce_only:
                kwargs["reduceOnly"] = "true"
            order = self._client.new_order(**kwargs)
            logger.info("停利單已提交: ID=%s", order["orderId"])
            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "停利單失敗") from e
        except ServerError as e:
            raise OrderError(f"停利單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self._client.cancel_order(
                symbol=self._to_native(symbol), orderId=int(order_id),
            )
            logger.info("已取消合約訂單: %s", order_id)
            return True
        except ClientError as e:
            if e.error_code == -2011:  # Unknown order
                logger.warning("合約訂單不存在: %s", order_id)
                return False
            raise _map_error(e, "取消合約訂單失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取消合約訂單失敗（伺服器錯誤）: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_order_status(self, order_id: str, symbol: str) -> dict:
        try:
            order = self._client.query_order(
                symbol=self._to_native(symbol), orderId=int(order_id),
            )
            return self._format_order(order, symbol)
        except ClientError as e:
            raise _map_error(e, "查詢合約訂單失敗") from e
        except ServerError as e:
            raise ExchangeError(f"查詢合約訂單失敗（伺服器錯誤）: {e}") from e

    # ─── 資金費率與保證金 ───

    @retry(max_retries=3, delay=1.0)
    def get_funding_rate(self, symbol: str) -> dict:
        try:
            data = self._client.mark_price(symbol=self._to_native(symbol))
            return {
                "symbol": symbol,
                "funding_rate": float(data.get("lastFundingRate", 0)),
                "next_funding_time": data.get("nextFundingTime"),
                "mark_price": float(data.get("markPrice", 0)),
                "index_price": float(data.get("indexPrice", 0)),
            }
        except ClientError as e:
            raise _map_error(e, "取得資金費率失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得資金費率失敗（伺服器錯誤）: {e}") from e

    def get_margin_ratio(self) -> float:
        """帳戶保證金比率 = 維持保證金 / 保證金餘額。"""
        if self._is_simulated:
            return 0.0
        try:
            account = self._client.account()
            margin_balance = float(account.get("totalMarginBalance", 0))
            if margin_balance <= 0:
                return 0.0
            maintenance = float(account.get("totalMaintMargin", 0))
            return maintenance / margin_balance
        except Exception as e:
            logger.warning("計算保證金比率失敗: %s", e)
            return 0.0

    def get_liquidation_price(self, symbol: str) -> float | None:
        try:
            positions = self.get_positions()
            for pos in positions:
                if pos["symbol"] == symbol:
                    liq = pos.get("liquidation_price", 0)
                    return liq if liq > 0 else None
            return None
        except Exception:
            return None

    def get_min_order_amount(self, symbol: str) -> float:
        info = self._market_info.get(symbol)
        if info:
            return info["min_qty"]
        return 0.0

    def get_min_notional(self, symbol: str) -> float:
        """取得最小名義金額（notional = qty × price）。"""
        info = self._market_info.get(symbol)
        if info:
            return info.get("min_notional", 0.0)
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

    # ─── AggTrades ───

    @retry(max_retries=3, delay=1.0)
    def fetch_agg_trades(self, symbol: str, limit: int = 1000) -> list[dict]:
        """取得最近的 aggTrade 數據。"""
        try:
            trades = self._client.agg_trades(self._to_native(symbol), limit=limit)
            return [
                {
                    "trade_id": t["a"],
                    "price": float(t["p"]),
                    "quantity": float(t["q"]),
                    "timestamp": t["T"],
                    "is_buyer_maker": t["m"],
                }
                for t in trades
            ]
        except ClientError as e:
            raise _map_error(e, "取得合約 aggTrade 失敗") from e
        except ServerError as e:
            raise ExchangeError(f"取得合約 aggTrade 失敗（伺服器錯誤）: {e}") from e

    # ─── 格式化 ───

    def _format_order(self, order: dict, symbol: str) -> dict:
        """將原生 SDK 訂單格式正規化為與舊版 ccxt 一致的格式。"""
        filled = float(order.get("executedQty", 0))
        cum_quote = float(order.get("cumQuote", 0))
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
            "timestamp": order.get("updateTime") or order.get("time"),
        }
