"""主應用程式 — 交易機器人核心迴圈（編排器）。

流程：持續運行 → 定時抓取 K 線 + aggTrade → 所有策略產生結論 → LLM 分析 → 執行。

拆分後的模組：
- spot_handler.py   — 現貨交易處理
- futures_handler.py — 合約交易處理
- loan_guardian.py   — 借貸監控
"""

from __future__ import annotations

import signal
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from bot.config.constants import TF_MINUTES
from bot.config.settings import Settings
from bot.db.supabase_client import SupabaseWriter
from bot.data.fetcher import DataFetcher
from bot.exchange.binance_client import BinanceClient
from bot.exchange.futures_client import FuturesBinanceClient
from bot.execution.executor import OrderExecutor
from bot.execution.futures_executor import FuturesOrderExecutor
from bot.execution.order_manager import OrderManager
from bot.futures_handler import FuturesHandler
from bot.llm.decision_engine import LLMDecisionEngine
from bot.llm.schemas import PortfolioState, PositionInfo
from bot.loan_guardian import LoanGuardian
from bot.logging_config import get_logger
from bot.logging_config.logger import attach_supabase_handler, setup_logging
from bot.risk.manager import RiskManager
from bot.risk.futures_manager import FuturesRiskManager
from bot.spot_handler import SpotHandler
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

def _current_slot(timeframe: str, tz_offset_hours: int = 8) -> tuple[int, str]:
    """計算當前時間區間編號（從 UTC+tz_offset 00:00 起算）及區間起始時間字串。"""
    tf_min = TF_MINUTES.get(timeframe, 60)
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc + timedelta(hours=tz_offset_hours)
    minutes_since_midnight = local_now.hour * 60 + local_now.minute
    slot = minutes_since_midnight // tf_min + 1  # 從 1 開始
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


# ════════════════════════════════════════════════════════════
# DecisionResult — 取代隱式 self._llm_override / _last_llm_size_pct
# ════════════════════════════════════════════════════════════

@dataclass
class DecisionResult:
    """_make_decision() 的完整回傳值。"""
    signal: Signal
    confidence: float
    horizon: str = "medium"
    llm_override: bool = False
    llm_size_pct: float = 0.0


# ════════════════════════════════════════════════════════════
# 共用函數（現貨 + 合約都需要）
# ════════════════════════════════════════════════════════════

def make_llm_decision(
    llm_engine: LLMDecisionEngine,
    db: SupabaseWriter,
    verdicts: list[StrategyVerdict],
    symbol: str,
    current_price: float,
    cycle_id: str,
    market_type: str,
    risk_metrics,
    mtf_summary: str,
    portfolio: PortfolioState,
    model_name: str,
) -> DecisionResult:
    """共用 LLM 決策邏輯（現貨 + 合約）。

    所有非 HOLD 決策強制過 LLM 審查；LLM 失敗直接 HOLD。
    """
    non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
    if not non_hold:
        return DecisionResult(signal=Signal.HOLD, confidence=0.0)

    if not llm_engine.enabled:
        return DecisionResult(signal=Signal.HOLD, confidence=0.0)

    try:
        decision = llm_engine.decide_sync(
            verdicts=verdicts,
            portfolio=portfolio,
            symbol=symbol,
            current_price=current_price,
            market_type=market_type,
            risk_metrics=risk_metrics,
            mtf_summary=mtf_summary,
        )
        db.insert_llm_decision(
            symbol, decision.action, decision.confidence,
            decision.reasoning, model_name,
            cycle_id, market_type=market_type,
        )

        horizon = decision.horizon if decision.horizon in ("short", "medium", "long") else "medium"

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

        llm_override = False
        strategy_signals = {v.signal for v in verdicts}
        if llm_signal != Signal.HOLD and llm_signal not in strategy_signals:
            if decision.confidence >= 0.7:
                logger.warning(
                    "%s[LLM] 覆蓋策略: %s (信心 %.2f)，無策略支持（策略: %s）→ 倉位縮半",
                    _L2, decision.action, decision.confidence,
                    ", ".join(s.value for s in strategy_signals),
                )
                llm_override = True
            else:
                logger.warning(
                    "%s[LLM] 決策 %s 無策略支持且信心不足 (%.2f < 0.7) → HOLD",
                    _L2, decision.action, decision.confidence,
                )
                return DecisionResult(signal=Signal.HOLD, confidence=0.0)

        return DecisionResult(
            signal=llm_signal,
            confidence=decision.confidence,
            horizon=horizon,
            llm_override=llm_override,
            llm_size_pct=decision.position_size_pct,
        )
    except Exception as e:
        logger.warning("%sLLM 決策失敗 → HOLD: %s", _L2, e)
        return DecisionResult(signal=Signal.HOLD, confidence=0.0)


