"""持倉對齊模組 — 同步交易所、RiskManager、Supabase 三方持倉狀態。

交易所為唯一事實來源（ground truth）。
- 幻影持倉（RM/Supabase 有但交易所沒有）→ 自動移除
- 孤兒持倉（交易所有但 RM/Supabase 沒有）→ 合約自動收編，現貨跳過
- 數量不符 → 以交易所數據修正
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bot.config.constants import TradingMode
from bot.logging_config import get_logger

if TYPE_CHECKING:
    from bot.config.settings import Settings
    from bot.db.supabase_client import SupabaseWriter
    from bot.exchange.binance_native_client import BinanceClient
    from bot.exchange.futures_native_client import FuturesBinanceClient
    from bot.risk.futures_manager import FuturesRiskManager
    from bot.risk.manager import RiskManager

logger = get_logger("reconciliation")

_PREFIX = "[對齊]"


class PositionReconciler:
    """持倉對齊器。"""

    def __init__(
        self,
        spot_exchange: BinanceClient | None,
        futures_exchange: FuturesBinanceClient | None,
        spot_risk: RiskManager | None,
        futures_risk: FuturesRiskManager | None,
        db: SupabaseWriter,
        settings: Settings,
    ) -> None:
        self._spot_exchange = spot_exchange
        self._futures_exchange = futures_exchange
        self._spot_risk = spot_risk
        self._futures_risk = futures_risk
        self._db = db
        self._settings = settings

    def reconcile_all(self, label: str = "啟動") -> None:
        """執行所有對齊（現貨 + 合約）。"""
        logger.info("%s 開始持倉對齊（%s）...", _PREFIX, label)
        self.reconcile_spot()
        self.reconcile_futures()

    # ─── 合約對齊 ───

    def _calc_fallback_sl_tp(
        self, side: str, entry_price: float,
    ) -> tuple[float, float]:
        """用固定百分比計算 SL/TP（收編時無 OHLCV 可算 ATR）。"""
        fc = self._settings.futures
        sl_pct = fc.stop_loss_pct
        tp_pct = fc.take_profit_pct
        if side == "long":
            return entry_price * (1 - sl_pct), entry_price * (1 + tp_pct)
        else:
            return entry_price * (1 + sl_pct), entry_price * (1 - tp_pct)

    def reconcile_futures(self) -> None:
        """合約持倉對齊：交易所 vs RiskManager vs Supabase。"""
        if not self._futures_exchange or not self._futures_risk:
            return

        fc = self._settings.futures
        # Paper 模式且非 testnet → 沒有真實交易所狀態可比對
        if fc.mode == TradingMode.PAPER and not self._settings.exchange.testnet:
            return

        try:
            exchange_positions = self._futures_exchange.get_positions()
        except Exception as e:
            logger.warning("%s 合約持倉查詢失敗，跳過對齊: %s", _PREFIX, e)
            return

        # 建立交易所持倉 lookup: (symbol, side) → data
        exchange_map: dict[tuple[str, str], dict] = {}
        for pos in exchange_positions:
            key = (pos["symbol"], pos["side"])
            exchange_map[key] = pos

        # 取得 RM 持倉
        rm_positions = self._futures_risk.get_all_positions()
        mode = fc.mode.value

        # 建立 RM lookup: (symbol, side) → data
        rm_map: dict[tuple[str, str], dict] = {}
        for _key, pos_data in rm_positions.items():
            rm_key = (pos_data["symbol"], pos_data["side"])
            rm_map[rm_key] = pos_data

        changes = 0

        # 1) 幻影持倉：RM 有但交易所沒有
        for (symbol, side), rm_data in rm_map.items():
            if (symbol, side) not in exchange_map:
                side_label = "多" if side == "long" else "空"
                logger.warning(
                    "%s 幻影合約%s倉移除: %s (RM qty=%.8f, 交易所無持倉)",
                    _PREFIX, side_label, symbol, rm_data["quantity"],
                )
                self._futures_risk.force_remove_position(symbol, side)
                self._db.delete_position(
                    symbol, mode=mode, market_type="futures", side=side,
                )
                changes += 1

        # 2) 孤兒持倉：交易所有但 RM 沒有
        for (symbol, side), ex_data in exchange_map.items():
            if (symbol, side) not in rm_map:
                # 只收編在設定交易對清單中的幣種
                if symbol not in fc.pairs:
                    logger.info(
                        "%s 交易所合約%s倉 %s 不在交易對清單中，跳過收編",
                        _PREFIX, side, symbol,
                    )
                    continue

                # Testnet 可能回傳 entry_price=0，用 mark_price 替代
                entry = ex_data["entry_price"] or ex_data.get("mark_price", 0.0)
                # 優先使用 Bot 設定的槓桿（testnet 可能回傳預設 1x）
                leverage = max(ex_data["leverage"], fc.leverage)
                sl, tp = (0.0, 0.0)
                if entry > 0:
                    sl, tp = self._calc_fallback_sl_tp(side, entry)
                logger.warning(
                    "%s 發現交易所孤兒合約%s倉: %s (qty=%.8f, entry=%.2f, %dx, SL=%.2f, TP=%.2f) → 已收編",
                    _PREFIX, side, symbol,
                    ex_data["contracts"], entry,
                    leverage, sl, tp,
                )
                self._futures_risk.add_position(
                    symbol, side, ex_data["contracts"],
                    entry, leverage,
                    stop_loss_price=sl, take_profit_price=tp,
                )
                self._db.upsert_position(symbol, {
                    "side": side,
                    "leverage": leverage,
                    "quantity": ex_data["contracts"],
                    "entry_price": entry,
                    "current_price": ex_data["mark_price"],
                    "unrealized_pnl": ex_data["unrealized_pnl"],
                    "liquidation_price": ex_data.get("liquidation_price"),
                    "margin_type": ex_data.get("margin_type"),
                    "stop_loss": sl,
                    "take_profit": tp,
                }, mode=mode, market_type="futures")
                changes += 1

        # 3) 數量不符：兩邊都有但 qty 差異 >1%
        for (symbol, side) in rm_map:
            if (symbol, side) not in exchange_map:
                continue
            ex_data = exchange_map[(symbol, side)]
            rm_data = rm_map[(symbol, side)]
            ex_qty = ex_data["contracts"]
            rm_qty = rm_data["quantity"]

            if ex_qty > 0 and abs(ex_qty - rm_qty) / ex_qty > 0.01:
                # 優先使用交易所 entry，若為 0 則保留 RM 的值
                entry = ex_data["entry_price"] or rm_data.get("entry_price", 0.0)
                leverage = max(ex_data["leverage"], fc.leverage)
                # 保留既有 SL/TP；若無值則用固定百分比計算
                existing_sl = rm_data.get("stop_loss_price", 0.0)
                existing_tp = rm_data.get("take_profit_price", 0.0)
                if (existing_sl <= 0 or existing_tp <= 0) and entry > 0:
                    existing_sl, existing_tp = self._calc_fallback_sl_tp(side, entry)
                logger.warning(
                    "%s 合約持倉數量不一致: %s %s (RM=%.8f, 交易所=%.8f) → 已修正",
                    _PREFIX, symbol, side, rm_qty, ex_qty,
                )
                self._futures_risk.force_remove_position(symbol, side)
                self._futures_risk.add_position(
                    symbol, side, ex_qty,
                    entry, leverage,
                    stop_loss_price=existing_sl, take_profit_price=existing_tp,
                )
                self._db.upsert_position(symbol, {
                    "side": side,
                    "leverage": leverage,
                    "quantity": ex_qty,
                    "entry_price": entry,
                    "current_price": ex_data["mark_price"],
                    "unrealized_pnl": ex_data["unrealized_pnl"],
                    "liquidation_price": ex_data.get("liquidation_price"),
                    "margin_type": ex_data.get("margin_type"),
                    "stop_loss": existing_sl,
                    "take_profit": existing_tp,
                }, mode=mode, market_type="futures")
                changes += 1

        if changes == 0:
            logger.info("%s 合約持倉已完全同步，無差異", _PREFIX)

    # ─── 現貨對齊 ───

    def reconcile_spot(self) -> None:
        """現貨持倉近似對齊：交易所餘額 vs RiskManager vs Supabase。

        注意：現貨只有餘額概念，無法精確判斷「持倉」。
        僅移除幻影持倉，不自動收編孤兒餘額。
        """
        if not self._spot_exchange or not self._spot_risk:
            return

        sc = self._settings.spot
        # Paper 模式且非 testnet → 無真實交易所可比對
        if sc.mode == TradingMode.PAPER and not self._settings.exchange.testnet:
            return

        try:
            balances = self._spot_exchange.get_balance()
        except Exception as e:
            logger.warning("%s 現貨餘額查詢失敗，跳過對齊: %s", _PREFIX, e)
            return

        rm_positions = self._spot_risk.get_all_positions()
        mode = sc.mode.value
        changes = 0

        for symbol, rm_data in rm_positions.items():
            # "BTC/USDT" → "BTC"
            base_asset = symbol.split("/")[0]
            exchange_qty = balances.get(base_asset, 0.0)
            rm_qty = rm_data["quantity"]

            if exchange_qty < rm_qty * 0.01:
                # 交易所幾乎沒有該資產 → 幻影持倉
                logger.warning(
                    "%s 幻影現貨持倉移除: %s (RM qty=%.8f, 交易所 %s=%.8f)",
                    _PREFIX, symbol, rm_qty, base_asset, exchange_qty,
                )
                self._spot_risk.force_remove_position(symbol)
                self._db.delete_position(
                    symbol, mode=mode, market_type="spot",
                )
                changes += 1
            elif exchange_qty < rm_qty * 0.95:
                # 數量明顯減少（可能手動賣了一部分）
                logger.warning(
                    "%s 現貨持倉數量不一致: %s (RM=%.8f, 交易所=%.8f) → 已修正",
                    _PREFIX, symbol, rm_qty, exchange_qty,
                )
                entry_price = rm_data["entry_price"]
                self._spot_risk.force_remove_position(symbol)
                self._spot_risk.add_position(symbol, exchange_qty, entry_price)
                self._db.upsert_position(symbol, {
                    "quantity": exchange_qty,
                    "entry_price": entry_price,
                    "current_price": entry_price,
                }, mode=mode, market_type="spot")
                changes += 1

        if changes == 0:
            logger.info("%s 現貨持倉已完全同步，無差異", _PREFIX)
