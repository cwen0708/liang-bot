"""主應用程式 — 交易機器人核心迴圈。

流程：持續運行 → 定時抓取 K 線 + aggTrade → 所有策略產生結論 → LLM 分析 → 執行。
"""

import signal
import time
from datetime import datetime, timezone

from bot.config.settings import LoanGuardConfig, Settings
from bot.db.supabase_client import SupabaseWriter
from bot.data.bar_aggregator import BarAggregator
from bot.data.fetcher import DataFetcher
from bot.data.models import AggTrade
from bot.exchange.binance_client import BinanceClient
from bot.execution.executor import OrderExecutor
from bot.execution.order_manager import OrderManager
from bot.llm.decision_engine import LLMDecisionEngine
from bot.llm.schemas import PortfolioState, PositionInfo
from bot.logging_config import get_logger
from bot.logging_config.logger import setup_logging
from bot.risk.manager import RiskManager
from bot.strategy.base import BaseStrategy, OrderFlowStrategy
from bot.strategy.router import StrategyRouter
from bot.strategy.signals import Signal, StrategyVerdict
from bot.strategy.sma_crossover import SMACrossoverStrategy
from bot.strategy.tia_orderflow import TiaBTCOrderFlowStrategy

logger = get_logger("app")

# Log indentation prefixes
_L1 = "  "        # Level 1: symbol
_L2 = "    "      # Level 2: strategy / action
_L3 = "      "    # Level 3: sub-detail

# OHLCV 策略註冊表
OHLCV_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "sma_crossover": SMACrossoverStrategy,
}


