"""WebSocket 驅動的非同步交易迴圈。"""

import asyncio
import signal

from bot.config.settings import Settings
from bot.data.bar_aggregator import BarAggregator
from bot.data.models import AggTrade, OrderFlowBar
from bot.data.stream import BinanceAggTradeStream
from bot.exchange.binance_client import BinanceClient
from bot.execution.executor import OrderExecutor
from bot.execution.order_manager import OrderManager
from bot.llm.decision_engine import LLMDecisionEngine
from bot.llm.schemas import PortfolioState
from bot.logging_config import get_logger
from bot.logging_config.logger import setup_logging
from bot.risk.manager import RiskManager
from bot.strategy.base import BaseStrategy, OrderFlowStrategy
from bot.strategy.router import StrategyRouter
from bot.strategy.signals import Signal
from bot.strategy.sma_crossover import SMACrossoverStrategy
from bot.strategy.tia_orderflow import TiaBTCOrderFlowStrategy

logger = get_logger("app_async")


class AsyncTradingBot:
    """
    WebSocket 驅動的非同步交易機器人。

    接收 aggTrade 即時數據 → 聚合 K 線 → 訂單流策略 → LLM 決策 → 執行。
    """

    def __init__(self, config_path: str | None = None) -> None:
        self.settings = Settings.load(config_path)
        setup_logging(
            level=self.settings.logging.level,
            file_enabled=self.settings.logging.file_enabled,
            log_dir=self.settings.logging.log_dir,
        )

        logger.info("初始化非同步交易機器人 (模式=%s)", self.settings.spot.mode)

        self.exchange = BinanceClient(self.settings.exchange)
        self.risk_manager = RiskManager(self.settings.spot)
        self.executor = OrderExecutor(self.exchange, self.settings.spot.mode)
        self.order_manager = OrderManager()

        # 訂單流策略
        of_params = {
            "bar_interval_seconds": self.settings.orderflow.bar_interval_seconds,
            "tick_size": self.settings.orderflow.tick_size,
            "cvd_lookback": self.settings.orderflow.cvd_lookback,
            "zscore_lookback": self.settings.orderflow.zscore_lookback,
            "divergence_peak_order": self.settings.orderflow.divergence_peak_order,
            "sfp_swing_lookback": self.settings.orderflow.sfp_swing_lookback,
            "absorption_lookback": self.settings.orderflow.absorption_lookback,
            "signal_threshold": self.settings.orderflow.signal_threshold,
        }
        self.of_strategy = TiaBTCOrderFlowStrategy(of_params)

        # 策略路由器
        self.router = StrategyRouter()

        # LLM 決策引擎
        self.llm_engine = LLMDecisionEngine(self.settings.llm)

        # 每個交易對一個 BarAggregator
        self._aggregators: dict[str, BarAggregator] = {}
        for pair in self.settings.spot.pairs:
            ws_symbol = pair.replace("/", "")
            self._aggregators[ws_symbol] = BarAggregator(
                interval_seconds=self.settings.orderflow.bar_interval_seconds,
                tick_size=self.settings.orderflow.tick_size,
            )

        self._running = False

    async def run(self) -> None:
        """啟動非同步交易迴圈。"""
        self._running = True

        # 設定信號處理
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._shutdown)
            except NotImplementedError:
                # Windows 不支援 add_signal_handler
                pass

        ws_symbols = [pair.replace("/", "") for pair in self.settings.spot.pairs]

        stream = BinanceAggTradeStream(
            symbols=ws_symbols,
            on_trade=self._on_trade,
            testnet=self.settings.exchange.testnet,
        )

        logger.info(
            "啟動 WebSocket 交易: 交易對=%s, 策略=%s",
            ws_symbols, self.of_strategy.name,
        )

        try:
            await stream.start()
        except asyncio.CancelledError:
            pass
        finally:
            await stream.stop()
            logger.info("非同步交易機器人已關閉")

    async def _on_trade(self, trade: AggTrade) -> None:
        """處理每筆 aggTrade。"""
        # 從交易對推斷 symbol（假設格式如 BTCUSDT）
        # 反向對照 settings.trading.pairs
        for pair in self.settings.spot.pairs:
            ws_symbol = pair.replace("/", "")
            if ws_symbol in self._aggregators:
                aggregator = self._aggregators[ws_symbol]
                bar = aggregator.add_trade(trade)

                if bar is not None:
                    await self._on_bar(pair, bar)
                break

    async def _on_bar(self, symbol: str, bar: OrderFlowBar) -> None:
        """處理完成的 K 線。"""
        try:
            # 1. 訂單流策略產生結論
            verdict = self.of_strategy.on_bar(symbol, bar)

            # 2. 收集結論
            self.router.clear()
            self.router.collect(verdict)

            # 3. 若 LLM 啟用，透過 LLM 決策
            if self.llm_engine.enabled and verdict.signal != Signal.HOLD:
                portfolio = self._build_portfolio_state(symbol, bar.close)
                decision = await self.llm_engine.decide(
                    verdicts=self.router.get_verdicts(),
                    portfolio=portfolio,
                    symbol=symbol,
                    current_price=bar.close,
                )

                # 根據 LLM 決策執行
                if decision.action == "BUY":
                    self._execute_signal(Signal.BUY, symbol, bar.close)
                elif decision.action == "SELL":
                    self._execute_signal(Signal.SELL, symbol, bar.close)
            else:
                # Fallback: 直接使用策略訊號
                if verdict.signal != Signal.HOLD:
                    self._execute_signal(verdict.signal, symbol, bar.close)

            # 4. 檢查停損停利
            sl_tp = self.risk_manager.check_stop_loss_take_profit(symbol, bar.close)
            if sl_tp == Signal.SELL:
                self._execute_signal(Signal.SELL, symbol, bar.close)

        except Exception:
            logger.exception("處理 K 線失敗: %s", symbol)

    def _execute_signal(self, sig: Signal, symbol: str, price: float) -> None:
        """執行交易訊號。"""
        balance = self.exchange.get_balance()
        usdt_balance = balance.get("USDT", 0.0)

        risk_output = self.risk_manager.evaluate(sig, symbol, price, usdt_balance)
        if not risk_output.approved:
            logger.info("風控拒絕: %s — %s", symbol, risk_output.reason)
            return

        order = self.executor.execute(sig, symbol, risk_output)
        if order:
            fill_price = order.get("price", price)
            if sig == Signal.BUY:
                self.risk_manager.add_position(symbol, risk_output.quantity, fill_price)
            elif sig == Signal.SELL:
                self.risk_manager.remove_position(symbol, fill_price)
            self.order_manager.add_order(order)

    def _build_portfolio_state(self, symbol: str, current_price: float) -> PortfolioState:
        """建構當前投資組合狀態（供 LLM 決策參考）。"""
        from bot.llm.schemas import PositionInfo

        balance = self.exchange.get_balance()
        usdt_balance = balance.get("USDT", 0.0)

        positions = []
        for sym, pos_data in self.risk_manager._open_positions.items():
            positions.append(PositionInfo(
                symbol=sym,
                quantity=pos_data["quantity"],
                entry_price=pos_data["entry_price"],
                current_price=current_price if sym == symbol else 0.0,
                unrealized_pnl=(current_price - pos_data["entry_price"]) * pos_data["quantity"] if sym == symbol else 0.0,
            ))

        return PortfolioState(
            available_balance=usdt_balance,
            positions=positions,
            max_positions=self.settings.spot.max_open_positions,
            current_position_count=self.risk_manager.open_position_count,
        )

    def _shutdown(self) -> None:
        logger.info("收到中止訊號，正在關閉...")
        self._running = False
