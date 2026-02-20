"""合約交易機器人 — USDT-M 永續合約主迴圈。

流程：持續運行 → 定時抓取 K 線 + aggTrade → 策略結論 → 訊號轉換 → LLM 分析 → 風控 → 執行。
策略仍輸出 BUY/SELL/HOLD，Bot 根據持倉狀態自動轉換為 開多/平多/開空/平空。
"""

import signal as sig_module
import time
import uuid
from datetime import datetime, timezone, timedelta

from bot.config.constants import DataFeedType, TF_MINUTES, TradingMode
from bot.config.settings import Settings
from bot.data.bar_aggregator import BarAggregator
from bot.data.fetcher import DataFetcher
from bot.db.supabase_client import SupabaseWriter
from bot.exchange.futures_native_client import FuturesBinanceClient
from bot.execution.futures_executor import FuturesOrderExecutor
from bot.llm.decision_engine import LLMDecisionEngine
from bot.llm.schemas import PortfolioState, PositionInfo
from bot.logging_config import get_logger
from bot.logging_config.logger import attach_supabase_handler, setup_logging
from bot.risk.futures_manager import FuturesRiskManager
from bot.strategy.base import BaseStrategy, Strategy
from bot.strategy.router import StrategyRouter
from bot.strategy.signals import Signal, StrategyVerdict
from bot.strategy.bollinger_breakout import BollingerBreakoutStrategy
from bot.strategy.ema_ribbon import EMARibbonStrategy
from bot.strategy.macd_momentum import MACDMomentumStrategy
from bot.strategy.rsi_oversold import RSIOversoldStrategy
from bot.strategy.sma_crossover import SMACrossoverStrategy
from bot.reconciliation import PositionReconciler
from bot.strategy.tia_orderflow import TiaBTCOrderFlowStrategy
from bot.strategy.vwap_reversion import VWAPReversionStrategy

logger = get_logger("app_futures")

_L1 = "  "
_L2 = "    "
_L3 = "      "

OHLCV_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "sma_crossover": SMACrossoverStrategy,
    "rsi_oversold": RSIOversoldStrategy,
    "bollinger_breakout": BollingerBreakoutStrategy,
    "macd_momentum": MACDMomentumStrategy,
    "vwap_reversion": VWAPReversionStrategy,
    "ema_ribbon": EMARibbonStrategy,
}


