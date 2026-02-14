"""合約風控管理器 — 槓桿感知 + ATR 動態 SL/TP + 盈虧比把關。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date

import pandas as pd

from bot.config.settings import FuturesConfig, HorizonRiskConfig
from bot.logging_config import get_logger
from bot.risk.metrics import RiskMetrics
from bot.strategy.signals import Signal

logger = get_logger("risk.futures_manager")


@dataclass
class FuturesRiskOutput:
    """合約風控評估結果。"""
    approved: bool
    quantity: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    leverage: int = 1
    liquidation_price: float = 0.0
    risk_reward_ratio: float = 0.0
    reason: str = ""


class FuturesRiskManager:
    """
    合約風控管理器。

    職責:
    1. ATR 動態停損 / 停利（可 fallback 回固定百分比）
    2. 盈虧比 (R:R) 門檻檢查
    3. 帳戶角度風險評估（槓桿放大後實際帳戶虧損百分比）
    4. 槓桿感知的部位大小計算
    5. 最大持倉數 / 每日虧損限制 / 保證金比率檢查
    6. 清算價計算
    """

    # Binance USDT-M 維持保證金率（簡化，取中等層級）
    MAINTENANCE_MARGIN_RATE = 0.004

    def __init__(
        self, config: FuturesConfig,
        horizon_config: HorizonRiskConfig | None = None,
    ) -> None:
        self.config = config
        self.horizon_config = horizon_config or HorizonRiskConfig()
        self._lock = threading.Lock()
        self._open_positions: dict[str, dict] = {}  # key = "{symbol}:{side}"
        self._reserved_slots: set[str] = set()  # 預留中的 pos_key
        self._daily_pnl: float = 0.0
        self._pnl_date: date = date.today()

    @property
    def open_position_count(self) -> int:
        with self._lock:
            return len(self._open_positions)

    def _pos_key(self, symbol: str, side: str) -> str:
        return f"{symbol}:{side}"

    # ─── Thread-safe slot reservation（並行下單用）───

    def reserve_slot(self, symbol: str, side: str) -> bool:
        """預留一個持倉 slot。並行處理時防止超額開倉。"""
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            if pos_key in self._open_positions or pos_key in self._reserved_slots:
                return False
            total = len(self._open_positions) + len(self._reserved_slots)
            if total >= self.config.max_open_positions:
                return False
            self._reserved_slots.add(pos_key)
            logger.debug("預留 slot: %s (已佔 %d/%d)", pos_key, total + 1, self.config.max_open_positions)
            return True

    def confirm_position(
        self, symbol: str, side: str, quantity: float,
        entry_price: float, leverage: int = 1,
        tp_order_id: str | None = None, sl_order_id: str | None = None,
        stop_loss_price: float = 0.0, take_profit_price: float = 0.0,
    ) -> None:
        """確認預留 slot 並轉為正式持倉。"""
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            self._reserved_slots.discard(pos_key)
            self._open_positions[pos_key] = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "leverage": leverage,
                "tp_order_id": tp_order_id,
                "sl_order_id": sl_order_id,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
            }
        side_label = "多" if side == "long" else "空"
        logger.info(
            "確認%s倉: %s qty=%.8f entry=%.2f leverage=%dx SL=%.2f TP=%.2f",
            side_label, symbol, quantity, entry_price, leverage,
            stop_loss_price, take_profit_price,
        )

    def release_slot(self, symbol: str, side: str) -> None:
        """釋放預留 slot（下單失敗或 LLM 拒絕時呼叫）。"""
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            self._reserved_slots.discard(pos_key)
        logger.debug("釋放 slot: %s", pos_key)

    # ─── Horizon 參數 ───

    def _get_horizon_params(self, horizon: str) -> dict:
        """根據 horizon 取得對應的 SL/TP 倍率和倉位因子。"""
        hc = self.horizon_config
        prefix = horizon if horizon in ("short", "medium", "long") else "medium"
        return {
            "sl_multiplier": getattr(hc, f"{prefix}_sl_multiplier"),
            "tp_multiplier": getattr(hc, f"{prefix}_tp_multiplier"),
            "sl_pct": getattr(hc, f"{prefix}_sl_pct"),
            "tp_pct": getattr(hc, f"{prefix}_tp_pct"),
            "size_factor": getattr(hc, f"{prefix}_size_factor"),
            "min_rr": getattr(hc, f"{prefix}_min_rr"),
        }

    # ─── ATR 計算 ───

    @staticmethod
    def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
        """從 OHLCV DataFrame 計算 ATR。委派至共用工具。"""
        from bot.utils.indicators import compute_atr
        return compute_atr(df, period)

    # ─── 預計算風控指標（LLM 決策前） ───

    def pre_calculate_metrics(
        self,
        signal: Signal,
        symbol: str,
        side: str,
        price: float,
        available_margin: float,
        margin_ratio: float,
        ohlcv: pd.DataFrame | None = None,
        horizon: str = "medium",
    ) -> RiskMetrics | None:
        """預先計算合約風控指標，供 LLM 決策參考。"""
        if signal not in (Signal.BUY, Signal.SHORT):
            return None

        with self._lock:
            self._reset_daily_pnl_if_needed()

            # 基本風控檢查（僅標記）
            reason = ""
            if margin_ratio >= self.config.max_margin_ratio:
                reason = f"保證金比率 {margin_ratio:.1%} >= {self.config.max_margin_ratio:.0%}"
            elif self._daily_pnl < -(available_margin * self.config.max_daily_loss_pct):
                reason = f"已達每日虧損限制 ({self.config.max_daily_loss_pct * 100:.1f}%)"
            elif len(self._open_positions) + len(self._reserved_slots) >= self.config.max_open_positions:
                reason = f"已達最大持倉數 ({self.config.max_open_positions})"
            else:
                pos_key = self._pos_key(symbol, side)
                if pos_key in self._open_positions or pos_key in self._reserved_slots:
                    reason = f"已持有 {symbol} {side}"

        # SL/TP
        stop_loss, take_profit, sl_distance, tp_distance = self._calc_sl_tp(
            side, price, ohlcv, horizon,
        )
        rr_ratio = tp_distance / sl_distance if sl_distance > 0 else 0.0
        hp = self._get_horizon_params(horizon)
        passes_rr = rr_ratio >= hp["min_rr"]
        if not passes_rr and not reason:
            reason = f"R:R {rr_ratio:.2f} < {hp['min_rr']:.1f} (horizon={horizon})"

        # ATR
        from bot.utils.indicators import compute_atr as _compute_atr
        atr_val = 0.0
        if self.config.atr.enabled and ohlcv is not None:
            atr_val = _compute_atr(ohlcv, self.config.atr.period)

        # 清算價
        leverage = self.config.leverage
        if side == "long":
            liq_price = price * (1 - 1 / leverage + self.MAINTENANCE_MARGIN_RATE)
        else:
            liq_price = price * (1 + 1 / leverage - self.MAINTENANCE_MARGIN_RATE)

        # 帳戶風險
        sl_pct = sl_distance / price if price > 0 else 0
        account_risk_pct = sl_pct * leverage * self.config.max_position_pct

        metrics = RiskMetrics(
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            sl_distance=sl_distance,
            tp_distance=tp_distance,
            risk_reward_ratio=rr_ratio,
            atr_value=atr_val,
            atr_used=atr_val > 0 and self.config.atr.enabled,
            leverage=leverage,
            liquidation_price=liq_price,
            account_risk_pct=account_risk_pct,
            passes_min_rr=passes_rr,
            reason=reason,
        )

        # Fibonacci
        try:
            from bot.utils.indicators import compute_fibonacci_levels
            fib = compute_fibonacci_levels(ohlcv)
            if fib:
                metrics.fib_levels = fib
        except Exception:
            logger.debug("Fibonacci 計算失敗", exc_info=True)

        # 支撐壓力位
        try:
            from bot.utils.indicators import compute_support_resistance
            sr = compute_support_resistance(ohlcv)
            metrics.support_levels = sr["support"]
            metrics.resistance_levels = sr["resistance"]
        except Exception:
            logger.debug("支撐壓力位計算失敗", exc_info=True)

        # 布林帶
        try:
            from bot.utils.indicators import compute_bollinger_bands
            bb = compute_bollinger_bands(ohlcv)
            if bb:
                metrics.bb_upper = bb["upper"]
                metrics.bb_mid = bb["mid"]
                metrics.bb_lower = bb["lower"]
                metrics.bb_pct_b = bb["pct_b"]
        except Exception:
            logger.debug("布林帶計算失敗", exc_info=True)

        return metrics

    # ─── 評估入口 ───

    def _validate_llm_prices(
        self, price: float, llm_sl: float, llm_tp: float,
        horizon: str = "medium", is_short: bool = False,
    ) -> tuple[float, float, str]:
        """驗證 LLM 的 SL/TP 價位，回傳 (sl, tp, note)。"""
        hp = self._get_horizon_params(horizon)

        if is_short:
            if llm_sl <= price or llm_tp >= price:
                return 0, 0, "做空方向不正確"
            sl_dist = llm_sl - price
            tp_dist = price - llm_tp
        else:
            if llm_sl >= price or llm_tp <= price:
                return 0, 0, "做多方向不正確"
            sl_dist = price - llm_sl
            tp_dist = llm_tp - price

        sl_pct = sl_dist / price
        if sl_pct < 0.005:
            return 0, 0, f"SL 距離太近 ({sl_pct:.2%} < 0.5%)"
        if sl_pct > 0.15:
            return 0, 0, f"SL 距離太遠 ({sl_pct:.2%} > 15%)"

        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        note = ""
        if rr < hp["min_rr"]:
            adjusted_tp_dist = sl_dist * hp["min_rr"]
            if is_short:
                llm_tp = price - adjusted_tp_dist
            else:
                llm_tp = price + adjusted_tp_dist
            note = f"TP 調整至 R:R>={hp['min_rr']:.1f} ({rr:.2f}→{hp['min_rr']:.1f})"
            logger.info("LLM 價位調整: %s", note)

        return llm_sl, llm_tp, note

    def evaluate(
        self, signal: Signal, symbol: str, price: float,
        available_margin: float, margin_ratio: float = 0.0,
        ohlcv: pd.DataFrame | None = None,
        horizon: str = "medium",
        llm_size_pct: float = 0.0,
        llm_stop_loss: float = 0.0,
        llm_take_profit: float = 0.0,
    ) -> FuturesRiskOutput:
        """評估合約交易訊號。"""
        with self._lock:
            self._reset_daily_pnl_if_needed()

            if signal == Signal.BUY:
                return self._evaluate_open(
                    symbol, "long", price, available_margin, margin_ratio,
                    ohlcv, horizon, llm_size_pct, llm_stop_loss, llm_take_profit,
                )
            elif signal == Signal.SHORT:
                return self._evaluate_open(
                    symbol, "short", price, available_margin, margin_ratio,
                    ohlcv, horizon, llm_size_pct, llm_stop_loss, llm_take_profit,
                )
            elif signal == Signal.SELL:
                return self._evaluate_close(symbol, "long", price)
            elif signal == Signal.COVER:
                return self._evaluate_close(symbol, "short", price)
            else:
                return FuturesRiskOutput(approved=False, reason="HOLD 訊號")

    # ─── 開倉評估 ───

    def _evaluate_open(
        self, symbol: str, side: str, price: float,
        available_margin: float, margin_ratio: float,
        ohlcv: pd.DataFrame | None = None,
        horizon: str = "medium",
        llm_size_pct: float = 0.0,
        llm_stop_loss: float = 0.0,
        llm_take_profit: float = 0.0,
    ) -> FuturesRiskOutput:
        """評估開倉（開多或開空）。"""
        # 注意: 呼叫者 evaluate() 已持有 self._lock

        # ── 1. 基礎風控檢查 ──

        # 保證金比率檢查
        if margin_ratio >= self.config.max_margin_ratio:
            reason = f"保證金比率已達 {margin_ratio:.1%}，超過警戒線 {self.config.max_margin_ratio:.0%}"
            logger.warning(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        # 每日虧損限制
        if self._daily_pnl < -(available_margin * self.config.max_daily_loss_pct):
            reason = f"已達每日虧損限制 ({self.config.max_daily_loss_pct * 100:.1f}%)"
            logger.warning(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        # 最大持倉數（含預留 slot）
        total = len(self._open_positions) + len(self._reserved_slots)
        if total >= self.config.max_open_positions:
            reason = f"已達最大持倉數 ({self.config.max_open_positions})"
            logger.warning(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        # 已持有或已預留同方向持倉
        pos_key = self._pos_key(symbol, side)
        if pos_key in self._open_positions or pos_key in self._reserved_slots:
            reason = f"已持有 {symbol} {side} 倉"
            logger.info(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        # ── 2. 計算 SL/TP（優先用 LLM 價位，fallback ATR / 固定百分比）──
        is_short = side == "short"
        used_llm_prices = False
        if llm_stop_loss > 0 and llm_take_profit > 0:
            validated_sl, validated_tp, note = self._validate_llm_prices(
                price, llm_stop_loss, llm_take_profit, horizon, is_short=is_short,
            )
            if validated_sl > 0 and validated_tp > 0:
                stop_loss, take_profit = validated_sl, validated_tp
                if is_short:
                    sl_distance = stop_loss - price
                    tp_distance = price - take_profit
                else:
                    sl_distance = price - stop_loss
                    tp_distance = take_profit - price
                used_llm_prices = True
                if note:
                    logger.info("LLM 價位: %s", note)
                logger.info(
                    "使用 LLM 價位: SL=%.2f, TP=%.2f (side=%s)",
                    stop_loss, take_profit, side,
                )
            else:
                logger.info("LLM 價位驗證失敗 (%s)，fallback ATR", note)

        if not used_llm_prices:
            stop_loss, take_profit, sl_distance, tp_distance = self._calc_sl_tp(
                side, price, ohlcv, horizon,
            )

        # ── 3. 盈虧比 (R:R) 檢查（根據 horizon）──
        hp = self._get_horizon_params(horizon)
        risk_reward = tp_distance / sl_distance if sl_distance > 0 else 0.0
        if risk_reward < hp["min_rr"]:
            reason = (
                f"盈虧比不足: R:R={risk_reward:.2f} < 最低要求 {hp['min_rr']:.1f} "
                f"(SL距離={sl_distance:.2f}, TP距離={tp_distance:.2f}, horizon={horizon})"
            )
            logger.warning(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        # ── 4. 帳戶角度風險評估（槓桿放大效應）──
        leverage = self.config.leverage
        sl_pct = sl_distance / price
        account_risk_pct = sl_pct * leverage * self.config.max_position_pct
        max_single_trade_risk = self.config.max_daily_loss_pct / 2
        if account_risk_pct > max_single_trade_risk:
            reason = (
                f"單筆帳戶風險過高: {account_risk_pct:.2%} > {max_single_trade_risk:.2%} "
                f"(SL={sl_pct:.2%} × {leverage}x × {self.config.max_position_pct:.1%})"
            )
            logger.warning(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        # ── 5. 停損價不可超過清算價 ──
        if side == "long":
            liq_price = price * (1 - 1 / leverage + self.MAINTENANCE_MARGIN_RATE)
            if stop_loss <= liq_price:
                reason = f"停損價 {stop_loss:.2f} 已超過清算價 {liq_price:.2f}，風險過高"
                logger.warning(reason)
                return FuturesRiskOutput(approved=False, reason=reason)
        else:  # short
            liq_price = price * (1 + 1 / leverage - self.MAINTENANCE_MARGIN_RATE)
            if stop_loss >= liq_price:
                reason = f"停損價 {stop_loss:.2f} 已超過清算價 {liq_price:.2f}，風險過高"
                logger.warning(reason)
                return FuturesRiskOutput(approved=False, reason=reason)

        # ── 6. 槓桿感知部位計算（根據 horizon 調整）──
        notional = available_margin * self.config.max_position_pct * leverage * hp["size_factor"]
        quantity = notional / price

        # LLM 建議的倉位佔比（取較保守者）
        if llm_size_pct > 0:
            llm_notional = available_margin * llm_size_pct * leverage
            llm_quantity = llm_notional / price
            quantity = min(quantity, llm_quantity)

        if quantity <= 0:
            return FuturesRiskOutput(approved=False, reason="計算數量為 0")

        side_label = "開多" if side == "long" else "開空"
        logger.info(
            "風控通過 %s %s: 數量=%.8f, 槓桿=%dx, SL=%.2f, TP=%.2f, R:R=%.2f, 清算=%.2f, 帳戶風險=%.2f%% [horizon=%s]",
            side_label, symbol, quantity, leverage,
            stop_loss, take_profit, risk_reward, liq_price,
            account_risk_pct * 100, horizon,
        )

        return FuturesRiskOutput(
            approved=True,
            quantity=quantity,
            stop_loss_price=stop_loss,
            take_profit_price=take_profit,
            leverage=leverage,
            liquidation_price=liq_price,
            risk_reward_ratio=risk_reward,
        )

    def _calc_sl_tp(
        self, side: str, price: float,
        ohlcv: pd.DataFrame | None,
        horizon: str = "medium",
    ) -> tuple[float, float, float, float]:
        """計算 SL/TP 價位和距離。

        優先使用 ATR 動態計算；若 ATR 不可用則 fallback 到固定百分比。
        根據 horizon 選擇對應倍率。

        Returns:
            (stop_loss, take_profit, sl_distance, tp_distance)
        """
        hp = self._get_horizon_params(horizon)
        atr = 0.0
        if self.config.atr.enabled and ohlcv is not None:
            atr = self.compute_atr(ohlcv, self.config.atr.period)

        if atr > 0:
            sl_distance = atr * hp["sl_multiplier"]
            tp_distance = atr * hp["tp_multiplier"]
            logger.debug(
                "ATR=%.2f, SL距離=%.2f (×%.1f), TP距離=%.2f (×%.1f) [horizon=%s]",
                atr, sl_distance, hp["sl_multiplier"],
                tp_distance, hp["tp_multiplier"], horizon,
            )
        else:
            sl_distance = price * hp["sl_pct"]
            tp_distance = price * hp["tp_pct"]
            if self.config.atr.enabled:
                logger.debug("ATR 數據不足，fallback 到固定百分比 SL/TP")

        if side == "long":
            stop_loss = price - sl_distance
            take_profit = price + tp_distance
        else:  # short
            stop_loss = price + sl_distance
            take_profit = price - tp_distance

        return stop_loss, take_profit, sl_distance, tp_distance

    # ─── 平倉評估 ───

    def _evaluate_close(
        self, symbol: str, side: str, price: float,
    ) -> FuturesRiskOutput:
        """評估平倉（平多或平空）。"""
        pos_key = self._pos_key(symbol, side)
        if pos_key not in self._open_positions:
            reason = f"未持有 {symbol} {side} 倉，無法平倉"
            logger.info(reason)
            return FuturesRiskOutput(approved=False, reason=reason)

        position = self._open_positions[pos_key]
        quantity = position["quantity"]

        side_label = "平多" if side == "long" else "平空"
        logger.info("風控通過 %s %s: 數量=%.8f", side_label, symbol, quantity)
        return FuturesRiskOutput(
            approved=True,
            quantity=quantity,
            leverage=position.get("leverage", self.config.leverage),
        )

    # ─── 持倉管理 ───

    def add_position(
        self, symbol: str, side: str, quantity: float,
        entry_price: float, leverage: int = 1,
        tp_order_id: str | None = None,
        sl_order_id: str | None = None,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0,
    ) -> None:
        """記錄新持倉（含 SL/TP 價位）。

        若已透過 reserve_slot → confirm_position 流程，建議使用
        confirm_position 代替此方法。此方法保留用於向後相容。
        """
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            self._reserved_slots.discard(pos_key)
            self._open_positions[pos_key] = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": entry_price,
                "leverage": leverage,
                "tp_order_id": tp_order_id,
                "sl_order_id": sl_order_id,
                "stop_loss_price": stop_loss_price,
                "take_profit_price": take_profit_price,
            }
        side_label = "多" if side == "long" else "空"
        logger.info(
            "新增%s倉: %s qty=%.8f entry=%.2f leverage=%dx SL=%.2f TP=%.2f",
            side_label, symbol, quantity, entry_price, leverage,
            stop_loss_price, take_profit_price,
        )

    def remove_position(self, symbol: str, side: str, exit_price: float) -> float:
        """移除持倉並計算損益。"""
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            if pos_key not in self._open_positions:
                return 0.0

            position = self._open_positions.pop(pos_key)
            entry = position["entry_price"]
            qty = position["quantity"]

            if side == "long":
                pnl = (exit_price - entry) * qty
            else:  # short
                pnl = (entry - exit_price) * qty

            self._daily_pnl += pnl

        side_label = "多" if side == "long" else "空"
        logger.info(
            "移除%s倉: %s PnL=%.2f USDT (入場=%.2f, 出場=%.2f)",
            side_label, symbol, pnl, entry, exit_price,
        )
        return pnl

    def check_stop_loss_take_profit(
        self, symbol: str, side: str, current_price: float,
    ) -> Signal:
        """檢查是否觸發停損或停利（paper 模式用輪詢價格判斷）。

        優先使用開倉時儲存的 SL/TP 價位（保持 horizon 一致性），
        若無儲存值則 fallback 到固定百分比（向後相容）。
        """
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            if pos_key not in self._open_positions:
                return Signal.HOLD

            position = self._open_positions[pos_key]
            entry = position["entry_price"]

            # 優先使用開倉時儲存的 SL/TP 價位
            stop_loss = position.get("stop_loss_price", 0.0)
            take_profit = position.get("take_profit_price", 0.0)

        # Fallback: 若無儲存值（舊持倉或恢復時），用固定百分比（鎖外計算）
        if stop_loss <= 0 or take_profit <= 0:
            if side == "long":
                stop_loss = entry * (1 - self.config.stop_loss_pct)
                take_profit = entry * (1 + self.config.take_profit_pct)
            else:
                stop_loss = entry * (1 + self.config.stop_loss_pct)
                take_profit = entry * (1 - self.config.take_profit_pct)

        if side == "long":
            if current_price <= stop_loss:
                logger.warning(
                    "觸發多倉停損: %s 現價=%.2f <= 停損=%.2f",
                    symbol, current_price, stop_loss,
                )
                return Signal.SELL
            if current_price >= take_profit:
                logger.info(
                    "觸發多倉停利: %s 現價=%.2f >= 停利=%.2f",
                    symbol, current_price, take_profit,
                )
                return Signal.SELL
        else:  # short
            if current_price >= stop_loss:
                logger.warning(
                    "觸發空倉停損: %s 現價=%.2f >= 停損=%.2f",
                    symbol, current_price, stop_loss,
                )
                return Signal.COVER
            if current_price <= take_profit:
                logger.info(
                    "觸發空倉停利: %s 現價=%.2f <= 停利=%.2f",
                    symbol, current_price, take_profit,
                )
                return Signal.COVER

        return Signal.HOLD

    def get_sl_tp_order_ids(self, symbol: str, side: str) -> tuple[str | None, str | None]:
        """取得持倉的 SL/TP 掛單 ID。"""
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            pos = self._open_positions.get(pos_key)
            if not pos:
                return None, None
            return pos.get("tp_order_id"), pos.get("sl_order_id")

    def has_exchange_sl_tp(self, symbol: str, side: str) -> bool:
        """該持倉是否有交易所掛單中的 SL/TP。"""
        tp_id, sl_id = self.get_sl_tp_order_ids(symbol, side)
        return bool(tp_id or sl_id)

    def get_position(self, symbol: str, side: str) -> dict | None:
        """取得指定持倉。"""
        with self._lock:
            pos = self._open_positions.get(self._pos_key(symbol, side))
            return dict(pos) if pos else None

    def get_all_positions(self) -> dict[str, dict]:
        """取得所有持倉（回傳副本，thread-safe）。"""
        with self._lock:
            return {k: dict(v) for k, v in self._open_positions.items()}

    def force_remove_position(self, symbol: str, side: str) -> None:
        """強制移除幻影持倉（不記錄 PnL，用於對齊）。"""
        pos_key = self._pos_key(symbol, side)
        with self._lock:
            removed = self._open_positions.pop(pos_key, None)
        if removed:
            side_label = "多" if side == "long" else "空"
            logger.warning(
                "強制移除幻影%s倉: %s (entry=%.2f, qty=%.8f)",
                side_label, symbol, removed["entry_price"], removed["quantity"],
            )

    def _reset_daily_pnl_if_needed(self) -> None:
        """重置每日 PnL（呼叫者需持有 self._lock）。"""
        today = date.today()
        if today != self._pnl_date:
            self._daily_pnl = 0.0
            self._pnl_date = today
