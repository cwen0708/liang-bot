"""合約交易處理模組 — 從 app.py 拆分而來。

負責：
- process_symbol() — 單一合約交易對的完整處理流程
- _translate_signal() — BUY/SELL → 合約訊號轉換
- _execute_open() / _execute_close() — 開倉 / 平倉
- _sync_sl_tp() — 檢查交易所 SL/TP 掛單
- record_margin() — 保證金帳戶快照
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from bot.config.constants import DataFeedType, TF_MINUTES
from bot.logging_config import get_logger
from bot.llm.schemas import PortfolioState, PositionInfo
from bot.strategy.signals import Signal, StrategyVerdict

if TYPE_CHECKING:
    import pandas as pd
    from bot.config.settings import Settings
    from bot.data.fetcher import DataFetcher
    from bot.db.supabase_client import SupabaseWriter
    from bot.exchange.futures_client import FuturesBinanceClient
    from bot.execution.futures_executor import FuturesOrderExecutor
    from bot.llm.decision_engine import LLMDecisionEngine
    from bot.risk.futures_manager import FuturesRiskManager
    from bot.strategy.base import Strategy
    from bot.strategy.router import StrategyRouter

logger = get_logger("futures_handler")

_L1 = "  "
_L2 = "    "
_L3 = "      "


class FuturesHandler:
    """合約交易處理器。"""

    def __init__(
        self,
        settings: Settings,
        futures_exchange: FuturesBinanceClient,
        futures_data_fetcher: DataFetcher,
        futures_risk: FuturesRiskManager,
        futures_executor: FuturesOrderExecutor,
        db: SupabaseWriter,
        llm_engine: LLMDecisionEngine,
        router: StrategyRouter,
    ) -> None:
        self._settings = settings
        self._exchange = futures_exchange
        self._data_fetcher = futures_data_fetcher
        self._risk = futures_risk
        self._executor = futures_executor
        self._db = db
        self._llm_engine = llm_engine
        self._router = router
        self._last_strategy_slot: dict[str, int] = {}

    def process_symbol(
        self,
        symbol: str,
        cycle_id: str,
        cycle: int,
        strategies: list[Strategy],
        *,
        make_decision_fn,
    ) -> None:
        """處理單一合約交易對。

        Args:
            make_decision_fn: 共用 LLM 決策函數 (from app.py)
        """
        fc = self._settings.futures

        # 確保槓桿和保證金模式已設定
        self._exchange.ensure_leverage_and_margin(symbol)

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

        last = self._last_strategy_slot.get(symbol, -1)
        if slot == last:
            return
        self._last_strategy_slot[symbol] = slot

        # 按 timeframe 分組
        tf_groups: dict[str, list] = {}
        for s in ohlcv_strategies:
            tf = s.timeframe or fc.timeframe
            tf_groups.setdefault(tf, []).append(s)

        # 抓取各 timeframe 的 K 線（改用 DataFetcher，有 TTL 快取）
        tf_dataframes: dict[str, "pd.DataFrame"] = {}
        for tf, group in tf_groups.items():
            max_req = max(s.required_candles for s in group)
            try:
                tf_dataframes[tf] = self._data_fetcher.fetch_ohlcv(
                    symbol, timeframe=tf, limit=max(max_req + 10, 100), cache_ttl=30,
                )
            except Exception:
                logger.exception("%s[合約] 抓取 %s K 線失敗", _L2, tf)

        if not tf_dataframes:
            logger.warning("%s[合約] %s 無可用 K 線資料", _L1, symbol)
            return

        # 取最細粒度 timeframe 的 close 作為現價
        finest_tf = min(tf_dataframes, key=lambda t: TF_MINUTES.get(t, 9999))
        finest_df = tf_dataframes[finest_tf]
        current_price = float(finest_df["close"].iloc[-1])
        logger.info("%s[合約] %s 現價: %.2f USDT", _L1, symbol, current_price)

        # ── 2. 停損停利（多倉 + 空倉都要檢查）──
        for side in ("long", "short"):
            pos = self._risk.get_position(symbol, side)
            if not pos:
                continue

            if self._executor.is_live and self._risk.has_exchange_sl_tp(symbol, side):
                if self._sync_sl_tp(symbol, side):
                    continue

            if not self._risk.has_exchange_sl_tp(symbol, side):
                sl_tp_signal = self._risk.check_stop_loss_take_profit(
                    symbol, side, current_price,
                )
                if sl_tp_signal in (Signal.SELL, Signal.COVER):
                    close_side = "long" if sl_tp_signal == Signal.SELL else "short"
                    logger.info("%s[合約] %s %s倉觸發停損/停利", _L2, symbol, close_side)
                    self._execute_close(symbol, close_side, current_price, cycle_id)

        # ── 3. 收集策略結論 ──
        self._router.clear()

        for strategy in strategies:
            if strategy.data_feed_type != DataFeedType.OHLCV:
                continue

            try:
                tf = strategy.timeframe or fc.timeframe
                df = tf_dataframes.get(tf)
                if df is None or len(df) < strategy.required_candles:
                    continue
                verdict = strategy.generate_verdict(df)
            except Exception:
                logger.exception("%s[合約][%s] 策略執行失敗", _L2, strategy.name)
                continue

            if verdict is not None:
                self._router.collect(verdict)
                self._db.insert_verdict(
                    symbol, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning, cycle_id,
                    market_type="futures",
                    timeframe=verdict.timeframe,
                )
                abbr = strategy.name[:3]
                tf_label = verdict.timeframe or "of"
                sig_str = f"{verdict.signal.value} {verdict.confidence:.0%}"
                logger.info(
                    "%s[合約][%s|%-3s] %-9s — %s",
                    _L2, abbr, tf_label, sig_str, verdict.reasoning[:80],
                )

        verdicts = self._router.get_verdicts()
        if not verdicts:
            return

        # ── 4. 預計算合約風控指標 ──
        risk_metrics = None
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if non_hold:
            primary_signal = non_hold[0].signal
            if primary_signal in (Signal.BUY, Signal.SHORT):
                side = "long" if primary_signal == Signal.BUY else "short"
                try:
                    balance = self._exchange.get_futures_balance()
                    available = balance["available_balance"]
                    margin_ratio = self._exchange.get_margin_ratio()
                    risk_metrics = self._risk.pre_calculate_metrics(
                        signal=primary_signal,
                        symbol=symbol,
                        side=side,
                        price=current_price,
                        available_margin=available,
                        margin_ratio=margin_ratio,
                        ohlcv=finest_df,
                    )
                except Exception as e:
                    logger.warning("%s[合約] 預計算風控指標失敗: %s", _L2, e)

        # ── 5. 多時間框架摘要（直接用已抓取的 K 線）──
        from bot.app import build_mtf_summary
        mtf_summary = build_mtf_summary(tf_dataframes, enabled=self._settings.mtf.enabled)

        # ── 6. LLM 審查 ──
        portfolio = self._build_portfolio_state(symbol, current_price)
        decision_result = make_decision_fn(
            verdicts=verdicts,
            symbol=symbol,
            current_price=current_price,
            cycle_id=cycle_id,
            market_type="futures",
            risk_metrics=risk_metrics,
            mtf_summary=mtf_summary,
            portfolio=portfolio,
        )

        final_signal = decision_result.signal
        final_confidence = decision_result.confidence
        horizon = decision_result.horizon

        if final_signal == Signal.HOLD:
            logger.info("%s[合約] → HOLD（不動作）", _L2)
            return

        min_conf = fc.min_confidence
        if final_confidence < min_conf:
            logger.info(
                "%s[合約] %s 信心 %.2f 低於門檻 %.2f → HOLD",
                _L2, final_signal.value, final_confidence, min_conf,
            )
            return

        # 訊號轉換
        final_signal = self._translate_signal(final_signal, symbol)
        if final_signal == Signal.HOLD:
            return

        logger.info("%s[合約] → %s (信心 %.2f, horizon=%s)", _L2, final_signal.value, final_confidence, horizon)

        # ── 7. 風控 + 執行 ──
        if final_signal in (Signal.BUY, Signal.SHORT):
            self._execute_open(
                symbol, final_signal, current_price, cycle_id,
                decision=decision_result, ohlcv=df,
            )
        elif final_signal in (Signal.SELL, Signal.COVER):
            side = "long" if final_signal == Signal.SELL else "short"
            self._execute_close(symbol, side, current_price, cycle_id)

    def _translate_signal(self, signal: Signal, symbol: str) -> Signal:
        """將策略/LLM 的 BUY/SELL 轉換為合約訊號。"""
        has_long = self._risk.get_position(symbol, "long") is not None
        has_short = self._risk.get_position(symbol, "short") is not None

        if signal == Signal.SELL:
            return Signal.SELL if has_long else Signal.SHORT
        elif signal == Signal.BUY:
            return Signal.COVER if has_short else Signal.BUY
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

    def _execute_open(
        self,
        symbol: str,
        signal: Signal,
        price: float,
        cycle_id: str,
        *,
        decision,
        ohlcv: pd.DataFrame | None = None,
    ) -> None:
        """執行合約開倉（開多或開空）。"""
        fc = self._settings.futures
        side = "long" if signal == Signal.BUY else "short"

        try:
            balance = self._exchange.get_futures_balance()
            available = balance["available_balance"]
            margin_ratio = self._exchange.get_margin_ratio()
        except Exception as e:
            logger.warning("%s[合約] 取得保證金失敗: %s", _L2, e)
            return

        llm_size_pct = decision.llm_size_pct

        risk_output = self._risk.evaluate(
            signal, symbol, price, available, margin_ratio, ohlcv=ohlcv,
            horizon=decision.horizon, llm_size_pct=llm_size_pct,
        )
        if not risk_output.approved:
            logger.info("%s[合約] 風控拒絕: %s", _L2, risk_output.reason)
            return

        if decision.llm_override and risk_output.quantity > 0:
            risk_output.quantity /= 2
            logger.info("%s[合約][覆蓋] 倉位縮半: %.6f", _L2, risk_output.quantity)

        order = self._executor.execute(signal, symbol, risk_output)
        if not order:
            return

        fill_price = order.get("price", price)
        leverage = fc.leverage

        tp_order_id, sl_order_id = None, None
        oco_info = self._executor.place_sl_tp(
            symbol, risk_output.quantity, side,
            risk_output.take_profit_price, risk_output.stop_loss_price,
        )
        if oco_info:
            tp_order_id = oco_info.get("tp_order_id")
            sl_order_id = oco_info.get("sl_order_id")

        self._risk.add_position(
            symbol, side, risk_output.quantity, fill_price, leverage,
            tp_order_id=tp_order_id, sl_order_id=sl_order_id,
            stop_loss_price=risk_output.stop_loss_price,
            take_profit_price=risk_output.take_profit_price,
        )

        _mode = fc.mode.value
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
            "margin_type": fc.margin_type,
        }, mode=_mode, market_type="futures")

        logger.info(
            "%s[合約] %s %s @ %.2f, qty=%.8f, %dx (SL=%.2f, TP=%.2f, 清算=%.2f)",
            _L3, side_label, symbol, fill_price, risk_output.quantity,
            leverage, risk_output.stop_loss_price, risk_output.take_profit_price,
            risk_output.liquidation_price,
        )

    def _execute_close(
        self, symbol: str, side: str, price: float, cycle_id: str,
    ) -> None:
        """執行合約平倉（平多或平空）。"""
        fc = self._settings.futures
        close_signal = Signal.SELL if side == "long" else Signal.COVER

        tp_id, sl_id = self._risk.get_sl_tp_order_ids(symbol, side)
        if tp_id or sl_id:
            self._executor.cancel_sl_tp(symbol, tp_id, sl_id)

        risk_output = self._risk.evaluate(close_signal, symbol, price, 0)
        if not risk_output.approved:
            return

        order = self._executor.execute(close_signal, symbol, risk_output)
        if not order:
            return

        fill_price = order.get("price", price)
        pnl = self._risk.remove_position(symbol, side, fill_price)

        _mode = fc.mode.value
        side_label = "平多" if side == "long" else "平空"
        self._db.insert_order(
            order, mode=_mode, cycle_id=cycle_id,
            market_type="futures", position_side=side,
            leverage=fc.leverage, reduce_only=True,
        )
        self._db.delete_position(symbol, mode=_mode, market_type="futures", side=side)

        logger.info(
            "%s[合約] %s %s @ %.2f, PnL=%.2f USDT",
            _L3, side_label, symbol, fill_price, pnl,
        )

    def _sync_sl_tp(self, symbol: str, side: str) -> bool:
        """檢查合約 SL/TP 掛單是否已成交。"""
        tp_id, sl_id = self._risk.get_sl_tp_order_ids(symbol, side)
        for order_id, label in [(tp_id, "停利"), (sl_id, "停損")]:
            if not order_id:
                continue
            try:
                status = self._exchange.get_order_status(order_id, symbol)
                if status["status"] == "closed":
                    fill_price = status.get("price", 0)
                    pnl = self._risk.remove_position(symbol, side, fill_price)
                    _mode = self._settings.futures.mode.value
                    self._db.delete_position(symbol, mode=_mode, market_type="futures", side=side)
                    logger.info(
                        "%s[合約] 交易所 %s 成交: %s %s @ %.2f, PnL=%.2f USDT",
                        _L2, label, symbol, side, fill_price, pnl,
                    )
                    return True
            except Exception as e:
                logger.debug("[合約] 查詢訂單 %s 失敗: %s", order_id, e)
        return False

    def record_margin(self) -> None:
        """記錄合約保證金帳戶快照。"""
        balance = self._exchange.get_futures_balance()
        margin_ratio = self._exchange.get_margin_ratio()
        self._db.insert_futures_margin(
            wallet_balance=balance["total_wallet_balance"],
            available_balance=balance["available_balance"],
            unrealized_pnl=balance["total_unrealized_pnl"],
            margin_balance=balance["total_margin_balance"],
            margin_ratio=margin_ratio,
        )

    def _build_portfolio_state(
        self, symbol: str, current_price: float,
    ) -> PortfolioState:
        """建構合約投資組合狀態（供 LLM 參考）。"""
        try:
            balance = self._exchange.get_futures_balance()
            available = balance["available_balance"]
        except Exception:
            available = 0.0

        positions = []
        for key, pos_data in self._risk._open_positions.items():
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

        fc = self._settings.futures
        return PortfolioState(
            available_balance=available,
            positions=positions,
            max_positions=fc.max_open_positions,
            current_position_count=self._risk.open_position_count,
            daily_realized_pnl=self._risk._daily_pnl,
            daily_risk_remaining=available * fc.max_daily_loss_pct + self._risk._daily_pnl,
        )
