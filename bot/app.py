"""主應用程式 — 交易機器人核心迴圈。

流程：持續運行 → 定時抓取 K 線 + aggTrade → 所有策略產生結論 → LLM 分析 → 執行。
"""

from __future__ import annotations

import signal
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from bot.config.settings import FuturesConfig, LoanGuardConfig, Settings
from bot.db.supabase_client import SupabaseWriter
from bot.data.bar_aggregator import BarAggregator
from bot.data.fetcher import DataFetcher
from bot.exchange.binance_client import BinanceClient
from bot.exchange.futures_client import FuturesBinanceClient
from bot.execution.executor import OrderExecutor
from bot.execution.futures_executor import FuturesOrderExecutor
from bot.execution.order_manager import OrderManager
from bot.llm.decision_engine import LLMDecisionEngine
from bot.llm.schemas import PortfolioState, PositionInfo
from bot.logging_config import get_logger
from bot.logging_config.logger import attach_supabase_handler, setup_logging
from bot.risk.manager import RiskManager
from bot.risk.futures_manager import FuturesRiskManager
from bot.config.constants import DataFeedType
from bot.strategy.base import BaseStrategy, Strategy
from bot.strategy.router import StrategyRouter
from bot.strategy.signals import Signal, StrategyVerdict
from bot.strategy.bollinger_breakout import BollingerBreakoutStrategy
from bot.strategy.macd_momentum import MACDMomentumStrategy
from bot.strategy.rsi_oversold import RSIOversoldStrategy
from bot.strategy.sma_crossover import SMACrossoverStrategy
from bot.strategy.tia_orderflow import TiaBTCOrderFlowStrategy

logger = get_logger("app")

# Log indentation prefixes
_L1 = "  "        # Level 1: symbol
_L2 = "    "      # Level 2: strategy / action
_L3 = "      "    # Level 3: sub-detail

# Timeframe → 分鐘數對映
_TF_MINUTES: dict[str, int] = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080, "1M": 43200,
}


def _current_slot(timeframe: str, tz_offset_hours: int = 8) -> tuple[int, str]:
    """計算當前時間區間編號（從 UTC+tz_offset 00:00 起算）及區間起始時間字串。"""
    tf_min = _TF_MINUTES.get(timeframe, 60)
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc + timedelta(hours=tz_offset_hours)
    minutes_since_midnight = local_now.hour * 60 + local_now.minute
    slot = minutes_since_midnight // tf_min + 1  # 從 1 開始
    # 區間起始時間
    slot_start_min = (slot - 1) * tf_min
    slot_h, slot_m = divmod(slot_start_min, 60)
    slot_start_str = f"{slot_h:02d}:{slot_m:02d}"
    return slot, slot_start_str