class FuturesTradingBot:
    """
    Binance USDT-M 永續合約交易機器人。

    與現貨 Bot 獨立運行，共用策略引擎和 LLM 引擎，
    但使用合約專用的交易所客戶端、風控管理器、訂單執行器。
    """

    def __init__(self, config_path: str | None = None) -> None:
        self.settings = Settings.load(config_path)
        setup_logging(
            level=self.settings.logging.level,
            file_enabled=self.settings.logging.file_enabled,
            log_dir=self.settings.logging.log_dir,
        )

        if not self.settings.futures.enabled:
            raise RuntimeError("合約交易未啟用，請在配置中設定 futures.enabled = true")

        self._db = SupabaseWriter()
        self._config_version: int = 0

        # 載入 Supabase 線上配置
        remote_cfg = self._db.load_config()
        if remote_cfg is not None:
            try:
                self.settings = Settings.from_dict(remote_cfg, self.settings)
                self._config_version = self._db._last_config_version
                logger.info("已載入 Supabase 線上配置 (version=%d)", self._config_version)
            except Exception as e:
                logger.warning("載入 Supabase 配置失敗，使用本地配置: %s", e)

        fc = self.settings.futures
        logger.info(
            "初始化合約交易機器人 (模式=%s, 槓桿=%dx, 保證金=%s)",
            fc.mode, fc.leverage, fc.margin_type,
        )

        # 合約交易所客戶端（獨立 ccxt 實例）
        self.exchange = FuturesBinanceClient(self.settings.exchange, fc)

        # DataFetcher 可複用（get_ohlcv 介面相同）
        self.data_fetcher = DataFetcher(self.exchange)

        # 策略
        self.strategies: list[Strategy] = []
        self._create_all_strategies()
        self._strategy_fingerprint = self._get_strategy_fingerprint()
        self._cache_loaded: set[str] = set()

        # 合約風控
        self.risk_manager = FuturesRiskManager(fc)

        # 合約執行器
        self.executor = FuturesOrderExecutor(
            self.exchange, fc.mode,
            is_testnet=self.settings.exchange.testnet,
        )

        # 從 Supabase 恢復持倉
        self._restore_positions()

        # 策略路由器 + LLM
        self.router = StrategyRouter()
        self.llm_engine = LLMDecisionEngine(self.settings.llm)

        attach_supabase_handler(self._db)

        # 持倉對齊器
        self._reconciler = PositionReconciler(
            spot_exchange=None,
            futures_exchange=self.exchange,
            spot_risk=None,
            futures_risk=self.risk_manager,
            db=self._db,
            settings=self.settings,
        )
        try:
            self._reconciler.reconcile_futures()
        except Exception:
            logger.exception("啟動合約持倉對齊失敗，繼續運行")

        # OrderFlow 聚合器
        self._aggregators: dict[str, BarAggregator] = {}
        self._last_trade_id: dict[str, int] = {}
        self._last_strategy_slot: dict[str, int] = {}

        self._running = False
        self._start_time: float = 0.0

    def _restore_positions(self) -> None:
        """從 Supabase 恢復合約持倉。"""
        mode = self.settings.futures.mode.value
        rows = self._db.load_positions(mode, market_type="futures")
        fc = self.settings.futures
        for row in rows:
            symbol = row.get("symbol", "")
            qty = row.get("quantity", 0)
            entry = row.get("entry_price", 0)
            side = row.get("side", "long")
            leverage = row.get("leverage", fc.leverage)
            sl = row.get("stop_loss", 0) or 0
            tp = row.get("take_profit", 0) or 0
            if symbol and qty > 0 and entry > 0:
                # 若 DB 無 SL/TP，用固定百分比計算
                if sl <= 0 or tp <= 0:
                    if side == "long":
                        sl = entry * (1 - fc.stop_loss_pct)
                        tp = entry * (1 + fc.take_profit_pct)
                    else:
                        sl = entry * (1 + fc.stop_loss_pct)
                        tp = entry * (1 - fc.take_profit_pct)
                self.risk_manager.add_position(
                    symbol, side, qty, entry, leverage,
                    stop_loss_price=sl, take_profit_price=tp,
                )
        if rows:
            logger.info("已從 Supabase 恢復 %d 筆合約 %s 模式持倉", len(rows), mode)

    def _create_all_strategies(self) -> None:
        """建立所有策略（與現貨共用策略配置）。"""
        self.strategies.clear()
        default_tf = self.settings.futures.timeframe
        strat_list = self.settings.strategies_config.strategies

        for strat_cfg in strat_list:
            name = strat_cfg.get("name", "")
            params = strat_cfg.get("params", {})
            timeframe = strat_cfg.get("timeframe", "")

            if name in OHLCV_STRATEGY_REGISTRY:
                params["_timeframe"] = timeframe or default_tf
                self.strategies.append(OHLCV_STRATEGY_REGISTRY[name](params))
                logger.info("載入合約策略: %s (%s)", name, params["_timeframe"])
            elif name == "tia_orderflow":
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
                self.strategies.append(TiaBTCOrderFlowStrategy(of_params))
                logger.info("載入合約策略: %s (orderflow)", name)
            else:
                logger.warning("未知策略: %s，跳過", name)

        if not self.strategies:
            params = {"_timeframe": default_tf}
            self.strategies.append(SMACrossoverStrategy(params))
            logger.info("使用預設 sma_crossover 策略 (%s)", default_tf)

    def _get_strategy_fingerprint(self) -> str:
        configs = self.settings.strategies_config.strategies
        return str(sorted((c.get("name"), c.get("timeframe"), str(c.get("params"))) for c in configs))

    def run(self) -> None:
        """啟動合約交易迴圈。"""
        self._running = True
        sig_module.signal(sig_module.SIGINT, self._shutdown)

        fc = self.settings.futures
        all_names = [s.name for s in self.strategies]
        logger.info(
            "啟動合約交易: 交易對=%s, 時間框架=%s, 槓桿=%dx, 策略=%s, LLM=%s",
            fc.pairs, fc.timeframe, fc.leverage, all_names,
            "啟用" if self.llm_engine.enabled else "停用",
        )

        # 設定所有交易對的槓桿和保證金模式
        for symbol in fc.pairs:
            try:
                self.exchange.ensure_leverage_and_margin(symbol)
            except Exception as e:
                logger.error("設定 %s 槓桿/保證金失敗: %s", symbol, e)

        self._start_time = time.monotonic()
        cycle = self._db.get_last_cycle_num()
        if cycle > 0:
            logger.info("從 Supabase 接續 cycle_num=%d", cycle)

        while self._running:
            cycle += 1
            cycle_id = f"fc{cycle}-{uuid.uuid4().hex[:8]}"
            logger.info("=============================================")

            # 載入最新配置
            new_cfg = self._db.load_config()
            if new_cfg is not None:
                try:
                    self.settings = Settings.from_dict(new_cfg, self.settings)
                    self._config_version = self._db._last_config_version
                    logger.info("已套用 Supabase 新配置 (version=%d)", self._config_version)

                    new_fp = self._get_strategy_fingerprint()
                    if new_fp != self._strategy_fingerprint:
                        self._create_all_strategies()
                        self._cache_loaded.clear()
                        self._last_strategy_slot.clear()
                        self._strategy_fingerprint = new_fp
                except Exception as e:
                    logger.error("套用 Supabase 配置失敗: %s", e)

            # 處理每個交易對
            for symbol in self.settings.futures.pairs:
                try:
                    self._process_symbol(symbol, cycle_id, cycle)
                except Exception:
                    logger.exception("%s處理合約交易對時發生錯誤", _L1)

            # 記錄保證金帳戶快照
            try:
                self._record_margin_snapshot()
            except Exception:
                logger.debug("記錄保證金快照失敗", exc_info=True)

            # 定期持倉對齊（每 4 個 cycle）
            if cycle % 4 == 0:
                try:
                    self._reconciler.reconcile_futures()
                except Exception:
                    logger.debug("定期合約持倉對齊失敗", exc_info=True)

            # 心跳
            uptime = int(time.monotonic() - self._start_time)
            self._db.update_bot_status(
                cycle_num=cycle,
                status="running_futures",
                config_ver=self._config_version,
                pairs=list(self.settings.futures.pairs),
                uptime_sec=uptime,
                mode=self.settings.futures.mode.value,
            )
            self._db.flush_logs()

            if self._running:
                logger.info(
                    "=============================================",
                )
                time.sleep(self.settings.futures.check_interval_seconds)

    def _process_symbol(self, symbol: str, cycle_id: str, cycle: int) -> None:
        """處理單一合約交易對。"""
        fc = self.settings.futures

        # 1. 按 timeframe 分組抓取 K 線
        ohlcv_strategies = [s for s in self.strategies if s.data_feed_type == DataFeedType.OHLCV]

        # 統一排程：per-symbol slot，用最小 timeframe 的分鐘數
        min_tf_min = min(
            (TF_MINUTES.get(s.timeframe, 9999) for s in ohlcv_strategies),
            default=15,
        )
        now = datetime.now(timezone.utc)
        minutes_since_midnight = now.hour * 60 + now.minute
        slot = minutes_since_midnight // min_tf_min

        # 收集訂單流（每輪）
        for strategy in self.strategies:
            if strategy.data_feed_type != DataFeedType.ORDER_FLOW:
                continue
            try:
                if symbol not in self._cache_loaded:
                    strategy.load_cache(symbol)
                    self._cache_loaded.add(symbol)
                raw_trades = self.exchange.fetch_agg_trades(symbol, limit=1000)
                if raw_trades:
                    agg = self._aggregators.setdefault(
                        symbol,
                        BarAggregator(
                            interval_seconds=self.settings.orderflow.bar_interval_seconds,
                            tick_size=self.settings.orderflow.tick_size,
                        ),
                    )
                    _, new_id = strategy.feed_trades(
                        symbol, raw_trades, agg,
                        self._last_trade_id.get(symbol, 0),
                    )
                    if new_id > 0:
                        self._last_trade_id[symbol] = new_id
            except Exception:
                logger.exception("%s[%s] 訂單流資料收集失敗", _L2, strategy.name)

        # Slot 防重複
        last = self._last_strategy_slot.get(symbol, -1)
        if slot == last:
            return
        self._last_strategy_slot[symbol] = slot

        # 按 timeframe 分組
        tf_groups: dict[str, list] = {}
        for s in ohlcv_strategies:
            tf = s.timeframe or fc.timeframe
            tf_groups.setdefault(tf, []).append(s)

        tf_dataframes: dict[str, object] = {}
        for tf, group in tf_groups.items():
            max_req = max(s.required_candles for s in group)
            try:
                tf_dataframes[tf] = self.data_fetcher.fetch_ohlcv(
                    symbol, timeframe=tf, limit=max(max_req + 10, 100), cache_ttl=30,
                )
            except Exception:
                logger.exception("%s抓取 %s K 線失敗", _L2, tf)

        if not tf_dataframes:
            logger.warning("%s%s 無可用 K 線資料", _L1, symbol)
            return

        finest_tf = min(tf_dataframes, key=lambda t: TF_MINUTES.get(t, 9999))
        finest_df = tf_dataframes[finest_tf]
        current_price = float(finest_df["close"].iloc[-1])
        logger.info("%s%s 現價: %.2f USDT", _L1, symbol, current_price)
        self._db.insert_market_snapshot(symbol, current_price, mode=self.settings.futures.mode.value)

        # 2. 停損停利檢查
        for side in ("long", "short"):
            pos = self.risk_manager.get_position(symbol, side)
            if not pos:
                continue
            if self.executor.is_live and self.risk_manager.has_exchange_sl_tp(symbol, side):
                if self._sync_sl_tp_orders(symbol, side):
                    continue
            if not self.risk_manager.has_exchange_sl_tp(symbol, side):
                sl_tp_signal = self.risk_manager.check_stop_loss_take_profit(
                    symbol, side, current_price,
                )
                if sl_tp_signal in (Signal.SELL, Signal.COVER):
                    logger.info("%s觸發%s停損/停利 → 平倉", _L2, side)
                    self._execute_close(symbol, side, current_price, cycle_id)

        # 3. 收集策略結論
        self.router.clear()

        for strategy in self.strategies:
            verdict = None
            try:
                if strategy.data_feed_type == DataFeedType.OHLCV:
                    tf = strategy.timeframe or fc.timeframe
                    df = tf_dataframes.get(tf)
                    if df is None or len(df) < strategy.required_candles:
                        continue
                    verdict = strategy.generate_verdict(df)
                elif strategy.data_feed_type == DataFeedType.ORDER_FLOW:
                    verdict = strategy.latest_verdict(symbol)
            except Exception:
                logger.exception("%s[%s] 策略執行失敗", _L2, strategy.name)

            if verdict:
                self.router.collect(verdict)
                self._db.insert_verdict(
                    symbol, verdict.strategy_name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning, cycle_id,
                    market_type="futures",
                    timeframe=verdict.timeframe,
                    mode=self.settings.futures.mode.value,
                )
                abbr = strategy.name[:3]
                tf_label = verdict.timeframe or "of"
                sig_str = f"{verdict.signal.value} {verdict.confidence:.0%}"
                logger.info(
                    "%s[%s|%-3s] %-9s — %s",
                    _L2, abbr, tf_label, sig_str, verdict.reasoning[:80],
                )

        verdicts = self.router.get_verdicts()
        if not verdicts:
            return

        # 5. LLM 決策
        decision = self._make_decision(symbol, verdicts, current_price, cycle_id)
        if not decision or decision.action == Signal.HOLD:
            return

        # 6. 訊號轉換：根據持倉狀態將策略 BUY/SELL 轉為合約動作
        action = self._convert_signal(symbol, decision.action)
        if action == Signal.HOLD:
            return

        # 7. 風控評估
        margin_info = self.exchange.get_futures_balance()
        available_margin = margin_info["available_balance"]
        margin_ratio = self.exchange.get_margin_ratio()

        risk_output = self.risk_manager.evaluate(
            action, symbol, current_price, available_margin, margin_ratio,
        )

        if not risk_output.approved:
            logger.info("%s合約風控拒絕: %s", _L2, risk_output.reason)
            return

        # 8. 執行交易
        if action in (Signal.BUY, Signal.SHORT):
            side = "long" if action == Signal.BUY else "short"
            self._execute_open(symbol, side, current_price, risk_output, cycle_id)
        elif action in (Signal.SELL, Signal.COVER):
            side = "long" if action == Signal.SELL else "short"
            self._execute_close(symbol, side, current_price, cycle_id)

    def _convert_signal(self, symbol: str, raw_signal: Signal) -> Signal:
        """
        根據持倉狀態轉換策略訊號為合約動作。

        策略 SELL + 持有多倉 → 平多 (SELL)
        策略 SELL + 無持倉   → 開空 (SHORT)
        策略 BUY  + 持有空倉 → 平空 (COVER)
        策略 BUY  + 無持倉   → 開多 (BUY)
        """
        has_long = self.risk_manager.get_position(symbol, "long") is not None
        has_short = self.risk_manager.get_position(symbol, "short") is not None

        if raw_signal == Signal.SELL:
            if has_long:
                return Signal.SELL    # 平多
            elif not has_short:
                return Signal.SHORT   # 開空
            else:
                return Signal.HOLD    # 已有空倉，不操作

        elif raw_signal == Signal.BUY:
            if has_short:
                return Signal.COVER   # 平空
            elif not has_long:
                return Signal.BUY     # 開多
            else:
                return Signal.HOLD    # 已有多倉，不操作

        # SHORT/COVER 直接透傳（來自 LLM 直接決策）
        elif raw_signal in (Signal.SHORT, Signal.COVER):
            return raw_signal

        return Signal.HOLD

    def _make_decision(
        self, symbol: str, verdicts: list[StrategyVerdict],
        current_price: float, cycle_id: str,
    ):
        """LLM 決策（或 fallback 加權投票）。"""
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if not non_hold:
            return None

        # 建立投資組合狀態
        margin_info = self.exchange.get_futures_balance()
        margin_ratio = self.exchange.get_margin_ratio()
        positions_info = []
        for pos_key, pos in self.risk_manager.get_all_positions().items():
            entry = pos["entry_price"]
            pnl = (current_price - entry) * pos["quantity"]
            if pos["side"] == "short":
                pnl = (entry - current_price) * pos["quantity"]
            positions_info.append(PositionInfo(
                symbol=pos["symbol"],
                quantity=pos["quantity"],
                entry_price=entry,
                current_price=current_price,
                unrealized_pnl=pnl,
                side=pos["side"],
                leverage=pos.get("leverage", self.settings.futures.leverage),
                liquidation_price=pos.get("liquidation_price"),
                market_type="futures",
            ))

        portfolio = PortfolioState(
            available_balance=margin_info["available_balance"],
            used_capital_pct=1.0 - (
                margin_info["available_balance"] / margin_info["total_margin_balance"]
                if margin_info["total_margin_balance"] > 0 else 0
            ),
            positions=positions_info,
            daily_realized_pnl=self.risk_manager._daily_pnl,
            max_positions=self.settings.futures.max_open_positions,
            current_position_count=self.risk_manager.open_position_count,
            market_type="futures",
            margin_balance=margin_info["total_margin_balance"],
            margin_ratio=margin_ratio,
            leverage=self.settings.futures.leverage,
        )

        decision = self.llm_engine.decide_sync(
            symbol=symbol,
            verdicts=verdicts,
            portfolio=portfolio,
            current_price=current_price,
            market_type="futures",
        )

        if decision:
            # 將 action 字串轉為 Signal
            action_str = decision.action if isinstance(decision.action, str) else decision.action.value
            action_map = {
                "BUY": Signal.BUY, "SELL": Signal.SELL,
                "HOLD": Signal.HOLD, "SHORT": Signal.SHORT, "COVER": Signal.COVER,
            }
            llm_signal = action_map.get(action_str.upper(), Signal.HOLD)

            # 檢查 LLM 決策是否有策略支持
            # 平倉信號（SELL/COVER）是降低風險的動作，不受覆蓋攔截限制
            is_close_signal = llm_signal in (Signal.SELL, Signal.COVER)
            strategy_signals = {v.signal for v in verdicts}
            # 策略只發 BUY/SELL/HOLD，但 LLM 可發 SHORT/COVER
            # SELL（看跌）應視為支持 SHORT，BUY（看漲）應視為支持 COVER
            if Signal.SELL in strategy_signals:
                strategy_signals.add(Signal.SHORT)
            if Signal.BUY in strategy_signals:
                strategy_signals.add(Signal.COVER)
            if llm_signal != Signal.HOLD and llm_signal not in strategy_signals and not is_close_signal:
                if decision.confidence >= 0.7:
                    logger.warning(
                        "%s[LLM] 覆蓋策略: %s (信心 %.2f)，無策略支持 → 倉位縮半",
                        _L2, action_str, decision.confidence,
                    )
                    self._db.insert_llm_decision(
                        symbol, action_str, decision.confidence,
                        decision.reasoning, self.settings.llm.model, cycle_id,
                        market_type="futures",
                        mode=self.settings.futures.mode.value,
                    )
                else:
                    reject = f"無策略支持且信心不足 ({decision.confidence:.2f} < 0.7)"
                    logger.warning(
                        "%s[LLM] 決策 %s %s → HOLD",
                        _L2, action_str, reject,
                    )
                    self._db.insert_llm_decision(
                        symbol, action_str, decision.confidence,
                        decision.reasoning, self.settings.llm.model, cycle_id,
                        market_type="futures",
                        executed=False, reject_reason=reject,
                        mode=self.settings.futures.mode.value,
                    )
                    return None
            else:
                self._db.insert_llm_decision(
                    symbol, action_str, decision.confidence,
                    decision.reasoning, self.settings.llm.model, cycle_id,
                    market_type="futures",
                    mode=self.settings.futures.mode.value,
                )

            decision.action = llm_signal

        return decision

    def _execute_open(
        self, symbol: str, side: str, price: float,
        risk_output, cycle_id: str,
    ) -> None:
        """執行開倉（開多或開空）。"""
        signal_map = {"long": Signal.BUY, "short": Signal.SHORT}
        order = self.executor.execute(signal_map[side], symbol, risk_output)
        if not order:
            return

        fill_price = order.get("price") or price
        # 優先用 filled（實際成交量），若為 0 則用 amount（截斷後下單量）
        fill_qty = order.get("filled") or order.get("amount") or risk_output.quantity

        # 記錄持倉
        self.risk_manager.add_position(
            symbol, side, fill_qty, fill_price,
            leverage=risk_output.leverage,
        )

        # 掛 SL/TP
        sl_tp = self.executor.place_sl_tp(
            symbol, fill_qty, side,
            risk_output.take_profit_price,
            risk_output.stop_loss_price,
        )
        if sl_tp:
            pos = self.risk_manager.get_position(symbol, side)
            if pos:
                pos["tp_order_id"] = sl_tp.get("tp_order_id")
                pos["sl_order_id"] = sl_tp.get("sl_order_id")

        # 寫入 Supabase
        mode = self.settings.futures.mode.value
        order["source"] = "bot_futures"
        self._db.insert_order(
            order, mode=mode, cycle_id=cycle_id,
            market_type="futures", position_side=side,
            leverage=risk_output.leverage,
        )

        self._db.upsert_position(symbol, {
            "side": side,
            "quantity": fill_qty,
            "entry_price": fill_price,
            "current_price": price,
            "unrealized_pnl": 0.0,
            "stop_loss": risk_output.stop_loss_price,
            "take_profit": risk_output.take_profit_price,
            "leverage": risk_output.leverage,
            "liquidation_price": risk_output.liquidation_price,
            "margin_type": self.settings.futures.margin_type,
        }, mode=mode, market_type="futures")

        side_label = "做多" if side == "long" else "做空"
        logger.info(
            "%s合約%s %s: 數量=%.8f, 價格=%.2f, 槓桿=%dx",
            _L2, side_label, symbol, fill_qty, fill_price, risk_output.leverage,
        )

    def _execute_close(
        self, symbol: str, side: str, price: float,
        cycle_id: str = "",
    ) -> None:
        """執行平倉（平多或平空）。"""
        # 取消掛單
        tp_id, sl_id = self.risk_manager.get_sl_tp_order_ids(symbol, side)
        if tp_id or sl_id:
            self.executor.cancel_sl_tp(symbol, tp_id, sl_id)
            logger.info("%s已取消合約 SL/TP 掛單", _L3)

        # 風控確認
        risk_output = self.risk_manager.evaluate(
            Signal.SELL if side == "long" else Signal.COVER,
            symbol, price, 0,
        )
        if not risk_output.approved:
            logger.warning("%s合約平倉風控拒絕: %s", _L2, risk_output.reason)
            return

        # 執行平倉
        close_signal = Signal.SELL if side == "long" else Signal.COVER
        order = self.executor.execute(close_signal, symbol, risk_output)
        if not order:
            return

        exit_price = order.get("price") or price
        pnl = self.risk_manager.remove_position(symbol, side, exit_price)

        mode = self.settings.futures.mode.value
        order["source"] = "bot_futures"
        self._db.insert_order(
            order, mode=mode, cycle_id=cycle_id,
            market_type="futures", position_side=side,
            leverage=self.settings.futures.leverage,
            reduce_only=True,
        )
        self._db.delete_position(symbol, mode=mode, market_type="futures", side=side)

        side_label = "平多" if side == "long" else "平空"
        logger.info(
            "%s合約%s %s: 出場=%.2f, PnL=%.2f USDT",
            _L2, side_label, symbol, exit_price, pnl,
        )

    def _sync_sl_tp_orders(self, symbol: str, side: str) -> bool:
        """檢查交易所 SL/TP 掛單是否已成交。"""
        tp_id, sl_id = self.risk_manager.get_sl_tp_order_ids(symbol, side)

        for order_id in [tp_id, sl_id]:
            if not order_id:
                continue
            try:
                status = self.exchange.get_order_status(order_id, symbol)
                if status.get("status") in ("closed", "filled"):
                    exit_price = status.get("price", 0) or status.get("average", 0)
                    pnl = self.risk_manager.remove_position(symbol, side, exit_price)

                    mode = self.settings.futures.mode.value
                    self._db.delete_position(
                        symbol, mode=mode, market_type="futures", side=side,
                    )

                    label = "停利" if order_id == tp_id else "停損"
                    logger.info(
                        "%s合約%s成交: %s %s exit=%.2f PnL=%.2f",
                        _L2, label, symbol, side, exit_price, pnl,
                    )
                    return True
            except Exception as e:
                logger.debug("查詢合約掛單 %s 失敗: %s", order_id, e)

        return False

    def _record_margin_snapshot(self) -> None:
        """記錄合約保證金帳戶快照。"""
        try:
            margin = self.exchange.get_futures_balance()
            ratio = self.exchange.get_margin_ratio()
            self._db.insert_futures_margin(
                wallet_balance=margin["total_wallet_balance"],
                available_balance=margin["available_balance"],
                unrealized_pnl=margin["total_unrealized_pnl"],
                margin_balance=margin["total_margin_balance"],
                margin_ratio=ratio,
                mode=self.settings.futures.mode.value,
            )

            # 保證金比率警告
            if ratio >= self.settings.futures.max_margin_ratio:
                logger.warning(
                    "保證金比率警告: %.1f%% >= %.0f%%",
                    ratio * 100, self.settings.futures.max_margin_ratio * 100,
                )
        except Exception as e:
            logger.debug("記錄保證金快照失敗: %s", e)

    def _shutdown(self, signum, frame) -> None:
        logger.info("收到中止訊號，正在關閉合約 Bot...")
        self._running = False