class TradingBot:
    """
    幣安現貨交易機器人。

    主迴圈（每 check_interval_seconds 執行一次）：
    1. 抓取 K 線 → OHLCV 策略產生結論
    2. 抓取最近 aggTrade → 聚合為 OrderFlowBar → 訂單流策略產生結論
    3. StrategyRouter 收集所有結論
    4. LLM 決策引擎彙整分析 → 最終決策（或 fallback 加權投票）
    5. 風控評估 → 執行交易
    """

    def __init__(self, config_path: str | None = None) -> None:
        self.settings = Settings.load(config_path)
        setup_logging(
            level=self.settings.logging.level,
            file_enabled=self.settings.logging.file_enabled,
            log_dir=self.settings.logging.log_dir,
        )

        logger.info("初始化交易機器人 (模式=%s)", self.settings.trading.mode)

        self.exchange = BinanceClient(self.settings.exchange)
        self.data_fetcher = DataFetcher(self.exchange)

        # 初始化 OHLCV 策略
        self.ohlcv_strategies: list[BaseStrategy] = []
        # 初始化訂單流策略
        self.of_strategies: list[OrderFlowStrategy] = []
        self._create_all_strategies()

        self.risk_manager = RiskManager(self.settings.risk)
        self.executor = OrderExecutor(self.exchange, self.settings.trading.mode)
        self.order_manager = OrderManager()

        # 策略路由器
        self.router = StrategyRouter(
            fallback_weights=self.settings.llm.fallback_weights,
        )

        # LLM 決策引擎
        self.llm_engine = LLMDecisionEngine(self.settings.llm)

        # LLM client（借款監控用）
        from bot.llm.client import ClaudeCLIClient
        self._llm_client = ClaudeCLIClient(self.settings.llm)

        # Supabase 寫入層
        self._db = SupabaseWriter()

        # 每個交易對一個 BarAggregator（跨輪保留，持續聚合）
        self._aggregators: dict[str, BarAggregator] = {}

        # 記錄每個交易對最後處理的 trade ID（避免重複餵入）
        self._last_trade_id: dict[str, int] = {}

        # 記錄每個交易對最後一根 K 線的時間戳（避免小時線未更新時重複計算 OHLCV 策略）
        self._last_candle_time: dict[str, object] = {}

        self._running = False
        self._start_time: float = 0.0
        self._config_version: int = 0

    def _create_all_strategies(self) -> None:
        """根據 config 建立所有策略（OHLCV + 訂單流）。"""
        for strat_cfg in self.settings.strategies_config.strategies:
            name = strat_cfg.get("name", "")
            params = strat_cfg.get("params", {})

            if name in OHLCV_STRATEGY_REGISTRY:
                strategy = OHLCV_STRATEGY_REGISTRY[name](params)
                self.ohlcv_strategies.append(strategy)
                logger.info("載入 OHLCV 策略: %s", name)
            elif name == "tia_orderflow":
                # 合併 orderflow config 參數
                of_params = {
                    "bar_interval_seconds": self.settings.orderflow.bar_interval_seconds,
                    "tick_size": self.settings.orderflow.tick_size,
                    "cvd_lookback": self.settings.orderflow.cvd_lookback,
                    "zscore_lookback": self.settings.orderflow.zscore_lookback,
                    "divergence_peak_order": self.settings.orderflow.divergence_peak_order,
                    "sfp_swing_lookback": self.settings.orderflow.sfp_swing_lookback,
                    "absorption_lookback": self.settings.orderflow.absorption_lookback,
                    "signal_threshold": self.settings.orderflow.signal_threshold,
                    **params,
                }
                of_strategy = TiaBTCOrderFlowStrategy(of_params)
                self.of_strategies.append(of_strategy)
                logger.info("載入訂單流策略: %s", name)
            else:
                logger.warning("未知策略: %s，跳過", name)

        # 至少保留一個 OHLCV 策略
        if not self.ohlcv_strategies:
            self.ohlcv_strategies.append(SMACrossoverStrategy(self.settings.strategy.params))
            logger.info("使用預設 sma_crossover 策略")

    def run(self) -> None:
        """啟動交易迴圈（持續運行）。"""
        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)

        all_names = (
            [s.name for s in self.ohlcv_strategies]
            + [s.name for s in self.of_strategies]
        )
        lg = self.settings.loan_guard
        logger.info(
            "啟動交易: 交易對=%s, 時間框架=%s, 策略=%s, LLM=%s, 借款監控=%s",
            self.settings.trading.pairs,
            self.settings.trading.timeframe,
            all_names,
            "啟用" if self.llm_engine.enabled else "停用（加權投票）",
            f"啟用 (低買>{lg.danger_ltv:.0%}, 高賣<{lg.low_ltv:.0%}, 目標={lg.target_ltv:.0%}{', 模擬' if lg.dry_run else ''})"
            if lg.enabled else "停用",
        )

        self._start_time = time.monotonic()
        cycle = 0
        while self._running:
            cycle += 1
            logger.info("═══ 第 %d 輪分析開始 ═══", cycle)

            # 從 Supabase 載入最新配置（若版本已變更）
            new_cfg = self._db.load_config()
            if new_cfg is not None:
                try:
                    self.settings = Settings.from_dict(new_cfg, self.settings)
                    self._config_version = self._db._last_config_version
                    logger.info("已套用 Supabase 新配置 (version=%d)", self._config_version)
                except Exception as e:
                    logger.error("套用 Supabase 配置失敗: %s（保留舊配置）", e)

            for symbol in self.settings.trading.pairs:
                try:
                    self._process_symbol(symbol)
                except Exception:
                    logger.exception("%s處理時發生錯誤", _L1)

            # 借款 LTV 監控（AI 審核可能耗時，先寫一次心跳避免前端誤判離線）
            if self.settings.loan_guard.enabled:
                uptime_mid = int(time.monotonic() - self._start_time)
                self._db.update_bot_status(
                    cycle_num=cycle,
                    status="running",
                    config_ver=self._config_version,
                    pairs=list(self.settings.trading.pairs),
                    uptime_sec=uptime_mid,
                )
                try:
                    self._check_loan_health()
                except Exception:
                    logger.exception("%s借款監控發生錯誤", _L1)

            # 更新 Supabase 心跳 + flush 日誌
            uptime = int(time.monotonic() - self._start_time)
            self._db.update_bot_status(
                cycle_num=cycle,
                status="running",
                config_ver=self._config_version,
                pairs=list(self.settings.trading.pairs),
                uptime_sec=uptime,
            )
            self._db.flush_logs()

            if self._running:
                logger.info(
                    "═══ 第 %d 輪完成，%d 秒後進行下一輪 ═══",
                    cycle, self.settings.trading.check_interval_seconds,
                )
                time.sleep(self.settings.trading.check_interval_seconds)

    def _process_symbol(self, symbol: str) -> None:
        """
        處理單一交易對：收集所有策略結論 → LLM/加權投票 → 執行。
        """
        # ── 1. 抓取 K 線 ──
        max_required = max((s.required_candles for s in self.ohlcv_strategies), default=50)
        df = self.data_fetcher.fetch_ohlcv(
            symbol,
            timeframe=self.settings.trading.timeframe,
            limit=max(max_required + 10, 100),
        )

        if len(df) < max_required:
            logger.warning("%sK 線資料不足: %d/%d", _L1, len(df), max_required)
            return

        current_price = float(df["close"].iloc[-1])
        logger.info("%s%s 現價: %.2f USDT", _L1, symbol, current_price)
        self._db.insert_market_snapshot(symbol, current_price)

        # ── 2. 停損停利 ──
        # Live 模式：檢查交易所 OCO 訂單是否已成交
        if self.executor.is_live and self.risk_manager.has_exchange_sl_tp(symbol):
            if self._sync_oco_order(symbol):
                return  # OCO 已成交，持倉已移除

        # Paper 模式（或 live 無 OCO 掛單時）：輪詢價格判斷
        if not self.risk_manager.has_exchange_sl_tp(symbol):
            sl_tp_signal = self.risk_manager.check_stop_loss_take_profit(symbol, current_price)
            if sl_tp_signal == Signal.SELL:
                logger.info("%s觸發停損/停利 → 執行賣出", _L2)
                self._execute_sell(symbol, current_price)
                return

        # ── 3. 收集所有策略結論 ──
        self.router.clear()

        # 3a. OHLCV 策略（只在新 K 線出現時重新計算）
        latest_candle_time = df["timestamp"].iloc[-1]
        ohlcv_changed = (self._last_candle_time.get(symbol) != latest_candle_time)

        if ohlcv_changed:
            self._last_candle_time[symbol] = latest_candle_time
            for strategy in self.ohlcv_strategies:
                if len(df) < strategy.required_candles:
                    continue
                verdict = strategy.generate_verdict(df)
                self.router.collect(verdict)
                self._db.insert_verdict(
                    symbol, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning,
                )
                logger.info(
                    "%s[%s] %s (信心 %.2f) — %s",
                    _L2, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning[:80],
                )
        else:
            logger.info("%sOHLCV 無新 K 線，跳過 (%s)", _L2, self.settings.trading.timeframe)

        # 3b. 訂單流策略（每輪都執行，aggTrade 持續更新）
        if self.of_strategies:
            self._collect_orderflow_verdicts(symbol)

        verdicts = self.router.get_verdicts()
        if not verdicts:
            logger.info("%s無策略結論，跳過", _L2)
            return

        # ── 4. LLM 決策 或 加權投票 ──
        final_signal, final_confidence = self._make_decision(
            verdicts, symbol, current_price
        )

        if final_signal == Signal.HOLD:
            logger.info("%s→ HOLD（不動作）", _L2)
            return

        logger.info(
            "%s→ %s (信心 %.2f)",
            _L2, final_signal.value, final_confidence,
        )

        # ── 5. 風控 + 執行 ──
        if final_signal == Signal.BUY:
            try:
                balance = self.exchange.get_balance()
                usdt_balance = balance.get("USDT", 0.0)
            except Exception as e:
                logger.warning("%s取得餘額失敗，跳過買入: %s", _L2, e)
                return

            risk_output = self.risk_manager.evaluate(
                final_signal, symbol, current_price, usdt_balance
            )
            if not risk_output.approved:
                logger.info("%s風控拒絕: %s", _L2, risk_output.reason)
                return
            self._execute_buy(symbol, current_price, risk_output)

        elif final_signal == Signal.SELL:
            self._execute_sell(symbol, current_price)

    def _collect_orderflow_verdicts(self, symbol: str) -> None:
        """透過 REST API 抓取 aggTrade，聚合後送入訂單流策略（跨輪累積）。"""
        try:
            # 首次呼叫時從本地快取載入歷史 bars（避免重啟後等 30 分鐘）
            if symbol not in self._last_trade_id:
                for of_strategy in self.of_strategies:
                    of_strategy.load_cache(symbol)

            raw_trades = self.exchange.fetch_agg_trades(symbol, limit=1000)
            if not raw_trades:
                logger.debug("%s無 aggTrade 數據", _L2)
                return

            # 過濾已處理的 trades（避免重複餵入）
            last_id = int(self._last_trade_id.get(symbol, 0))
            new_trades = [t for t in raw_trades if int(t.get("trade_id") or 0) > last_id]

            if not new_trades:
                # 無新交易，仍用最近一根 bar 的結論
                for of_strategy in self.of_strategies:
                    verdict = of_strategy.latest_verdict(symbol)
                    if verdict is not None:
                        self.router.collect(verdict)
                return

            # 更新 last_trade_id
            self._last_trade_id[symbol] = int(new_trades[-1]["trade_id"] or 0)

            # 取得或建立 aggregator（跨輪保留）
            if symbol not in self._aggregators:
                self._aggregators[symbol] = BarAggregator(
                    interval_seconds=self.settings.orderflow.bar_interval_seconds,
                    tick_size=self.settings.orderflow.tick_size,
                )
            aggregator = self._aggregators[symbol]

            # 將新 trades 聚合為 bars
            new_bars = []
            for t in new_trades:
                trade = AggTrade(
                    trade_id=t["trade_id"] or 0,
                    price=t["price"],
                    quantity=t["quantity"],
                    timestamp=datetime.fromtimestamp(
                        t["timestamp"] / 1000, tz=timezone.utc
                    ),
                    is_buyer_maker=t["is_buyer_maker"],
                )
                bar = aggregator.add_trade(trade)
                if bar is not None:
                    new_bars.append(bar)

            # 不 flush 最後一根（留給下一輪繼續聚合）

            if new_bars:
                logger.info("%saggTrade → %d 根新訂單流 K 線", _L2, len(new_bars))

            # 送入各訂單流策略（不 reset，跨輪累積）
            for of_strategy in self.of_strategies:
                verdict = None
                for bar in new_bars:
                    verdict = of_strategy.on_bar(symbol, bar)

                # 無新 bar 時用最近結論
                if verdict is None:
                    verdict = of_strategy.latest_verdict(symbol)

                if verdict is not None:
                    self.router.collect(verdict)
                    logger.info(
                        "%s[%s] %s (信心 %.2f) [%d/%d bars] — %s",
                        _L2, of_strategy.name, verdict.signal.value,
                        verdict.confidence,
                        len(of_strategy._bars), of_strategy.required_bars,
                        verdict.reasoning[:60],
                    )

        except Exception:
            logger.exception("%s訂單流分析失敗", _L2)

    def _make_decision(
        self,
        verdicts: list[StrategyVerdict],
        symbol: str,
        current_price: float,
    ) -> tuple[Signal, float]:
        """LLM 啟用時呼叫 LLM 分析，否則加權投票。"""
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if not non_hold:
            return Signal.HOLD, 0.0

        # 檢查是否有策略達到最低信心門檻
        min_conf = self.settings.llm.min_confidence
        qualified = [v for v in non_hold if v.confidence >= min_conf]
        if not qualified:
            logger.info(
                "%s所有策略信心低於 %.2f → 跳過 LLM，使用加權投票", _L2, min_conf
            )

        if self.llm_engine.enabled and qualified:
            try:
                portfolio = self._build_portfolio_state(symbol, current_price)
                decision = self.llm_engine.decide_sync(
                    verdicts=verdicts,
                    portfolio=portfolio,
                    symbol=symbol,
                    current_price=current_price,
                )
                self._db.insert_llm_decision(
                    symbol, decision.action, decision.confidence,
                    decision.reasoning, self.settings.llm.model,
                )
                logger.info(
                    "%s[LLM] %s (信心 %.2f) — %s",
                    _L2, decision.action, decision.confidence,
                    decision.reasoning[:100],
                )
                action_map = {"BUY": Signal.BUY, "SELL": Signal.SELL}
                return action_map.get(decision.action, Signal.HOLD), decision.confidence
            except Exception as e:
                logger.warning("%sLLM 決策失敗 → 改用加權投票: %s", _L2, e)

        # Fallback: 加權投票
        vote_result = self.router.weighted_vote()
        logger.info(
            "%s[加權投票] %s (信心 %.2f) — %s",
            _L2, vote_result.signal.value, vote_result.confidence,
            vote_result.reasoning,
        )
        return vote_result.signal, vote_result.confidence

    def _build_portfolio_state(
        self, symbol: str, current_price: float
    ) -> PortfolioState:
        """建構投資組合狀態（供 LLM 參考）。"""
        try:
            balance = self.exchange.get_balance()
            usdt_balance = balance.get("USDT", 0.0)
        except Exception:
            usdt_balance = 0.0

        positions = []
        for sym, pos_data in self.risk_manager._open_positions.items():
            entry = pos_data["entry_price"]
            qty = pos_data["quantity"]
            price = current_price if sym == symbol else entry
            pnl = (price - entry) * qty
            pnl_pct = (price - entry) / entry if entry > 0 else 0.0

            positions.append(PositionInfo(
                symbol=sym,
                quantity=qty,
                entry_price=entry,
                current_price=price,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
            ))

        return PortfolioState(
            available_balance=usdt_balance,
            positions=positions,
            max_positions=self.settings.risk.max_open_positions,
            current_position_count=self.risk_manager.open_position_count,
            daily_realized_pnl=self.risk_manager._daily_pnl,
            daily_risk_remaining=usdt_balance * self.settings.risk.max_daily_loss_pct + self.risk_manager._daily_pnl,
        )

    def _execute_buy(self, symbol: str, price: float, risk_output) -> None:
        order = self.executor.execute(Signal.BUY, symbol, risk_output)
        if order:
            fill_price = order.get("price", price)

            # 掛 SL/TP 單
            tp_order_id, sl_order_id = None, None
            oco_info = self.executor.place_sl_tp(
                symbol,
                risk_output.quantity,
                risk_output.take_profit_price,
                risk_output.stop_loss_price,
            )
            if oco_info:
                tp_order_id = oco_info.get("tp_order_id")
                sl_order_id = oco_info.get("sl_order_id")

            self.risk_manager.add_position(
                symbol, risk_output.quantity, fill_price,
                tp_order_id=tp_order_id,
                sl_order_id=sl_order_id,
            )
            self.order_manager.add_order(order)
            self._db.insert_order(order)
            self._db.upsert_position(symbol, {
                "quantity": risk_output.quantity,
                "entry_price": fill_price,
                "current_price": fill_price,
                "unrealized_pnl": 0,
                "stop_loss": risk_output.stop_loss_price,
                "take_profit": risk_output.take_profit_price,
            })
            logger.info(
                "%s✓ BUY %s @ %.2f, qty=%.8f (SL=%.2f, TP=%.2f)",
                _L3, symbol, fill_price, risk_output.quantity,
                risk_output.stop_loss_price, risk_output.take_profit_price,
            )

    def _execute_sell(self, symbol: str, price: float) -> None:
        # 取消交易所上的 SL/TP 掛單（若有）
        tp_id, sl_id = self.risk_manager.get_sl_tp_order_ids(symbol)
        if tp_id or sl_id:
            self.executor.cancel_sl_tp(symbol, tp_id, sl_id)
            logger.info("%s已取消 SL/TP 掛單", _L3)

        risk_output = self.risk_manager.evaluate(Signal.SELL, symbol, price, 0)
        if not risk_output.approved:
            return

        order = self.executor.execute(Signal.SELL, symbol, risk_output)
        if order:
            fill_price = order.get("price", price)
            pnl = self.risk_manager.remove_position(symbol, fill_price)
            self.order_manager.add_order(order)
            self._db.insert_order(order)
            self._db.delete_position(symbol)
            logger.info("%s✓ SELL %s @ %.2f, PnL=%.2f USDT", _L3, symbol, fill_price, pnl)

    def _check_loan_health(self) -> None:
        """檢查借款 LTV，超過危險閾值時提交 AI 審核後買入質押物。"""
        lg = self.settings.loan_guard
        orders = self.exchange.fetch_loan_ongoing_orders()
        if not orders:
            return

        for o in orders:
            loan_coin = o.get("loanCoin", "?")
            collateral_coin = o.get("collateralCoin", "?")
            ltv = float(o.get("currentLTV", 0))
            debt = float(o.get("totalDebt", 0))
            collateral_amt = float(o.get("collateralAmount", 0))
            label = f"{collateral_coin}→{loan_coin}"

            # 寫入 loan health 快照（action 先寫 none，AI 核准後才更新）
            lh_row_id = self._db.insert_loan_health({
                "loan_coin": loan_coin,
                "collateral_coin": collateral_coin,
                "ltv": ltv,
                "total_debt": debt,
                "collateral_amount": collateral_amt,
                "action_taken": "none",
            })

            if ltv >= lg.danger_ltv:
                logger.warning(
                    "%s[借款] %s LTV=%.1f%% 超過 %.0f%%！啟動保護流程",
                    _L1, label, ltv * 100, lg.danger_ltv * 100,
                )
                self._loan_protect(lg, o, lh_row_id)

            elif ltv >= lg.target_ltv:
                logger.warning(
                    "%s[借款] %s LTV=%.1f%% 接近危險閾值 %.0f%%",
                    _L1, label, ltv * 100, lg.danger_ltv * 100,
                )
            elif ltv <= lg.low_ltv:
                logger.info(
                    "%s[借款] %s LTV=%.1f%% 低於 %.0f%%，啟動獲利了結",
                    _L1, label, ltv * 100, lg.low_ltv * 100,
                )
                self._loan_take_profit(lg, o, lh_row_id)
            else:
                logger.info("%s[借款] %s LTV=%.1f%% 安全", _L1, label, ltv * 100)

    def _loan_protect(self, lg: LoanGuardConfig, order: dict, lh_row_id: int | None = None) -> None:
        """
        借款保護：計算所需質押物 → AI 審核 → 買入現貨 → 增加質押。

        流程:
        1. 計算需增加多少質押物才能降回 warn_ltv
        2. 查詢質押幣種現價、可用餘額
        3. 組合摘要送 AI 審核
        4. AI 同意 → 買入 → 增加質押
        """
        loan_coin = order.get("loanCoin", "?")
        collateral_coin = order.get("collateralCoin", "?")
        ltv = float(order.get("currentLTV", 0))
        debt = float(order.get("totalDebt", 0))
        collateral_amt = float(order.get("collateralAmount", 0))
        label = f"{collateral_coin}→{loan_coin}"

        # ── 1. 計算需要多少質押物 ──
        # LTV = debt / collateral_value
        # collateral_value = debt / ltv
        # target_collateral_value = debt / target_ltv
        # additional_value = target_collateral_value - current_collateral_value
        collateral_value = debt / ltv if ltv > 0 else 0
        target_collateral_value = debt / lg.target_ltv
        additional_value_usdt = target_collateral_value - collateral_value

        if additional_value_usdt <= 0:
            return

        # 查質押幣的現價
        pair = f"{collateral_coin}/USDT"
        try:
            ticker = self.exchange.get_ticker(pair)
            coin_price = ticker["last"]
        except Exception as e:
            logger.error("%s[借款] 無法取得 %s 報價: %s", _L2, pair, e)
            return

        additional_qty = additional_value_usdt / coin_price
        buy_cost_usdt = additional_qty * coin_price

        # 查可用 USDT 餘額
        try:
            balance = self.exchange.get_balance()
            usdt_available = balance.get("USDT", 0.0)
        except Exception:
            usdt_available = 0.0

        # ── 2. 組合 AI 審核摘要 ──
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

        # ── 3. AI 審核 ──
        try:
            ai_response = self._llm_client.call_sync(summary)
            logger.info("%s[借款] AI 回覆: %s", _L2, ai_response[:200])
        except Exception as e:
            logger.error("%s[借款] AI 審核失敗: %s，不執行操作", _L2, e)
            return

        # 解析 AI 回覆
        import json
        try:
            # 嘗試從回覆中提取 JSON
            text = ai_response.strip()
            # 處理 markdown code block
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            decision = json.loads(text)
        except (json.JSONDecodeError, IndexError):
            logger.warning("%s[借款] AI 回覆非 JSON，視為拒絕: %s", _L2, ai_response[:100])
            return

        approved = decision.get("approved", False)
        reason = decision.get("reason", "無理由")

        if not approved:
            logger.info("%s[借款] AI 拒絕操作: %s", _L2, reason)
            return

        logger.info("%s[借款] AI 同意: %s", _L2, reason)

        # ── 4. 執行：模擬 or 實際 ──
        if lg.dry_run:
            logger.info(
                "%s[借款] [模擬] 將買入 %.8f %s 並追加質押（未實際執行）",
                _L2, additional_qty, collateral_coin,
            )
            return

        # 4a. 檢查現貨是否已有足夠的質押幣（上次殘留）
        try:
            pre_balance = self.exchange.get_balance()
            existing = pre_balance.get(collateral_coin, 0.0)
        except Exception:
            existing = 0.0

        need_to_buy = additional_qty - existing
        if need_to_buy > 0:
            # 需要買入
            try:
                buy_order = self.exchange.place_market_order(pair, "buy", need_to_buy)
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
                # 買入失敗但有現貨殘留，繼續嘗試質押
        else:
            logger.info(
                "%s[借款] 現貨已有 %.8f %s，無需購買",
                _L2, existing, collateral_coin,
            )

        # 4b. 查詢實際可用餘額
        try:
            post_balance = self.exchange.get_balance()
            actual_available = post_balance.get(collateral_coin, 0.0)
        except Exception:
            actual_available = existing + (filled_qty * 0.999 if need_to_buy > 0 else 0)

        pledge_qty = min(additional_qty, actual_available)

        # 4c. 增加質押物
        try:
            self.exchange.loan_adjust_ltv(
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

    def _loan_take_profit(self, lg: LoanGuardConfig, order: dict, lh_row_id: int | None = None) -> None:
        """
        低 LTV 獲利了結：質押物升值過多 → AI 審核 → 減少質押 → 賣出現貨。

        流程（與 _loan_protect 鏡像操作）:
        1. 計算可減少多少質押物仍維持 target_ltv
        2. 查詢質押幣種現價
        3. 組合摘要送 AI 審核
        4. AI 同意 → 減少質押 → 賣出現貨
        """
        loan_coin = order.get("loanCoin", "?")
        collateral_coin = order.get("collateralCoin", "?")
        ltv = float(order.get("currentLTV", 0))
        debt = float(order.get("totalDebt", 0))
        collateral_amt = float(order.get("collateralAmount", 0))
        label = f"{collateral_coin}→{loan_coin}"

        # ── 1. 計算可減少的質押物 ──
        # current_collateral_value = debt / ltv
        # target_collateral_value = debt / target_ltv
        # removable_value = current - target
        collateral_value = debt / ltv if ltv > 0 else 0
        target_collateral_value = debt / lg.target_ltv
        removable_value_usdt = collateral_value - target_collateral_value

        if removable_value_usdt <= 0:
            return

        # 查質押幣的現價
        pair = f"{collateral_coin}/USDT"
        try:
            ticker = self.exchange.get_ticker(pair)
            coin_price = ticker["last"]
        except Exception as e:
            logger.error("%s[借款] 無法取得 %s 報價: %s", _L2, pair, e)
            return

        removable_qty = removable_value_usdt / coin_price
        sell_revenue_usdt = removable_qty * coin_price

        # 預估操作後的 LTV
        new_collateral_value = collateral_value - removable_value_usdt
        expected_ltv = debt / new_collateral_value if new_collateral_value > 0 else 1.0

        # ── 2. 組合 AI 審核摘要 ──
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

        # ── 3. AI 審核 ──
        try:
            ai_response = self._llm_client.call_sync(summary)
            logger.info("%s[借款] AI 回覆: %s", _L2, ai_response[:200])
        except Exception as e:
            logger.error("%s[借款] AI 審核失敗: %s，不執行操作", _L2, e)
            return

        import json
        try:
            text = ai_response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            decision = json.loads(text)
        except (json.JSONDecodeError, IndexError):
            logger.warning("%s[借款] AI 回覆非 JSON，視為拒絕: %s", _L2, ai_response[:100])
            return

        approved = decision.get("approved", False)
        reason = decision.get("reason", "無理由")

        if not approved:
            logger.info("%s[借款] AI 拒絕獲利了結: %s", _L2, reason)
            return

        logger.info("%s[借款] AI 同意獲利了結: %s", _L2, reason)

        # ── 4. 執行：模擬 or 實際 ──
        if lg.dry_run:
            logger.info(
                "%s[借款] [模擬] 將減少質押 %.8f %s 並賣出（未實際執行）",
                _L2, removable_qty, collateral_coin,
            )
            return

        # 4a. 減少質押物
        try:
            self.exchange.loan_adjust_ltv(
                loan_coin, collateral_coin, removable_qty, direction="REDUCED"
            )
            logger.info(
                "%s[借款] 已減少質押 %.8f %s",
                _L2, removable_qty, collateral_coin,
            )
        except Exception as e:
            logger.error("%s[借款] 減少質押失敗: %s", _L2, e)
            return

        # 4b. 賣出取回的現貨
        try:
            sell_order = self.exchange.place_market_order(pair, "sell", removable_qty)
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

    def _sync_oco_order(self, symbol: str) -> bool:
        """
        檢查交易所 OCO 訂單是否已成交（live 模式）。

        Returns:
            True 表示 OCO 已成交，持倉已移除。
        """
        tp_id, sl_id = self.risk_manager.get_sl_tp_order_ids(symbol)

        for order_id, label in [(tp_id, "停利"), (sl_id, "停損")]:
            if not order_id:
                continue
            try:
                status = self.exchange.get_order_status(order_id, symbol)
                if status["status"] == "closed":
                    fill_price = status.get("price", 0)
                    pnl = self.risk_manager.remove_position(symbol, fill_price)
                    self.order_manager.add_order(status)
                    logger.info(
                        "%s交易所 %s 成交: %s @ %.2f, PnL=%.2f USDT",
                        _L2, label, symbol, fill_price, pnl,
                    )
                    return True
            except Exception as e:
                logger.debug("查詢 OCO 訂單 %s 失敗: %s", order_id, e)

        return False

    def _shutdown(self, signum, frame) -> None:
        logger.info("收到中止訊號，正在關閉...")
        self._running = False
