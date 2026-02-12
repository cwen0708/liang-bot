"""Binance USDT-M 永續合約客戶端 — 封裝 ccxt。"""

import ccxt
import pandas as pd

from bot.config.settings import ExchangeConfig, FuturesConfig
from bot.exchange.base_futures import BaseFuturesExchange
from bot.exchange.exceptions import (
    AuthenticationError,
    ExchangeError,
    InsufficientBalanceError,
    OrderError,
    RateLimitError,
)
from bot.logging_config import get_logger
from bot.utils.decorators import retry

logger = get_logger("exchange.futures")


class FuturesBinanceClient(BaseFuturesExchange):
    """Binance USDT-M 永續合約交易客戶端。"""

    def __init__(self, config: ExchangeConfig, futures_config: FuturesConfig) -> None:
        from bot.config.constants import TradingMode

        # 合約 testnet 有獨立 key（與現貨 testnet 不同系統）
        api_key = config.futures_api_key or config.api_key
        api_secret = config.futures_api_secret or config.api_secret

        options = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }

        if config.testnet:
            options["sandbox"] = True
            key_source = "合約專用" if config.futures_api_key else "共用"
            logger.info("使用幣安合約測試網 (Testnet, %s key)", key_source)

        self._exchange = ccxt.binance(options)
        self._exchange.load_markets()
        self._default_leverage = futures_config.leverage
        self._margin_type = futures_config.margin_type
        self._is_paper = futures_config.mode == TradingMode.PAPER
        # 純模擬 = paper 且非 testnet（testnet 需要真實 API 互動）
        self._is_simulated = self._is_paper and not config.testnet
        self._leverage_set: set[str] = set()  # 已設定槓桿的交易對

        mode_label = "testnet" if (self._is_paper and config.testnet) else (
            "paper" if self._is_paper else "live"
        )
        logger.info(
            "Binance 合約客戶端初始化完成，載入 %d 個交易對，預設槓桿=%dx, 模式=%s",
            len(self._exchange.markets), self._default_leverage, mode_label,
        )

    def ensure_leverage_and_margin(self, symbol: str) -> None:
        """確保交易對已設定槓桿和保證金模式（每個交易對只需設定一次）。

        Paper 模式下跳過，因為 set_margin_mode / set_leverage 需要合約交易權限。
        """
        if symbol in self._leverage_set:
            return
        if self._is_simulated:
            logger.debug("[模擬] 跳過設定槓桿/保證金模式: %s", symbol)
            self._leverage_set.add(symbol)
            return
        self.set_margin_type(symbol, self._margin_type)
        self.set_leverage(symbol, self._default_leverage)
        self._leverage_set.add(symbol)

    # ─── 報價與 K 線 ───

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
        self, symbol: str, timeframe: str = "1h", limit: int = 100,
        since: int | None = None,
    ) -> pd.DataFrame:
        try:
            ohlcv = self._exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit, since=since,
            )
            df = pd.DataFrame(
                ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得合約 K 線失敗: {e}") from e

    # ─── 帳戶資訊 ───

    # Paper 模式模擬錢包餘額（USDT）
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
            balance = self._exchange.fetch_balance()
            info = balance.get("info", {})

            # ccxt binance futures balance
            total_wallet = float(info.get("totalWalletBalance", 0))
            available = float(info.get("availableBalance", 0))
            unrealized = float(info.get("totalUnrealizedProfit", 0))
            margin_balance = float(info.get("totalMarginBalance", 0))

            return {
                "total_wallet_balance": total_wallet,
                "available_balance": available,
                "total_unrealized_pnl": unrealized,
                "total_margin_balance": margin_balance,
            }
        except ccxt.AuthenticationError as e:
            raise AuthenticationError(f"認證失敗: {e}") from e
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得合約餘額失敗: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_positions(self) -> list[dict]:
        try:
            positions = self._exchange.fetch_positions()
            result = []
            for pos in positions:
                contracts = float(pos.get("contracts", 0))
                if contracts == 0:
                    continue
                result.append({
                    "symbol": pos["symbol"],
                    "side": pos["side"],  # "long" or "short"
                    "contracts": contracts,
                    "entry_price": float(pos.get("entryPrice", 0)),
                    "mark_price": float(pos.get("markPrice", 0)),
                    "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                    "liquidation_price": float(pos.get("liquidationPrice", 0)),
                    "leverage": int(pos.get("leverage", 1)),
                    "margin_type": pos.get("marginMode", "cross"),
                    "notional": float(pos.get("notional", 0)),
                })
            return result
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得合約持倉失敗: {e}") from e

    # ─── 槓桿與保證金 ───

    @retry(max_retries=2, delay=0.5)
    def set_leverage(self, symbol: str, leverage: int) -> None:
        try:
            self._exchange.set_leverage(leverage, symbol)
            logger.info("已設定 %s 槓桿為 %dx", symbol, leverage)
        except ccxt.NotSupported:
            # Testnet 不支援 setLeverage，跳過
            logger.debug("交易所不支援設定槓桿，跳過: %s", symbol)
        except ccxt.BaseError as e:
            # 有些情況下設定相同槓桿會報錯，可以忽略
            if "No need to change leverage" in str(e):
                logger.debug("槓桿未變更: %s", symbol)
            else:
                raise ExchangeError(f"設定槓桿失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        try:
            self._exchange.set_margin_mode(margin_type, symbol)
            logger.info("已設定 %s 保證金模式為 %s", symbol, margin_type)
        except ccxt.NotSupported:
            # Testnet 不支援 setMarginMode，跳過（預設已是 cross）
            logger.debug("交易所不支援設定保證金模式，跳過: %s", symbol)
        except ccxt.BaseError as e:
            if "No need to change margin type" in str(e):
                logger.debug("保證金模式未變更: %s", symbol)
            else:
                raise ExchangeError(f"設定保證金模式失敗: {e}") from e

    # ─── 下單 ───

    @retry(max_retries=2, delay=0.5)
    def place_market_order(
        self, symbol: str, side: str, amount: float,
        reduce_only: bool = False,
    ) -> dict:
        logger.info(
            "合約市價單: %s %s %.8f reduce_only=%s",
            side.upper(), symbol, amount, reduce_only,
        )
        try:
            params = {}
            if reduce_only:
                params["reduceOnly"] = True
            order = self._exchange.create_order(
                symbol=symbol, type="market", side=side,
                amount=amount, params=params,
            )
            logger.info("合約市價單成交: ID=%s, 狀態=%s", order["id"], order["status"])
            return self._format_order(order)
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"保證金不足: {e}") from e
        except ccxt.BaseError as e:
            raise OrderError(f"合約下單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_limit_order(
        self, symbol: str, side: str, amount: float, price: float,
        reduce_only: bool = False,
    ) -> dict:
        logger.info(
            "合約限價單: %s %s %.8f @ %.2f reduce_only=%s",
            side.upper(), symbol, amount, price, reduce_only,
        )
        try:
            params = {}
            if reduce_only:
                params["reduceOnly"] = True
            order = self._exchange.create_order(
                symbol=symbol, type="limit", side=side,
                amount=amount, price=price, params=params,
            )
            logger.info("合約限價單已提交: ID=%s", order["id"])
            return self._format_order(order)
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(f"保證金不足: {e}") from e
        except ccxt.BaseError as e:
            raise OrderError(f"合約下單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_stop_market(
        self, symbol: str, side: str, amount: float,
        stop_price: float, reduce_only: bool = True,
    ) -> dict:
        """停損市價單（STOP_MARKET）。"""
        logger.info(
            "合約停損單: %s %s %.8f stop=%.2f",
            side.upper(), symbol, amount, stop_price,
        )
        try:
            order = self._exchange.create_order(
                symbol=symbol, type="STOP_MARKET", side=side,
                amount=amount, price=None,
                params={
                    "stopPrice": stop_price,
                    "reduceOnly": reduce_only,
                },
            )
            logger.info("停損單已提交: ID=%s", order["id"])
            return self._format_order(order)
        except ccxt.BaseError as e:
            raise OrderError(f"停損單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def place_take_profit_market(
        self, symbol: str, side: str, amount: float,
        stop_price: float, reduce_only: bool = True,
    ) -> dict:
        """停利市價單（TAKE_PROFIT_MARKET）。"""
        logger.info(
            "合約停利單: %s %s %.8f stop=%.2f",
            side.upper(), symbol, amount, stop_price,
        )
        try:
            order = self._exchange.create_order(
                symbol=symbol, type="TAKE_PROFIT_MARKET", side=side,
                amount=amount, price=None,
                params={
                    "stopPrice": stop_price,
                    "reduceOnly": reduce_only,
                },
            )
            logger.info("停利單已提交: ID=%s", order["id"])
            return self._format_order(order)
        except ccxt.BaseError as e:
            raise OrderError(f"停利單失敗: {e}") from e

    @retry(max_retries=2, delay=0.5)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        try:
            self._exchange.cancel_order(order_id, symbol)
            logger.info("已取消合約訂單: %s", order_id)
            return True
        except ccxt.OrderNotFound:
            logger.warning("合約訂單不存在: %s", order_id)
            return False
        except ccxt.BaseError as e:
            raise ExchangeError(f"取消合約訂單失敗: {e}") from e

    @retry(max_retries=3, delay=1.0)
    def get_order_status(self, order_id: str, symbol: str) -> dict:
        try:
            order = self._exchange.fetch_order(order_id, symbol)
            return self._format_order(order)
        except ccxt.BaseError as e:
            raise ExchangeError(f"查詢合約訂單失敗: {e}") from e

    # ─── 資金費率與保證金 ───

    @retry(max_retries=3, delay=1.0)
    def get_funding_rate(self, symbol: str) -> dict:
        try:
            rate = self._exchange.fetch_funding_rate(symbol)
            return {
                "symbol": symbol,
                "funding_rate": float(rate.get("fundingRate", 0)),
                "next_funding_time": rate.get("fundingDatetime"),
                "mark_price": float(rate.get("markPrice", 0)),
                "index_price": float(rate.get("indexPrice", 0)),
            }
        except ccxt.BaseError as e:
            raise ExchangeError(f"取得資金費率失敗: {e}") from e

    def get_margin_ratio(self) -> float:
        """帳戶保證金比率 = 維持保證金 / 保證金餘額。"""
        if self._is_simulated:
            return 0.0  # Paper 模式無真實保證金
        try:
            balance = self.get_futures_balance()
            margin_balance = balance["total_margin_balance"]
            if margin_balance <= 0:
                return 0.0
            # 用 ccxt 的帳戶資訊取得維持保證金
            raw = self._exchange.fetch_balance()
            info = raw.get("info", {})
            maintenance = float(info.get("totalMaintMargin", 0))
            return maintenance / margin_balance if margin_balance > 0 else 0.0
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
        market = self._exchange.market(symbol)
        return float(market.get("limits", {}).get("amount", {}).get("min", 0))

    @retry(max_retries=3, delay=1.0)
    def fetch_agg_trades(self, symbol: str, limit: int = 1000) -> list[dict]:
        """取得最近的 aggTrade 數據。"""
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
            raise ExchangeError(f"取得合約 aggTrade 失敗: {e}") from e

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
