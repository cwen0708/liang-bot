"""現貨交易處理模組 — 從 app.py 拆分而來。

負責：
- process_symbol() — 單一現貨交易對的完整處理流程
- _execute_buy() / _execute_sell() — 買入 / 賣出
- _sync_oco_order() — 檢查交易所 OCO 掛單
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from bot.config.constants import DataFeedType, TF_MINUTES
from bot.data.bar_aggregator import BarAggregator
from bot.logging_config import get_logger
from bot.llm.schemas import PortfolioState, PositionInfo
from bot.strategy.router import StrategyRouter
from bot.strategy.signals import Signal, StrategyVerdict

if TYPE_CHECKING:
    import pandas as pd
    from bot.config.settings import Settings
    from bot.data.fetcher import DataFetcher
    from bot.db.supabase_client import SupabaseWriter
    from bot.exchange.binance_native_client import BinanceClient
    from bot.execution.executor import OrderExecutor
    from bot.execution.order_manager import OrderManager
    from bot.llm.decision_engine import LLMDecisionEngine
    from bot.risk.manager import RiskManager
    from bot.strategy.base import Strategy
    from bot.strategy.router import StrategyRouter

logger = get_logger("spot_handler")

_L1 = "  "
_L2 = "    "
_L3 = "      "


class SpotHandler:
    """現貨交易處理器。"""

    def __init__(
        self,
        settings: Settings,
        exchange: BinanceClient,
        data_fetcher: DataFetcher,
        risk_manager: RiskManager,
        executor: OrderExecutor,
        order_manager: OrderManager,
        db: SupabaseWriter,
        llm_engine: LLMDecisionEngine,
        router: StrategyRouter,
    ) -> None:
        self._settings = settings
        self._exchange = exchange
        self._data_fetcher = data_fetcher
        self._risk = risk_manager
        self._executor = executor
        self._order_manager = order_manager
        self._db = db
        self._llm_engine = llm_engine
        self._router = router

        # 訂單流狀態
        self._aggregators: dict[str, BarAggregator] = {}
        self._last_trade_id: dict[str, int] = {}
        self._cache_loaded: set[str] = set()

        # 策略 slot 防重複
        self._last_strategy_slot: dict[str, int] = {}
        self._cooldown_until: dict[str, datetime] = {}  # symbol → 冷卻結束時間

    def process_symbol(
        self,
        symbol: str,
        cycle_id: str,
        cycle: int,
        strategies: list[Strategy],
        *,
        make_decision_fn,
    ) -> None:
        """處理單一交易對：收集所有策略結論 → LLM/加權投票 → 執行。

        Args:
            make_decision_fn: 共用 LLM 決策函數 (from app.py)
        """
        sc = self._settings.spot

        # ── 1. 按 timeframe 分組抓取 K 線 ──
        ohlcv_strategies = [s for s in strategies if s.data_feed_type == DataFeedType.OHLCV]

        # 統一排程：per-symbol slot，用最小 timeframe 的分鐘數
        min_tf_min = min(
            (TF_MINUTES.get(s.timeframe, 9999) for s in ohlcv_strategies),
            default=15,
        )
        now = datetime.now(timezone.utc)
        minutes_since_midnight = now.hour * 60 + now.minute
        slot = minutes_since_midnight // min_tf_min

        # ── 訂單流：每輪都收集資料（不受 slot 限制）──
        for strategy in strategies:
            if strategy.data_feed_type != DataFeedType.ORDER_FLOW:
                continue
            try:
                if symbol not in self._cache_loaded:
                    strategy.load_cache(symbol)
                    self._cache_loaded.add(symbol)

                raw_trades = self._exchange.fetch_agg_trades(symbol, limit=1000)
                if raw_trades:
                    agg = self._aggregators.setdefault(
                        symbol,
                        BarAggregator(
                            interval_seconds=self._settings.orderflow.bar_interval_seconds,
                            tick_size=self._settings.orderflow.tick_size,
                        ),
                    )
                    _, new_id = strategy.feed_trades(
                        symbol, raw_trades, agg,
                        self._last_trade_id.get(symbol, 0),
                    )
                    if new_id > 0:
                        self._last_trade_id[symbol] = new_id
            except Exception:
                logger.exception("%s[現貨][%s] 訂單流資料收集失敗", _L2, strategy.name)

        # Slot 防重複：同一 slot 只跑 orderflow 收集，不產 verdict
        last = self._last_strategy_slot.get(symbol, -1)
        if slot == last:
            return
        self._last_strategy_slot[symbol] = slot

        # 按 timeframe 分組
        tf_groups: dict[str, list] = {}
        for s in ohlcv_strategies:
            tf = s.timeframe or sc.timeframe
            tf_groups.setdefault(tf, []).append(s)

        # 抓取各 timeframe 的 K 線
        tf_dataframes: dict[str, pd.DataFrame] = {}
        for tf, group in tf_groups.items():
            max_req = max(s.required_candles for s in group)
            try:
                tf_dataframes[tf] = self._data_fetcher.fetch_ohlcv(
                    symbol, timeframe=tf, limit=max(max_req + 10, 100), cache_ttl=30,
                )
            except Exception:
                logger.exception("%s[現貨] 抓取 %s K 線失敗", _L2, tf)

        if not tf_dataframes:
            logger.warning("%s[現貨] 無可用 K 線資料", _L1)
            return

        # 取最細粒度 timeframe 的 close 作為現價
        finest_tf = min(tf_dataframes, key=lambda t: TF_MINUTES.get(t, 9999))
        finest_df = tf_dataframes[finest_tf]
        current_price = float(finest_df["close"].iloc[-1])
        logger.info("%s[現貨] %s 現價: %.2f USDT", _L1, symbol, current_price)
        self._db.insert_market_snapshot(symbol, current_price, mode=self._settings.spot.mode.value)

        # ── 2. 停損停利 ──
        if self._executor.is_live and self._risk.has_exchange_sl_tp(symbol):
            if self._sync_oco_order(symbol):
                return

        if not self._risk.has_exchange_sl_tp(symbol):
            sl_tp_signal = self._risk.check_stop_loss_take_profit(symbol, current_price)
            if sl_tp_signal == Signal.SELL:
                logger.info("%s[現貨] 觸發停損/停利 → 執行賣出", _L2)
                self._execute_sell(symbol, current_price)
                return

        # ── 3. 收集策略結論（per-call router，thread-safe）──
        router = StrategyRouter()

        for strategy in strategies:
            verdict = None
            try:
                if strategy.data_feed_type == DataFeedType.OHLCV:
                    tf = strategy.timeframe or sc.timeframe
                    df = tf_dataframes.get(tf)
                    if df is None or len(df) < strategy.required_candles:
                        continue
                    verdict = strategy.generate_verdict(df)
                else:
                    verdict = strategy.latest_verdict(symbol)
            except Exception:
                logger.exception("%s[現貨][%s] 策略執行失敗", _L2, strategy.name)
                continue

            if verdict is not None:
                router.collect(verdict)
                self._db.insert_verdict(
                    symbol, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning, cycle_id,
                    timeframe=verdict.timeframe,
                    mode=self._settings.spot.mode.value,
                )
                abbr = strategy.name[:3]
                tf_label = verdict.timeframe or "of"
                sig_str = f"{verdict.signal.value} {verdict.confidence:.0%}"
                logger.info(
                    "%s[現貨][%s|%-3s] %-9s — %s",
                    _L2, abbr, tf_label, sig_str, verdict.reasoning[:80],
                )

        verdicts = router.get_verdicts()
        if not verdicts:
            return

        # ── 4. 預計算風控指標 ──
        risk_metrics = None
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if non_hold:
            primary_signal = non_hold[0].signal
            if primary_signal == Signal.BUY:
                try:
                    balance = self._exchange.get_balance()
                    usdt_balance = balance.get("USDT", 0.0)
                    risk_metrics = self._risk.pre_calculate_metrics(
                        signal=primary_signal,
                        symbol=symbol,
                        price=current_price,
                        balance=usdt_balance,
                        ohlcv=finest_df,
                    )
                except Exception as e:
                    logger.warning("%s[現貨] 預計算風控指標失敗: %s", _L2, e)

        # ── 5. 多時間框架摘要（直接用已抓取的 K 線）──
        from bot.app import build_mtf_summary
        mtf_summary = build_mtf_summary(tf_dataframes, enabled=self._settings.mtf.enabled)

        # ── 6. LLM 決策 ──
        portfolio = self._build_portfolio_state(symbol, current_price)
        decision_result = make_decision_fn(
            verdicts=verdicts,
            symbol=symbol,
            current_price=current_price,
            cycle_id=cycle_id,
            market_type="spot",
            risk_metrics=risk_metrics,
            mtf_summary=mtf_summary,
            portfolio=portfolio,
            mode=self._settings.spot.mode.value,
        )

        final_signal = decision_result.signal
        final_confidence = decision_result.confidence
        horizon = decision_result.horizon

        if final_signal == Signal.HOLD:
            logger.info("%s[現貨] → HOLD（不動作）", _L2)
            return

        min_conf = self._settings.llm.min_confidence
        if final_confidence < min_conf:
            logger.info(
                "%s[現貨] → %s 信心 %.2f 低於門檻 %.2f → 視為 HOLD",
                _L2, final_signal.value, final_confidence, min_conf,
            )
            return

        logger.info(
            "%s[現貨] → %s (信心 %.2f, horizon=%s)",
            _L2, final_signal.value, final_confidence, horizon,
        )

        # ── 7. 風控 + 執行 ──
        if final_signal == Signal.BUY and self._is_in_cooldown(symbol):
            return

        if final_signal == Signal.BUY:
            try:
                balance = self._exchange.get_balance()
                usdt_balance = balance.get("USDT", 0.0)

                if usdt_balance < 1.0 and balance.get("LDUSDT", 0.0) > 0:
                    logger.info(
                        "%s[現貨] USDT 餘額不足 (%.2f)，偵測到 LDUSDT %.2f，嘗試自動贖回...",
                        _L2, usdt_balance, balance["LDUSDT"],
                    )
                    redeemed = self._exchange.redeem_all_usdt_earn()
                    if redeemed > 0:
                        logger.info("%s[現貨] 已贖回 %.4f USDT，重新取得餘額", _L2, redeemed)
                        time.sleep(1)
                        balance = self._exchange.get_balance()
                        usdt_balance = balance.get("USDT", 0.0)
                    else:
                        logger.warning("%s[現貨] 贖回失敗或無可贖回", _L2)
            except Exception as e:
                logger.warning("%s[現貨] 取得餘額失敗，跳過買入: %s", _L2, e)
                return

            llm_size_pct = decision_result.llm_size_pct

            risk_output = self._risk.evaluate(
                final_signal, symbol, current_price, usdt_balance,
                horizon=horizon,
                llm_size_pct=llm_size_pct,
                llm_stop_loss=decision_result.stop_loss,
                llm_take_profit=decision_result.take_profit,
            )
            if not risk_output.approved:
                logger.info("%s[現貨] 風控拒絕: %s", _L2, risk_output.reason)
                return
            if decision_result.llm_override and risk_output.quantity > 0:
                halved = risk_output.quantity / 2
                min_notional = self._exchange.get_min_notional(symbol)
                notional = halved * current_price
                if min_notional > 0 and notional < min_notional:
                    logger.info(
                        "%s[現貨][覆蓋] 縮半後名義金額 %.2f < 最小 %.0f，維持原量 %.6f",
                        _L2, notional, min_notional, risk_output.quantity,
                    )
                else:
                    risk_output.quantity = halved
                    logger.info("%s[現貨][覆蓋] 倉位縮半: %.6f", _L2, risk_output.quantity)
            self._execute_buy(
                symbol, current_price, risk_output, cycle_id,
                entry_horizon=horizon,
                entry_reasoning=decision_result.reasoning,
            )

        elif final_signal == Signal.SELL:
            hold_min = self._get_hold_minutes(symbol)
            min_hold = self._min_hold_for_horizon(symbol)
            if hold_min is not None and hold_min < min_hold:
                logger.info(
                    "%s[現貨] %s 持倉僅 %d 分鐘，最低 %d 分鐘 → 暫不平倉",
                    _L2, symbol, hold_min, min_hold,
                )
                return
            self._execute_sell(symbol, current_price, cycle_id)

    def _execute_buy(
        self, symbol: str, price: float, risk_output, cycle_id: str = "",
        entry_horizon: str = "", entry_reasoning: str = "",
    ) -> None:
        order = self._executor.execute(Signal.BUY, symbol, risk_output)
        if order:
            fill_price = order.get("price", price)

            tp_order_id, sl_order_id = None, None
            oco_info = self._executor.place_sl_tp(
                symbol,
                risk_output.quantity,
                risk_output.take_profit_price,
                risk_output.stop_loss_price,
            )
            if oco_info:
                tp_order_id = oco_info.get("tp_order_id")
                sl_order_id = oco_info.get("sl_order_id")

            self._risk.add_position(
                symbol, risk_output.quantity, fill_price,
                tp_order_id=tp_order_id,
                sl_order_id=sl_order_id,
                stop_loss_price=risk_output.stop_loss_price,
                take_profit_price=risk_output.take_profit_price,
                entry_horizon=entry_horizon,
                entry_reasoning=entry_reasoning,
            )
            self._order_manager.add_order(order)
            _mode = self._settings.spot.mode.value
            self._db.insert_order(order, mode=_mode, cycle_id=cycle_id)
            self._db.upsert_position(symbol, {
                "quantity": risk_output.quantity,
                "entry_price": fill_price,
                "current_price": fill_price,
                "unrealized_pnl": 0,
                "stop_loss": risk_output.stop_loss_price,
                "take_profit": risk_output.take_profit_price,
                "entry_horizon": entry_horizon,
                "entry_reasoning": entry_reasoning,
            }, mode=_mode)
            logger.info(
                "%s[現貨] ✓ BUY %s @ %.2f, qty=%.8f (SL=%.2f, TP=%.2f)",
                _L3, symbol, fill_price, risk_output.quantity,
                risk_output.stop_loss_price, risk_output.take_profit_price,
            )

    def _execute_sell(
        self, symbol: str, price: float, cycle_id: str = "",
    ) -> None:
        tp_id, sl_id = self._risk.get_sl_tp_order_ids(symbol)
        if tp_id or sl_id:
            self._executor.cancel_sl_tp(symbol, tp_id, sl_id)
            logger.info("%s[現貨] 已取消 SL/TP 掛單", _L3)

        risk_output = self._risk.evaluate(Signal.SELL, symbol, price, 0)
        if not risk_output.approved:
            return

        # 小額持倉檢查：名義價值低於交易所最低門檻時直接內部清理
        notional = risk_output.quantity * price
        min_notional = self._exchange.get_min_notional(symbol)
        if min_notional > 0 and notional < min_notional:
            logger.info(
                "%s[現貨] 持倉名義值 $%.4f < 最低 $%.2f，跳過下單直接清理 %s",
                _L3, notional, min_notional, symbol,
            )
            pnl = self._risk.remove_position(symbol, price)
            _mode = self._settings.spot.mode.value
            self._db.delete_position(symbol, mode=_mode)
            logger.info("%s[現貨] ✓ 清理小額持倉 %s, PnL=%.4f USDT", _L3, symbol, pnl)
            self._set_cooldown(symbol)
            return

        order = self._executor.execute(Signal.SELL, symbol, risk_output)
        if order:
            fill_price = order.get("price", price)
            pnl = self._risk.remove_position(symbol, fill_price)
            self._order_manager.add_order(order)
            _mode = self._settings.spot.mode.value
            self._db.insert_order(order, mode=_mode, cycle_id=cycle_id)
            self._db.delete_position(symbol, mode=_mode)
            logger.info("%s[現貨] ✓ SELL %s @ %.2f, PnL=%.2f USDT", _L3, symbol, fill_price, pnl)
            self._set_cooldown(symbol)

    def _set_cooldown(self, symbol: str) -> None:
        """平倉後設定冷卻期，防止同 symbol 短時間內再開倉。"""
        minutes = self._settings.spot.cooldown_minutes
        if minutes > 0:
            until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            self._cooldown_until[symbol] = until
            logger.info("%s[現貨] %s 進入冷卻期 %d 分鐘", _L2, symbol, minutes)

    def _is_in_cooldown(self, symbol: str) -> bool:
        """檢查 symbol 是否在冷卻期內。"""
        until = self._cooldown_until.get(symbol)
        if until is None:
            return False
        if datetime.now(timezone.utc) >= until:
            del self._cooldown_until[symbol]
            return False
        remaining = (until - datetime.now(timezone.utc)).total_seconds() / 60
        logger.info("%s[現貨] %s 冷卻中，剩餘 %.0f 分鐘", _L2, symbol, remaining)
        return True

    # ── 最低持倉時間 ──

    _HORIZON_MIN_HOLD = {"short": 60, "medium": 240, "long": 480}

    def _get_hold_minutes(self, symbol: str) -> int | None:
        """回傳該 symbol 已持倉多少分鐘，無持倉回傳 None。"""
        pos = self._risk.get_position(symbol)
        if pos is None:
            return None
        opened_at = pos.get("opened_at")
        if not opened_at:
            return None
        opened = datetime.fromisoformat(opened_at)
        return int((datetime.now(timezone.utc) - opened).total_seconds() / 60)

    def _min_hold_for_horizon(self, symbol: str) -> int:
        """根據 entry_horizon 回傳最低持倉分鐘數。"""
        pos = self._risk.get_position(symbol)
        if pos is None:
            return 0
        horizon = pos.get("entry_horizon", "short")
        return self._HORIZON_MIN_HOLD.get(horizon, 60)

    def _sync_oco_order(self, symbol: str) -> bool:
        """檢查交易所 OCO 訂單是否已成交。"""
        tp_id, sl_id = self._risk.get_sl_tp_order_ids(symbol)

        for order_id, label in [(tp_id, "停利"), (sl_id, "停損")]:
            if not order_id:
                continue
            try:
                status = self._exchange.get_order_status(order_id, symbol)
                if status["status"] == "closed":
                    fill_price = status.get("price", 0)
                    pnl = self._risk.remove_position(symbol, fill_price)
                    self._order_manager.add_order(status)
                    logger.info(
                        "%s[現貨] 交易所 %s 成交: %s @ %.2f, PnL=%.2f USDT",
                        _L2, label, symbol, fill_price, pnl,
                    )
                    self._set_cooldown(symbol)
                    return True
            except Exception as e:
                logger.debug("查詢 OCO 訂單 %s 失敗: %s", order_id, e)

        return False

    def _build_portfolio_state(
        self, symbol: str, current_price: float,
    ) -> PortfolioState:
        """建構投資組合狀態（供 LLM 參考）。"""
        try:
            balance = self._exchange.get_balance()
            usdt_balance = balance.get("USDT", 0.0) + balance.get("LDUSDT", 0.0)
        except Exception:
            usdt_balance = 0.0

        positions = []
        with self._risk._lock:
            open_pos = dict(self._risk._open_positions)
            daily_pnl = self._risk._daily_pnl
            pos_count = len(self._risk._open_positions)

        for sym, pos_data in open_pos.items():
            entry = pos_data["entry_price"]
            qty = pos_data["quantity"]
            price = current_price if sym == symbol else entry
            pnl = (price - entry) * qty
            pnl_pct = (price - entry) / entry if entry > 0 else 0.0

            # 計算持倉時長
            opened_at = pos_data.get("opened_at")
            hold_str = ""
            if opened_at:
                hold_min = int((datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 60)
                if hold_min >= 60:
                    hold_str = f"{hold_min // 60}h{hold_min % 60}m"
                else:
                    hold_str = f"{hold_min}m"

            positions.append(PositionInfo(
                symbol=sym,
                quantity=qty,
                entry_price=entry,
                current_price=price,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
                entry_horizon=pos_data.get("entry_horizon", ""),
                entry_reasoning=pos_data.get("entry_reasoning", ""),
                holding_duration=hold_str,
            ))

        sc = self._settings.spot
        return PortfolioState(
            available_balance=usdt_balance,
            positions=positions,
            max_positions=sc.max_open_positions,
            current_position_count=pos_count,
            daily_realized_pnl=daily_pnl,
            daily_risk_remaining=usdt_balance * sc.max_daily_loss_pct + daily_pnl,
        )