# OHLCV 策略註冊表
OHLCV_STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "sma_crossover": SMACrossoverStrategy,
    "rsi_oversold": RSIOversoldStrategy,
    "bollinger_breakout": BollingerBreakoutStrategy,
    "macd_momentum": MACDMomentumStrategy,
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

        # Supabase 寫入層（提前建立，以便載入線上配置）
        self._db = SupabaseWriter()
        self._config_version: int = 0

        # 優先載入 Supabase 線上配置（覆蓋本地 config.yaml）
        remote_cfg = self._db.load_config()
        if remote_cfg is not None:
            try:
                self.settings = Settings.from_dict(remote_cfg, self.settings)
                self._config_version = self._db._last_config_version
                logger.info("已載入 Supabase 線上配置 (version=%d)", self._config_version)
            except Exception as e:
                logger.warning("載入 Supabase 配置失敗，使用本地配置: %s", e)
        else:
            logger.info("Supabase 無配置或未變更，使用本地 config.yaml")

        logger.info("初始化交易機器人 (模式=%s)", self.settings.spot.mode)

        self.exchange = BinanceClient(self.settings.exchange)
        self.data_fetcher = DataFetcher(self.exchange)

        # 統一策略清單（OHLCV + 訂單流共用）
        self.strategies: list[Strategy] = []
        self._create_all_strategies()
        self._strategy_fingerprint = self._get_strategy_fingerprint()

        # 合約策略清單（獨立或共用現貨策略）
        self._futures_strategies: list[Strategy] = self.strategies

        # 訂單流快取載入追蹤
        self._cache_loaded: set[str] = set()

        self.risk_manager = RiskManager(self.settings.spot, self.settings.horizon_risk)
        self.executor = OrderExecutor(self.exchange, self.settings.spot.mode)
        self.order_manager = OrderManager()

        # 從 Supabase 恢復持倉到 RiskManager（重啟接續）
        self._restore_positions()

        # 策略路由器
        self.router = StrategyRouter()

        # LLM 決策引擎
        self.llm_engine = LLMDecisionEngine(self.settings.llm)

        # LLM client（借款監控用）
        from bot.llm.client import ClaudeCLIClient
        self._llm_client = ClaudeCLIClient(self.settings.llm)

        attach_supabase_handler(self._db)

        # ── 合約交易模組（若啟用）──
        self._futures_enabled = self.settings.futures.enabled
        self._futures_exchange: FuturesBinanceClient | None = None
        self._futures_risk: FuturesRiskManager | None = None
        self._futures_executor: FuturesOrderExecutor | None = None
        if self._futures_enabled:
            self._init_futures()

        # 每個交易對一個 BarAggregator（跨輪保留，持續聚合）
        self._aggregators: dict[str, BarAggregator] = {}

        # 記錄每個交易對最後處理的 trade ID（避免重複餵入）
        self._last_trade_id: dict[str, int] = {}

        # 借貸去重：記錄每個交易對上次寫入的 LTV，相同就跳過
        self._last_ltv: dict[str, float] = {}

        # 策略 slot 防重複：記錄每個策略上次執行的時間段 slot
        self._last_strategy_slot: dict[str, int] = {}

        self._running = False
        self._start_time: float = 0.0
        self._last_llm_size_pct: float = 0.0

    def _restore_positions(self) -> None:
        """從 Supabase positions 表恢復持倉到 RiskManager（重啟接續）。"""
        mode = self.settings.spot.mode.value
        rows = self._db.load_positions(mode)
        for row in rows:
            symbol = row.get("symbol", "")
            qty = row.get("quantity", 0)
            entry = row.get("entry_price", 0)
            if symbol and qty > 0 and entry > 0:
                self.risk_manager.add_position(symbol, qty, entry)
        if rows:
            logger.info("已從 Supabase 恢復 %d 筆 %s 模式持倉", len(rows), mode)

    def _init_futures(self) -> None:
        """初始化合約交易模組。"""
        fc = self.settings.futures
        logger.info(
            "初始化合約模組: 交易對=%s, 槓桿=%dx, 保證金=%s, 模式=%s",
            fc.pairs, fc.leverage, fc.margin_type, fc.mode.value,
        )
        self._futures_exchange = FuturesBinanceClient(self.settings.exchange, fc)
        self._futures_data_fetcher = DataFetcher(self._futures_exchange)
        self._futures_risk = FuturesRiskManager(fc, self.settings.horizon_risk)
        self._futures_executor = FuturesOrderExecutor(self._futures_exchange, fc.mode)
        self._create_futures_strategies()
        self._restore_futures_positions()

    def _restore_futures_positions(self) -> None:
        """從 Supabase 恢復合約持倉。"""
        if not self._futures_risk:
            return
        mode = self.settings.futures.mode.value
        rows = self._db.load_positions(mode, market_type="futures")
        for row in rows:
            symbol = row.get("symbol", "")
            qty = row.get("quantity", 0)
            entry = row.get("entry_price", 0)
            side = row.get("side", "long")
            leverage = row.get("leverage", self.settings.futures.leverage)
            if symbol and qty > 0 and entry > 0:
                self._futures_risk.add_position(
                    symbol, side, qty, entry, leverage,
                )
        if rows:
            logger.info("已從 Supabase 恢復 %d 筆合約 %s 模式持倉", len(rows), mode)

    def _create_all_strategies(self) -> None:
        """根據 config 建立所有策略（OHLCV + 訂單流），統一存入 self.strategies。"""
        self.strategies.clear()

        for strat_cfg in self.settings.strategies_config.strategies:
            name = strat_cfg.get("name", "")
            params = strat_cfg.get("params", {})
            interval_n = strat_cfg.get("interval_n", 60)

            if name in OHLCV_STRATEGY_REGISTRY:
                params["_interval_n"] = interval_n
                self.strategies.append(OHLCV_STRATEGY_REGISTRY[name](params))
                logger.info("載入策略: %s (interval_n=%d)", name, interval_n)
            elif name == "tia_orderflow":
                of_params = {
                    "_interval_n": interval_n,
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
                logger.info("載入策略: %s (interval_n=%d)", name, interval_n)
            else:
                logger.warning("未知策略: %s，跳過", name)

        if not self.strategies:
            params = {**self.settings.strategy.params, "_interval_n": 60}
            self.strategies.append(SMACrossoverStrategy(params))
            logger.info("使用預設 sma_crossover 策略")

    def _create_futures_strategies(self) -> None:
        """建立合約專用策略清單；若 futures.strategies 為空則共用現貨策略。"""
        fc = self.settings.futures
        if not fc.strategies:
            self._futures_strategies = self.strategies
            return

        self._futures_strategies = []
        for strat_cfg in fc.strategies:
            name = strat_cfg.get("name", "")
            params = strat_cfg.get("params", {})
            interval_n = strat_cfg.get("interval_n", 60)

            if name in OHLCV_STRATEGY_REGISTRY:
                params["_interval_n"] = interval_n
                self._futures_strategies.append(OHLCV_STRATEGY_REGISTRY[name](params))
                logger.info("載入合約策略: %s (interval_n=%d)", name, interval_n)
            else:
                logger.warning("未知合約策略: %s，跳過", name)

        if not self._futures_strategies:
            self._futures_strategies = self.strategies
            logger.info("合約策略清單為空，共用現貨策略")

    def _get_strategy_fingerprint(self) -> str:
        """取得目前策略配置的指紋（用於偵測變更）。"""
        configs = self.settings.strategies_config.strategies
        return str(sorted((c.get("name"), c.get("interval_n"), str(c.get("params"))) for c in configs))

    def run(self) -> None:
        """啟動交易迴圈（持續運行）。"""
        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)

        all_names = [s.name for s in self.strategies]
        lg = self.settings.loan_guard
        fc = self.settings.futures
        logger.info(
            "啟動交易: 現貨=%s, 時間框架=%s, 策略=%s, LLM=%s, 借款監控=%s, 合約=%s",
            self.settings.spot.pairs,
            self.settings.spot.timeframe,
            all_names,
            "啟用" if self.llm_engine.enabled else "停用",
            f"啟用 (低買>{lg.danger_ltv:.0%}, 高賣<{lg.low_ltv:.0%}, 目標={lg.target_ltv:.0%}{', 模擬' if lg.dry_run else ''})"
            if lg.enabled else "停用",
            f"啟用 (交易對={fc.pairs}, {fc.leverage}x, {fc.mode.value})" if fc.enabled else "停用",
        )

        self._start_time = time.monotonic()
        cycle = self._db.get_last_cycle_num()
        if cycle > 0:
            logger.info("從 Supabase 接續 cycle_num=%d", cycle)
        while self._running:
            cycle += 1
            cycle_id = f"c{cycle}-{uuid.uuid4().hex[:8]}"
            slot, slot_start = _current_slot(self.settings.spot.timeframe)
            logger.info(
                "═══ 第 %d 輪分析開始 (%s) | %s 第 %d 區間 (自 %s) ═══",
                cycle, cycle_id, self.settings.spot.timeframe, slot, slot_start,
            )

            # 從 Supabase 載入最新配置（若版本已變更）
            new_cfg = self._db.load_config()
            if new_cfg is not None:
                try:
                    self.settings = Settings.from_dict(new_cfg, self.settings)
                    self._config_version = self._db._last_config_version
                    logger.info("已套用 Supabase 新配置 (version=%d)", self._config_version)

                    # 合約熱重載
                    if self.settings.futures.enabled and not self._futures_enabled:
                        self._futures_enabled = True
                        self._init_futures()
                        logger.info("合約模組已啟用")
                    elif not self.settings.futures.enabled and self._futures_enabled:
                        self._futures_enabled = False
                        self._futures_exchange = None
                        self._futures_risk = None
                        self._futures_executor = None
                        logger.info("合約模組已停用")

                    # 策略熱重載：偵測策略清單或參數變更
                    new_fp = self._get_strategy_fingerprint()
                    if new_fp != self._strategy_fingerprint:
                        old_names = [s.name for s in self.strategies]
                        self._create_all_strategies()
                        self._create_futures_strategies()
                        self._cache_loaded.clear()  # 訂單流快取需重新載入
                        self._last_strategy_slot.clear()  # 重置 slot 讓新策略立即執行
                        self._strategy_fingerprint = new_fp
                        new_names = [s.name for s in self.strategies]
                        logger.info("策略熱重載: %s → %s", old_names, new_names)
                except Exception as e:
                    logger.error("套用 Supabase 配置失敗: %s（保留舊配置）", e)

            for symbol in self.settings.spot.pairs:
                try:
                    self._process_symbol(symbol, cycle_id, cycle)
                except Exception:
                    logger.exception("%s處理時發生錯誤", _L1)

            # ── 合約交易對處理 ──
            if self._futures_enabled and self._futures_exchange:
                for symbol in self.settings.futures.pairs:
                    try:
                        self._process_futures_symbol(symbol, cycle_id, cycle)
                    except Exception:
                        logger.exception("%s[合約] %s 處理時發生錯誤", _L1, symbol)

                # 記錄合約保證金快照
                try:
                    self._record_futures_margin()
                except Exception:
                    logger.debug("合約保證金快照失敗", exc_info=True)

            # 借款 LTV 監控（AI 審核可能耗時，先寫一次心跳避免前端誤判離線）
            if self.settings.loan_guard.enabled:
                uptime_mid = int(time.monotonic() - self._start_time)
                self._db.update_bot_status(
                    cycle_num=cycle,
                    status="running",
                    config_ver=self._config_version,
                    pairs=list(self.settings.spot.pairs),
                    uptime_sec=uptime_mid,
                )
                try:
                    self._check_loan_health()
                except Exception:
                    logger.exception("%s借款監控發生錯誤", _L1)

            # 寫入帳戶餘額快照
            try:
                bal = self.exchange.get_balance()
                usdt_vals: dict[str, float | None] = {}
                for cur, amt in bal.items():
                    base = cur[2:] if cur.startswith("LD") else cur
                    if base in ("USDT", "USDC", "BUSD", "FDUSD"):
                        usdt_vals[cur] = amt
                    else:
                        try:
                            tk = self.exchange.get_ticker(f"{base}/USDT")
                            usdt_vals[cur] = amt * tk["last"]
                        except Exception:
                            usdt_vals[cur] = None
                snap_id = f"cycle-{cycle}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
                self._db.insert_balances(bal, usdt_vals, snap_id)
            except Exception:
                logger.debug("寫入帳戶餘額快照失敗", exc_info=True)

            # 更新 Supabase 心跳 + flush 日誌
            uptime = int(time.monotonic() - self._start_time)
            self._db.update_bot_status(
                cycle_num=cycle,
                status="running",
                config_ver=self._config_version,
                pairs=list(self.settings.spot.pairs),
                uptime_sec=uptime,
            )
            self._db.flush_logs()

            if self._running:
                logger.info(
                    "═══ 第 %d 輪完成，%d 秒後進行下一輪 ═══",
                    cycle, self.settings.spot.check_interval_seconds,
                )
                time.sleep(self.settings.spot.check_interval_seconds)

    def _process_symbol(self, symbol: str, cycle_id: str = "", cycle: int = 1) -> None:
        """
        處理單一交易對：收集所有策略結論 → LLM/加權投票 → 執行。
        """
        # ── 1. 抓取 K 線 ──
        ohlcv_strategies = [s for s in self.strategies if s.data_feed_type == DataFeedType.OHLCV]
        max_required = max((s.required_candles for s in ohlcv_strategies), default=50)
        df = self.data_fetcher.fetch_ohlcv(
            symbol,
            timeframe=self.settings.spot.timeframe,
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

        # ── 3a. 訂單流：每輪都收集資料（確保 bar 不遺失） ──
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

        # ── 3b. 收集本輪應執行的策略結論 ──
        #   interval_n 用時間 slot：minutes_since_midnight // interval_n
        #   同一 slot 內只執行一次，避免重複；slot 變化即觸發，不怕錯過整點
        #   例如 interval_n=1 每分鐘、60 每小時、240 每 4 小時、1440 每天
        self.router.clear()
        now = datetime.now(timezone.utc)
        minutes_since_midnight = now.hour * 60 + now.minute

        for strategy in self.strategies:
            if strategy.interval_n > 0:
                slot = minutes_since_midnight // strategy.interval_n
                last_slot = self._last_strategy_slot.get(strategy.name, -1)
                if slot == last_slot:
                    continue
                self._last_strategy_slot[strategy.name] = slot

            verdict = None
            try:
                if strategy.data_feed_type == DataFeedType.OHLCV:
                    if len(df) < strategy.required_candles:
                        continue
                    verdict = strategy.generate_verdict(df)

                else:  # ORDER_FLOW — 資料已在 3a 收集，這裡只取結論
                    verdict = strategy.latest_verdict(symbol)
            except Exception:
                logger.exception("%s[%s] 策略執行失敗", _L2, strategy.name)
                continue

            if verdict is not None:
                self.router.collect(verdict)
                self._db.insert_verdict(
                    symbol, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning, cycle_id,
                )
                logger.info(
                    "%s[%s] %s (信心 %.2f) — %s",
                    _L2, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning[:80],
                )

        verdicts = self.router.get_verdicts()
        if not verdicts:
            logger.info("%s無策略結論，跳過", _L2)
            return

        # ── 4. 預計算風控指標（供 LLM 參考） ──
        risk_metrics = None
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if non_hold:
            primary_signal = non_hold[0].signal
            if primary_signal == Signal.BUY:
                try:
                    balance = self.exchange.get_balance()
                    usdt_balance = balance.get("USDT", 0.0)
                    risk_metrics = self.risk_manager.pre_calculate_metrics(
                        signal=primary_signal,
                        symbol=symbol,
                        price=current_price,
                        balance=usdt_balance,
                        ohlcv=df,
                    )
                except Exception as e:
                    logger.warning("%s預計算風控指標失敗: %s", _L2, e)

        # ── 5. 多時間框架摘要 ──
        mtf_summary = self._fetch_mtf_summary(symbol)

        # ── 6. LLM 決策 或 加權投票 ──
        self._llm_override = False
        self._last_decision = None
        self._last_llm_size_pct = 0.0  # 每個交易對重置，避免跨幣對污染
        final_signal, final_confidence, horizon = self._make_decision(
            verdicts, symbol, current_price, cycle_id,
            risk_metrics=risk_metrics,
            mtf_summary=mtf_summary,
        )

        if final_signal == Signal.HOLD:
            logger.info("%s→ HOLD（不動作）", _L2)
            return

        # 信心度門檻：低於 min_confidence 視為 HOLD
        min_conf = self.settings.llm.min_confidence
        if final_confidence < min_conf:
            logger.info(
                "%s→ %s 信心 %.2f 低於門檻 %.2f → 視為 HOLD",
                _L2, final_signal.value, final_confidence, min_conf,
            )
            return

        logger.info(
            "%s→ %s (信心 %.2f, horizon=%s)",
            _L2, final_signal.value, final_confidence, horizon,
        )

        # ── 7. 風控 + 執行 ──
        if final_signal == Signal.BUY:
            try:
                balance = self.exchange.get_balance()
                usdt_balance = balance.get("USDT", 0.0)

                # 若 USDT 不足但有 Earn 持倉（LDUSDT），自動贖回
                if usdt_balance < 1.0 and balance.get("LDUSDT", 0.0) > 0:
                    logger.info(
                        "%sUSDT 餘額不足 (%.2f)，偵測到 LDUSDT %.2f，嘗試自動贖回...",
                        _L2, usdt_balance, balance["LDUSDT"],
                    )
                    redeemed = self.exchange.redeem_all_usdt_earn()
                    if redeemed > 0:
                        logger.info("%s已贖回 %.4f USDT，重新取得餘額", _L2, redeemed)
                        time.sleep(1)  # 等待贖回到帳
                        balance = self.exchange.get_balance()
                        usdt_balance = balance.get("USDT", 0.0)
                    else:
                        logger.warning("%s贖回失敗或無可贖回", _L2)
            except Exception as e:
                logger.warning("%s取得餘額失敗，跳過買入: %s", _L2, e)
                return

            # 取得 LLM 建議的倉位佔比
            llm_size_pct = self._last_llm_size_pct

            risk_output = self.risk_manager.evaluate(
                final_signal, symbol, current_price, usdt_balance,
                horizon=horizon,
                llm_size_pct=llm_size_pct,
            )
            if not risk_output.approved:
                logger.info("%s風控拒絕: %s", _L2, risk_output.reason)
                return
            # LLM 覆蓋策略時倉位縮半
            if self._llm_override and risk_output.quantity > 0:
                risk_output.quantity /= 2
                logger.info("%s[覆蓋] 倉位縮半: %.6f", _L2, risk_output.quantity)
            self._execute_buy(symbol, current_price, risk_output, cycle_id)

        elif final_signal == Signal.SELL:
            self._execute_sell(symbol, current_price, cycle_id)

    def _make_decision(
        self,
        verdicts: list[StrategyVerdict],
        symbol: str,
        current_price: float,
        cycle_id: str = "",
        market_type: str = "spot",
        risk_metrics: "RiskMetrics | None" = None,
        mtf_summary: str = "",
    ) -> tuple[Signal, float, str]:
        """所有非 HOLD 決策強制過 LLM 審查；LLM 失敗直接 HOLD。

        Returns:
            (signal, confidence, horizon)
        """
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if not non_hold:
            return Signal.HOLD, 0.0, "medium"

        # ── LLM 審查（強制） ──
        if self.llm_engine.enabled:
            try:
                portfolio = (
                    self._build_futures_portfolio_state(symbol, current_price)
                    if market_type == "futures"
                    else self._build_portfolio_state(symbol, current_price)
                )
                decision = self.llm_engine.decide_sync(
                    verdicts=verdicts,
                    portfolio=portfolio,
                    symbol=symbol,
                    current_price=current_price,
                    market_type=market_type,
                    risk_metrics=risk_metrics,
                    mtf_summary=mtf_summary,
                )
                self._db.insert_llm_decision(
                    symbol, decision.action, decision.confidence,
                    decision.reasoning, self.settings.llm.model,
                    cycle_id, market_type=market_type,
                )

                # 驗證 horizon 值
                horizon = decision.horizon if decision.horizon in ("short", "medium", "long") else "medium"

                # 儲存 LLM 建議的倉位佔比供風控參考
                self._last_llm_size_pct = decision.position_size_pct

                logger.info(
                    "%s[LLM] %s (信心 %.2f, horizon=%s) — %s",
                    _L2, decision.action, decision.confidence,
                    horizon, decision.reasoning[:100],
                )
                action_map = {
                    "BUY": Signal.BUY, "SELL": Signal.SELL,
                    "SHORT": Signal.SHORT, "COVER": Signal.COVER,
                }
                llm_signal = action_map.get(decision.action, Signal.HOLD)

                # 有條件覆蓋：LLM 方向無策略支持時，要求高信心
                strategy_signals = {v.signal for v in verdicts}
                if llm_signal != Signal.HOLD and llm_signal not in strategy_signals:
                    if decision.confidence >= 0.7:
                        logger.warning(
                            "%s[LLM] 覆蓋策略: %s (信心 %.2f)，無策略支持（策略: %s）→ 倉位縮半",
                            _L2, decision.action, decision.confidence,
                            ", ".join(s.value for s in strategy_signals),
                        )
                        self._llm_override = True
                    else:
                        logger.warning(
                            "%s[LLM] 決策 %s 無策略支持且信心不足 (%.2f < 0.7) → HOLD",
                            _L2, decision.action, decision.confidence,
                        )
                        return Signal.HOLD, 0.0, "medium"

                return llm_signal, decision.confidence, horizon
            except Exception as e:
                logger.warning("%sLLM 決策失敗 → HOLD: %s", _L2, e)

        # LLM 關閉或失敗 → 直接 HOLD
        return Signal.HOLD, 0.0, "medium"

    def _build_portfolio_state(
        self, symbol: str, current_price: float
    ) -> PortfolioState:
        """建構投資組合狀態（供 LLM 參考）。"""
        try:
            balance = self.exchange.get_balance()
            usdt_balance = balance.get("USDT", 0.0) + balance.get("LDUSDT", 0.0)
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
            max_positions=self.settings.spot.max_open_positions,
            current_position_count=self.risk_manager.open_position_count,
            daily_realized_pnl=self.risk_manager._daily_pnl,
            daily_risk_remaining=usdt_balance * self.settings.spot.max_daily_loss_pct + self.risk_manager._daily_pnl,
        )

    def _build_futures_portfolio_state(
        self, symbol: str, current_price: float
    ) -> PortfolioState:
        """建構合約投資組合狀態（供合約 LLM 參考）。"""
        if not self._futures_exchange or not self._futures_risk:
            return self._build_portfolio_state(symbol, current_price)

        try:
            balance = self._futures_exchange.get_futures_balance()
            available = balance["available_balance"]
        except Exception:
            available = 0.0

        positions = []
        for key, pos_data in self._futures_risk._open_positions.items():
            sym = pos_data["symbol"]
            entry = pos_data["entry_price"]
            qty = pos_data["quantity"]
            side = pos_data["side"]
            price_now = current_price if sym == symbol else entry
            if side == "long":
                pnl = (price_now - entry) * qty
            else:
                pnl = (entry - price_now) * qty
            pnl_pct = pnl / (entry * qty) if entry * qty > 0 else 0.0

            positions.append(PositionInfo(
                symbol=f"{sym}({side})",
                quantity=qty,
                entry_price=entry,
                current_price=price_now,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
            ))

        return PortfolioState(
            available_balance=available,
            positions=positions,
            max_positions=self.settings.futures.max_open_positions,
            current_position_count=self._futures_risk.open_position_count,
            daily_realized_pnl=self._futures_risk._daily_pnl,
            daily_risk_remaining=available * self.settings.futures.max_daily_loss_pct + self._futures_risk._daily_pnl,
        )

    def _fetch_mtf_summary(self, symbol: str, market_type: str = "spot") -> str:
        """抓取多時間框架 K 線並產生 Markdown 摘要。失敗回傳空字串。"""
        mtf_cfg = self.settings.mtf
        if not mtf_cfg.enabled:
            return ""

        try:
            fetcher = (
                self._futures_data_fetcher
                if market_type == "futures" and hasattr(self, "_futures_data_fetcher")
                else self.data_fetcher
            )
            tf_data = fetcher.fetch_multi_timeframe(
                symbol,
                timeframes=list(mtf_cfg.timeframes),
                limit=mtf_cfg.candle_limit,
                cache_ttl=mtf_cfg.cache_ttl_seconds,
            )
            if not tf_data:
                return ""

            from bot.utils.indicators import compute_mtf_summary
            from bot.llm.summarizer import summarize_multi_timeframe

            summaries = []
            for tf, df in tf_data.items():
                s = compute_mtf_summary(df, tf)
                if s:
                    summaries.append(s)

            if not summaries:
                return ""

            result = summarize_multi_timeframe(summaries)
            logger.info(
                "%s[MTF] 已產生 %d 個時間框架摘要 (%s)",
                _L2, len(summaries), ", ".join(s.timeframe for s in summaries),
            )
            return result
        except Exception as e:
            logger.warning("%s[MTF] 多框架摘要生成失敗: %s", _L2, e)
            return ""

    def _execute_buy(self, symbol: str, price: float, risk_output,
                     cycle_id: str = "") -> None:
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
                stop_loss_price=risk_output.stop_loss_price,
                take_profit_price=risk_output.take_profit_price,
            )
            self.order_manager.add_order(order)
            _mode = self.settings.spot.mode.value
            self._db.insert_order(order, mode=_mode, cycle_id=cycle_id)
            self._db.upsert_position(symbol, {
                "quantity": risk_output.quantity,
                "entry_price": fill_price,
                "current_price": fill_price,
                "unrealized_pnl": 0,
                "stop_loss": risk_output.stop_loss_price,
                "take_profit": risk_output.take_profit_price,
            }, mode=_mode)
            logger.info(
                "%s✓ BUY %s @ %.2f, qty=%.8f (SL=%.2f, TP=%.2f)",
                _L3, symbol, fill_price, risk_output.quantity,
                risk_output.stop_loss_price, risk_output.take_profit_price,
            )

    def _execute_sell(self, symbol: str, price: float,
                      cycle_id: str = "") -> None:
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
            _mode = self.settings.spot.mode.value
            self._db.insert_order(order, mode=_mode, cycle_id=cycle_id)
            self._db.delete_position(symbol, mode=_mode)
            logger.info("%s✓ SELL %s @ %.2f, PnL=%.2f USDT", _L3, symbol, fill_price, pnl)

    # ════════════════════════════════════════════════════════════
    # 合約交易方法
    # ════════════════════════════════════════════════════════════

    def _process_futures_symbol(self, symbol: str, cycle_id: str = "",
                                cycle: int = 1) -> None:
        """處理單一合約交易對。"""
        assert self._futures_exchange and self._futures_risk and self._futures_executor

        # 確保槓桿和保證金模式已設定
        self._futures_exchange.ensure_leverage_and_margin(symbol)

        # ── 1. 取得 K 線 ──
        ohlcv_strategies = [s for s in self._futures_strategies if s.data_feed_type == DataFeedType.OHLCV]
        max_required = max((s.required_candles for s in ohlcv_strategies), default=50)
        df = self._futures_exchange.get_ohlcv(
            symbol,
            timeframe=self.settings.futures.timeframe,
            limit=max(max_required + 10, 100),
        )

        if len(df) < max_required:
            logger.warning("%s[合約] %s K 線資料不足: %d/%d", _L1, symbol, len(df), max_required)
            return

        current_price = float(df["close"].iloc[-1])
        logger.info("%s[合約] %s 現價: %.2f USDT", _L1, symbol, current_price)

        # ── 2. 停損停利（多倉 + 空倉都要檢查）──
        for side in ("long", "short"):
            pos = self._futures_risk.get_position(symbol, side)
            if not pos:
                continue

            # Live 模式：檢查交易所掛單
            if self._futures_executor.is_live and self._futures_risk.has_exchange_sl_tp(symbol, side):
                if self._sync_futures_sl_tp(symbol, side):
                    continue

            # Paper 模式（或 live 無掛單）：輪詢
            if not self._futures_risk.has_exchange_sl_tp(symbol, side):
                sl_tp_signal = self._futures_risk.check_stop_loss_take_profit(
                    symbol, side, current_price,
                )
                if sl_tp_signal in (Signal.SELL, Signal.COVER):
                    close_side = "long" if sl_tp_signal == Signal.SELL else "short"
                    logger.info("%s[合約] %s %s倉觸發停損/停利", _L2, symbol, close_side)
                    self._execute_futures_close(symbol, close_side, current_price, cycle_id)

        # ── 3. 收集策略結論 ──
        self.router.clear()
        now = datetime.now(timezone.utc)
        minutes_since_midnight = now.hour * 60 + now.minute

        for strategy in self._futures_strategies:
            if strategy.data_feed_type != DataFeedType.OHLCV:
                continue
            if strategy.interval_n > 0:
                slot = minutes_since_midnight // strategy.interval_n
                # 合約用獨立的 slot key 避免跟現貨衝突
                slot_key = f"futures:{strategy.name}"
                last_slot = self._last_strategy_slot.get(slot_key, -1)
                if slot == last_slot:
                    continue
                self._last_strategy_slot[slot_key] = slot

            try:
                if len(df) < strategy.required_candles:
                    continue
                verdict = strategy.generate_verdict(df)
            except Exception:
                logger.exception("%s[合約][%s] 策略執行失敗", _L2, strategy.name)
                continue

            if verdict is not None:
                self.router.collect(verdict)
                self._db.insert_verdict(
                    symbol, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning, cycle_id,
                    market_type="futures",
                )

        verdicts = self.router.get_verdicts()
        if not verdicts:
            return

        # ── 4. 預計算合約風控指標（供 LLM 參考） ──
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if not non_hold:
            return

        risk_metrics = None
        primary_signal = non_hold[0].signal
        if primary_signal in (Signal.BUY, Signal.SHORT):
            side = "long" if primary_signal == Signal.BUY else "short"
            try:
                balance = self._futures_exchange.get_futures_balance()
                available = balance["available_balance"]
                margin_ratio = self._futures_exchange.get_margin_ratio()
                risk_metrics = self._futures_risk.pre_calculate_metrics(
                    signal=primary_signal,
                    symbol=symbol,
                    side=side,
                    price=current_price,
                    available_margin=available,
                    margin_ratio=margin_ratio,
                    ohlcv=df,
                )
            except Exception as e:
                logger.warning("%s[合約] 預計算風控指標失敗: %s", _L2, e)

        # ── 5. 多時間框架摘要 ──
        mtf_summary = self._fetch_mtf_summary(symbol, market_type="futures")

        # ── 6. LLM 審查（用現有 LLM 引擎，傳入 market_type="futures"）──
        self._llm_override = False
        self._last_llm_size_pct = 0.0  # 每個交易對重置，避免跨幣對污染
        final_signal, final_confidence, horizon = self._make_decision(
            verdicts, symbol, current_price, cycle_id,
            market_type="futures",
            risk_metrics=risk_metrics,
            mtf_summary=mtf_summary,
        )

        if final_signal == Signal.HOLD:
            return

        min_conf = self.settings.futures.min_confidence
        if final_confidence < min_conf:
            logger.info(
                "%s[合約] %s 信心 %.2f 低於門檻 %.2f → HOLD",
                _L2, final_signal.value, final_confidence, min_conf,
            )
            return

        # 訊號轉換：根據持倉狀態將 BUY/SELL 轉為合約訊號
        final_signal = self._translate_futures_signal(final_signal, symbol)
        if final_signal == Signal.HOLD:
            return

        logger.info("%s[合約] → %s (信心 %.2f, horizon=%s)", _L2, final_signal.value, final_confidence, horizon)

        # ── 7. 風控 + 執行 ──
        if final_signal in (Signal.BUY, Signal.SHORT):
            self._execute_futures_open(
                symbol, final_signal, current_price, cycle_id,
                ohlcv=df, horizon=horizon,
            )
        elif final_signal in (Signal.SELL, Signal.COVER):
            side = "long" if final_signal == Signal.SELL else "short"
            self._execute_futures_close(symbol, side, current_price, cycle_id)

    def _translate_futures_signal(self, signal: Signal, symbol: str) -> Signal:
        """
        將策略/LLM 的 BUY/SELL 轉換為合約訊號。

        邏輯:
            SELL + 持有多倉 → SELL（平多）
            SELL + 無持倉   → SHORT（開空）
            BUY  + 持有空倉 → COVER（平空）
            BUY  + 無持倉   → BUY（開多）
        """
        assert self._futures_risk

        has_long = self._futures_risk.get_position(symbol, "long") is not None
        has_short = self._futures_risk.get_position(symbol, "short") is not None

        if signal == Signal.SELL:
            if has_long:
                return Signal.SELL   # 平多
            else:
                return Signal.SHORT  # 開空
        elif signal == Signal.BUY:
            if has_short:
                return Signal.COVER  # 平空
            else:
                return Signal.BUY    # 開多
        elif signal == Signal.SHORT:
            if has_short:
                logger.info("%s[合約] 已有空倉 %s，忽略 SHORT", _L2, symbol)
                return Signal.HOLD
            return Signal.SHORT
        elif signal == Signal.COVER:
            if not has_short:
                logger.info("%s[合約] 無空倉 %s，忽略 COVER", _L2, symbol)
                return Signal.HOLD
            return Signal.COVER
        return Signal.HOLD

    def _execute_futures_open(self, symbol: str, signal: Signal,
                              price: float, cycle_id: str = "",
                              ohlcv: pd.DataFrame | None = None,
                              horizon: str = "medium") -> None:
        """執行合約開倉（開多或開空）。"""
        assert self._futures_exchange and self._futures_risk and self._futures_executor

        side = "long" if signal == Signal.BUY else "short"

        # 取可用保證金
        try:
            balance = self._futures_exchange.get_futures_balance()
            available = balance["available_balance"]
            margin_ratio = self._futures_exchange.get_margin_ratio()
        except Exception as e:
            logger.warning("%s[合約] 取得保證金失敗: %s", _L2, e)
            return

        llm_size_pct = self._last_llm_size_pct

        risk_output = self._futures_risk.evaluate(
            signal, symbol, price, available, margin_ratio, ohlcv=ohlcv,
            horizon=horizon, llm_size_pct=llm_size_pct,
        )
        if not risk_output.approved:
            logger.info("%s[合約] 風控拒絕: %s", _L2, risk_output.reason)
            return

        # LLM 覆蓋策略時倉位縮半（與現貨一致）
        if self._llm_override and risk_output.quantity > 0:
            risk_output.quantity /= 2
            logger.info("%s[合約][覆蓋] 倉位縮半: %.6f", _L2, risk_output.quantity)

        order = self._futures_executor.execute(signal, symbol, risk_output)
        if not order:
            return

        fill_price = order.get("price", price)
        leverage = self.settings.futures.leverage

        # 掛 SL/TP
        tp_order_id, sl_order_id = None, None
        oco_info = self._futures_executor.place_sl_tp(
            symbol, risk_output.quantity, side,
            risk_output.take_profit_price, risk_output.stop_loss_price,
        )
        if oco_info:
            tp_order_id = oco_info.get("tp_order_id")
            sl_order_id = oco_info.get("sl_order_id")

        self._futures_risk.add_position(
            symbol, side, risk_output.quantity, fill_price, leverage,
            tp_order_id=tp_order_id, sl_order_id=sl_order_id,
            stop_loss_price=risk_output.stop_loss_price,
            take_profit_price=risk_output.take_profit_price,
        )

        _mode = self.settings.futures.mode.value
        side_label = "開多" if side == "long" else "開空"
        self._db.insert_order(
            order, mode=_mode, cycle_id=cycle_id,
            market_type="futures", position_side=side,
            leverage=leverage, reduce_only=False,
        )
        self._db.upsert_position(symbol, {
            "side": side,
            "leverage": leverage,
            "quantity": risk_output.quantity,
            "entry_price": fill_price,
            "current_price": fill_price,
            "unrealized_pnl": 0,
            "stop_loss": risk_output.stop_loss_price,
            "take_profit": risk_output.take_profit_price,
            "liquidation_price": risk_output.liquidation_price,
            "margin_type": self.settings.futures.margin_type,
        }, mode=_mode, market_type="futures")

        logger.info(
            "%s[合約] %s %s @ %.2f, qty=%.8f, %dx (SL=%.2f, TP=%.2f, 清算=%.2f)",
            _L3, side_label, symbol, fill_price, risk_output.quantity,
            leverage, risk_output.stop_loss_price, risk_output.take_profit_price,
            risk_output.liquidation_price,
        )

    def _execute_futures_close(self, symbol: str, side: str,
                               price: float, cycle_id: str = "") -> None:
        """執行合約平倉（平多或平空）。"""
        assert self._futures_exchange and self._futures_risk and self._futures_executor

        close_signal = Signal.SELL if side == "long" else Signal.COVER

        # 取消 SL/TP 掛單
        tp_id, sl_id = self._futures_risk.get_sl_tp_order_ids(symbol, side)
        if tp_id or sl_id:
            self._futures_executor.cancel_sl_tp(symbol, tp_id, sl_id)

        risk_output = self._futures_risk.evaluate(close_signal, symbol, price, 0)
        if not risk_output.approved:
            return

        order = self._futures_executor.execute(close_signal, symbol, risk_output)
        if not order:
            return

        fill_price = order.get("price", price)
        pnl = self._futures_risk.remove_position(symbol, side, fill_price)

        _mode = self.settings.futures.mode.value
        side_label = "平多" if side == "long" else "平空"
        self._db.insert_order(
            order, mode=_mode, cycle_id=cycle_id,
            market_type="futures", position_side=side,
            leverage=self.settings.futures.leverage, reduce_only=True,
        )
        self._db.delete_position(symbol, mode=_mode, market_type="futures", side=side)

        logger.info(
            "%s[合約] %s %s @ %.2f, PnL=%.2f USDT",
            _L3, side_label, symbol, fill_price, pnl,
        )

    def _sync_futures_sl_tp(self, symbol: str, side: str) -> bool:
        """檢查合約 SL/TP 掛單是否已成交。"""
        assert self._futures_exchange and self._futures_risk

        tp_id, sl_id = self._futures_risk.get_sl_tp_order_ids(symbol, side)
        for order_id, label in [(tp_id, "停利"), (sl_id, "停損")]:
            if not order_id:
                continue
            try:
                status = self._futures_exchange.get_order_status(order_id, symbol)
                if status["status"] == "closed":
                    fill_price = status.get("price", 0)
                    pnl = self._futures_risk.remove_position(symbol, side, fill_price)
                    _mode = self.settings.futures.mode.value
                    self._db.delete_position(symbol, mode=_mode, market_type="futures", side=side)
                    logger.info(
                        "%s[合約] 交易所 %s 成交: %s %s @ %.2f, PnL=%.2f USDT",
                        _L2, label, symbol, side, fill_price, pnl,
                    )
                    return True
            except Exception as e:
                logger.debug("[合約] 查詢訂單 %s 失敗: %s", order_id, e)
        return False

    def _record_futures_margin(self) -> None:
        """記錄合約保證金帳戶快照。"""
        assert self._futures_exchange
        balance = self._futures_exchange.get_futures_balance()
        margin_ratio = self._futures_exchange.get_margin_ratio()
        self._db.insert_futures_margin(
            wallet_balance=balance["total_wallet_balance"],
            available_balance=balance["available_balance"],
            unrealized_pnl=balance["total_unrealized_pnl"],
            margin_balance=balance["total_margin_balance"],
            margin_ratio=margin_ratio,
        )

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
            pair_key = f"{collateral_coin}/{loan_coin}"

            # LTV 跟上次一樣 → 跳過寫入和 AI 審核，節省空間和 API 費用
            ltv_rounded = round(ltv, 4)
            if self._last_ltv.get(pair_key) == ltv_rounded:
                logger.debug("%s[借款] %s LTV=%.1f%% 無變化，跳過", _L1, label, ltv * 100)
                continue
            self._last_ltv[pair_key] = ltv_rounded

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

            # 同步該幣對的借貸調整歷史到 Supabase
            try:
                history = self.exchange.fetch_loan_adjust_history(loan_coin, collateral_coin)
                if history:
                    count = self._db.sync_loan_adjustments(history)
                    if count:
                        logger.info("%s[借款] 同步 %s 調整歷史: 新增 %d 筆", _L1, label, count)
            except Exception as e:
                logger.debug("%s[借款] 同步 %s 調整歷史失敗: %s", _L1, label, e)

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

        # 查可用 USDT 餘額（含 Earn 持倉）
        try:
            balance = self.exchange.get_balance()
            usdt_available = balance.get("USDT", 0.0) + balance.get("LDUSDT", 0.0)
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