def build_mtf_summary(
    tf_dataframes: dict[str, "pd.DataFrame"],
    enabled: bool = True,
) -> str:
    """從已抓取的多時間框架 DataFrame 產生 MTF 摘要（不再另外 fetch）。

    Args:
        tf_dataframes: {timeframe: DataFrame} — 策略已抓取的 K 線。
        enabled: MTF 開關（從 settings.mtf.enabled 傳入）。
    """
    if not enabled or not tf_dataframes:
        return ""

    try:
        from bot.utils.indicators import compute_mtf_summary
        from bot.llm.summarizer import summarize_multi_timeframe

        summaries = []
        for tf, df in tf_dataframes.items():
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


# ════════════════════════════════════════════════════════════
# TradingBot — 編排器
# ════════════════════════════════════════════════════════════

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

        # ── 現貨 Handler ──
        self._spot_handler = SpotHandler(
            settings=self.settings,
            exchange=self.exchange,
            data_fetcher=self.data_fetcher,
            risk_manager=self.risk_manager,
            executor=self.executor,
            order_manager=self.order_manager,
            db=self._db,
            llm_engine=self.llm_engine,
            router=self.router,
        )

        # ── 合約交易模組（若啟用）──
        self._futures_enabled = self.settings.futures.enabled
        self._futures_exchange: FuturesBinanceClient | None = None
        self._futures_risk: FuturesRiskManager | None = None
        self._futures_executor: FuturesOrderExecutor | None = None
        self._futures_handler: FuturesHandler | None = None
        if self._futures_enabled:
            self._init_futures()

        # ── 借貸監控 ──
        self._loan_guardian: LoanGuardian | None = None
        if self.settings.loan_guard.enabled:
            self._loan_guardian = LoanGuardian(
                exchange=self.exchange,
                db=self._db,
                llm_client=self._llm_client,
                config=self.settings.loan_guard,
            )

        self._running = False
        self._start_time: float = 0.0

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

        # 建立合約 Handler
        self._futures_handler = FuturesHandler(
            settings=self.settings,
            futures_exchange=self._futures_exchange,
            futures_data_fetcher=self._futures_data_fetcher,
            futures_risk=self._futures_risk,
            futures_executor=self._futures_executor,
            db=self._db,
            llm_engine=self.llm_engine,
            router=StrategyRouter(),  # 合約用獨立 router
        )

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
        default_tf = self.settings.spot.timeframe

        for strat_cfg in self.settings.strategies_config.strategies:
            name = strat_cfg.get("name", "")
            params = strat_cfg.get("params", {})
            timeframe = strat_cfg.get("timeframe", "")

            if name in OHLCV_STRATEGY_REGISTRY:
                params["_timeframe"] = timeframe or default_tf
                self.strategies.append(OHLCV_STRATEGY_REGISTRY[name](params))
                logger.info("載入策略: %s (%s)", name, params["_timeframe"])
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
                logger.info("載入策略: %s (orderflow)", name)
            else:
                logger.warning("未知策略: %s，跳過", name)

        if not self.strategies:
            params = {**self.settings.strategy.params, "_timeframe": default_tf}
            self.strategies.append(SMACrossoverStrategy(params))
            logger.info("使用預設 sma_crossover 策略 (%s)", default_tf)

    def _create_futures_strategies(self) -> None:
        """建立合約專用策略清單；若 futures.strategies 為空則共用現貨策略。"""
        fc = self.settings.futures
        if not fc.strategies:
            self._futures_strategies = self.strategies
            return

        self._futures_strategies = []
        default_tf = fc.timeframe
        for strat_cfg in fc.strategies:
            name = strat_cfg.get("name", "")
            params = strat_cfg.get("params", {})
            timeframe = strat_cfg.get("timeframe", "")

            if name in OHLCV_STRATEGY_REGISTRY:
                params["_timeframe"] = timeframe or default_tf
                self._futures_strategies.append(OHLCV_STRATEGY_REGISTRY[name](params))
                logger.info("載入合約策略: %s (%s)", name, params["_timeframe"])
            else:
                logger.warning("未知合約策略: %s，跳過", name)

        if not self._futures_strategies:
            self._futures_strategies = self.strategies
            logger.info("合約策略清單為空，共用現貨策略")

    def _get_strategy_fingerprint(self) -> str:
        """取得目前策略配置的指紋（用於偵測變更）。"""
        configs = self.settings.strategies_config.strategies
        return str(sorted((c.get("name"), c.get("timeframe"), str(c.get("params"))) for c in configs))

    # ════════════════════════════════════════════════════════════
    # 決策委派函數（給 handler 用的 callback）
    # ════════════════════════════════════════════════════════════

    def _make_decision(
        self,
        verdicts: list[StrategyVerdict],
        symbol: str,
        current_price: float,
        cycle_id: str = "",
        market_type: str = "spot",
        risk_metrics=None,
        mtf_summary: str = "",
        portfolio: PortfolioState | None = None,
    ) -> DecisionResult:
        """委派到 module-level make_llm_decision。"""
        if portfolio is None:
            portfolio = PortfolioState()
        return make_llm_decision(
            llm_engine=self.llm_engine,
            db=self._db,
            verdicts=verdicts,
            symbol=symbol,
            current_price=current_price,
            cycle_id=cycle_id,
            market_type=market_type,
            risk_metrics=risk_metrics,
            mtf_summary=mtf_summary,
            portfolio=portfolio,
            model_name=self.settings.llm.model,
        )

    # ════════════════════════════════════════════════════════════
    # 主迴圈
    # ════════════════════════════════════════════════════════════

    def run(self) -> None:
        """啟動交易迴圈（持續運行）。"""
        self._running = True
        signal.signal(signal.SIGINT, self._shutdown)

        all_names = [s.name[:3] for s in self.strategies]
        lg = self.settings.loan_guard
        fc = self.settings.futures
        sc = self.settings.spot

        logger.info("=============================================")
        logger.info("  [現貨] 交易對=%s  時間框架=%s  模式=%s", sc.pairs, sc.timeframe, sc.mode.value)
        logger.info("  [策略] %s", ", ".join(all_names))
        logger.info("  [LLM]  %s", "啟用" if self.llm_engine.enabled else "停用")
        if lg.enabled:
            logger.info(
                "  [借貸] 啟用 (低買>%s 高賣<%s 目標=%s%s)",
                f"{lg.danger_ltv:.0%}", f"{lg.low_ltv:.0%}", f"{lg.target_ltv:.0%}",
                ", 模擬" if lg.dry_run else "",
            )
        else:
            logger.info("  [借貸] 停用")
        if fc.enabled:
            logger.info("  [合約] 啟用 (交易對=%s, %dx, %s)", fc.pairs, fc.leverage, fc.mode.value)
        else:
            logger.info("  [合約] 停用")

        self._start_time = time.monotonic()
        cycle = self._db.get_last_cycle_num()
        if cycle > 0:
            logger.info("從 Supabase 接續 cycle_num=%d", cycle)
        while self._running:
            cycle += 1
            cycle_id = f"c{cycle}-{uuid.uuid4().hex[:8]}"
            slot, slot_start = _current_slot(self.settings.spot.timeframe)
            logger.info(
                "=============================================",
            )

            # 從 Supabase 載入最新配置（若版本已變更）
            self._reload_config_if_changed()

            # ── 現貨交易對處理 ──
            for symbol in self.settings.spot.pairs:
                try:
                    self._spot_handler.process_symbol(
                        symbol, cycle_id, cycle, self.strategies,
                        make_decision_fn=self._make_decision,
                    )
                except Exception:
                    logger.exception("%s處理時發生錯誤", _L1)

            # ── 合約交易對處理 ──
            if self._futures_enabled and self._futures_handler:
                for symbol in self.settings.futures.pairs:
                    try:
                        self._futures_handler.process_symbol(
                            symbol, cycle_id, cycle, self._futures_strategies,
                            make_decision_fn=self._make_decision,
                        )
                    except Exception:
                        logger.exception("%s[合約] %s 處理時發生錯誤", _L1, symbol)

                # 記錄合約保證金快照
                try:
                    self._futures_handler.record_margin()
                except Exception:
                    logger.debug("合約保證金快照失敗", exc_info=True)

            # ── 借款 LTV 監控 ──
            if self._loan_guardian:
                uptime_mid = int(time.monotonic() - self._start_time)
                self._db.update_bot_status(
                    cycle_num=cycle,
                    status="running",
                    config_ver=self._config_version,
                    pairs=list(self.settings.spot.pairs),
                    uptime_sec=uptime_mid,
                )
                try:
                    self._loan_guardian.check()
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
                    "=============================================",
                )
                time.sleep(self.settings.spot.check_interval_seconds)

    def _reload_config_if_changed(self) -> None:
        """從 Supabase 載入最新配置（若版本已變更）。"""
        new_cfg = self._db.load_config()
        if new_cfg is None:
            return

        try:
            self.settings = Settings.from_dict(new_cfg, self.settings)
            self._config_version = self._db._last_config_version
            logger.info("已套用 Supabase 新配置 (version=%d)", self._config_version)

            # 更新 handler 的 settings 參照
            self._spot_handler._settings = self.settings

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
                self._futures_handler = None
                logger.info("合約模組已停用")
            elif self._futures_handler:
                self._futures_handler._settings = self.settings

            # 借貸監控熱重載
            if self.settings.loan_guard.enabled and not self._loan_guardian:
                self._loan_guardian = LoanGuardian(
                    exchange=self.exchange,
                    db=self._db,
                    llm_client=self._llm_client,
                    config=self.settings.loan_guard,
                )
            elif not self.settings.loan_guard.enabled:
                self._loan_guardian = None
            elif self._loan_guardian:
                self._loan_guardian.config = self.settings.loan_guard

            # 策略熱重載：偵測策略清單或參數變更
            new_fp = self._get_strategy_fingerprint()
            if new_fp != self._strategy_fingerprint:
                old_names = [s.name for s in self.strategies]
                self._create_all_strategies()
                self._create_futures_strategies()
                self._spot_handler._cache_loaded.clear()
                self._spot_handler._last_strategy_slot.clear()
                if self._futures_handler:
                    self._futures_handler._last_strategy_slot.clear()
                self._strategy_fingerprint = new_fp
                new_names = [s.name for s in self.strategies]
                logger.info("策略熱重載: %s → %s", old_names, new_names)
        except Exception as e:
            logger.error("套用 Supabase 配置失敗: %s（保留舊配置）", e)

    def _shutdown(self, signum, frame) -> None:
        logger.info("收到中止訊號，正在關閉...")
        self._running = False
