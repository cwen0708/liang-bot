"""Supabase 讀寫層 — Bot 與前端之間的唯一資料中介。

Bot 使用 service_role key（繞過 RLS）進行讀寫。
前端使用 anon key，受 RLS 限制。
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("supabase_writer")


class SupabaseWriter:
    """Bot 寫入 Supabase 的單一入口。"""

    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")

        if not url or not key:
            logger.warning("SUPABASE_URL 或 SUPABASE_SERVICE_KEY 未設定，Supabase 寫入停用")
            self._client = None
            self._enabled = False
            return

        from supabase import create_client

        self._client = create_client(url, key)
        self._enabled = True
        self._last_config_version: int = -1

        # 日誌批次緩衝
        self._log_buffer: list[dict] = []
        self._log_lock = threading.Lock()
        self._log_flush_interval = 5  # 秒
        self._log_flush_size = 20     # 筆
        self._last_log_flush = time.monotonic()
        self._log_seq = 0             # 同毫秒日誌序號（微秒偏移）

        logger.info("Supabase 連線已建立: %s", url)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ─── Config 讀取 ───

    def load_config(self) -> dict | None:
        """讀取最新配置。返回 config_json 或 None（版本未變）。"""
        if not self._enabled:
            return None

        try:
            resp = (
                self._client.table("bot_config")
                .select("version, config_json")
                .order("version", desc=True)
                .limit(1)
                .execute()
            )
            if not resp.data:
                return None

            row = resp.data[0]
            version = row["version"]
            if version == self._last_config_version:
                return None  # 版本相同，不需更新

            self._last_config_version = version
            logger.info("從 Supabase 載入新配置 (version=%d)", version)
            return row["config_json"]
        except Exception as e:
            logger.error("讀取 bot_config 失敗: %s", e)
            return None

    # ─── Strategy Verdicts ───

    def insert_verdict(self, symbol: str, strategy: str, signal: str,
                       confidence: float, reasoning: str = "",
                       cycle_id: str = "",
                       market_type: str = "spot") -> None:
        if not self._enabled:
            return
        try:
            self._client.table("strategy_verdicts").insert({
                "symbol": symbol,
                "strategy": strategy,
                "signal": signal,
                "confidence": confidence,
                "reasoning": reasoning[:500],
                "cycle_id": cycle_id,
                "market_type": market_type,
            }).execute()
        except Exception as e:
            logger.debug("寫入 strategy_verdicts 失敗: %s", e)

    # ─── LLM Decisions ───

    def insert_llm_decision(self, symbol: str, action: str, confidence: float,
                            reasoning: str = "", model: str = "",
                            cycle_id: str = "",
                            market_type: str = "spot") -> None:
        if not self._enabled:
            return
        try:
            self._client.table("llm_decisions").insert({
                "symbol": symbol,
                "action": action,
                "confidence": confidence,
                "reasoning": reasoning[:500],
                "model": model,
                "cycle_id": cycle_id,
                "market_type": market_type,
            }).execute()
        except Exception as e:
            logger.debug("寫入 llm_decisions 失敗: %s", e)

    # ─── Orders ───

    def insert_order(self, order: dict, mode: str = "live",
                     cycle_id: str = "",
                     market_type: str = "spot",
                     position_side: str = "long",
                     leverage: int = 1,
                     reduce_only: bool = False) -> None:
        if not self._enabled:
            return
        try:
            self._client.table("orders").insert({
                "symbol": order.get("symbol", ""),
                "side": order.get("side", ""),
                "order_type": order.get("type", "market"),
                "quantity": order.get("amount", 0),
                "price": order.get("price", 0),
                "filled": order.get("filled", 0),
                "status": order.get("status", "filled"),
                "exchange_id": str(order.get("id", "")),
                "source": order.get("source", "bot"),
                "mode": mode,
                "cycle_id": cycle_id,
                "market_type": market_type,
                "position_side": position_side,
                "leverage": leverage,
                "reduce_only": reduce_only,
            }).execute()
        except Exception as e:
            logger.debug("寫入 orders 失敗: %s", e)

    # ─── Positions ───

    def upsert_position(self, symbol: str, data: dict, mode: str = "live",
                        market_type: str = "spot") -> None:
        if not self._enabled:
            return
        try:
            self._client.table("positions").upsert({
                "symbol": symbol,
                "mode": mode,
                "market_type": market_type,
                "side": data.get("side", "long"),
                "leverage": data.get("leverage", 1),
                "liquidation_price": data.get("liquidation_price"),
                "margin_type": data.get("margin_type"),
                "quantity": data.get("quantity", 0),
                "entry_price": data.get("entry_price", 0),
                "current_price": data.get("current_price", 0),
                "unrealized_pnl": data.get("unrealized_pnl", 0),
                "stop_loss": data.get("stop_loss"),
                "take_profit": data.get("take_profit"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="symbol,mode,market_type,side").execute()
        except Exception as e:
            logger.debug("寫入 positions 失敗: %s", e)

    def load_positions(self, mode: str = "live",
                       market_type: str = "spot") -> list[dict]:
        """從 positions 表載入指定模式和市場類型的持倉，用於重啟恢復。"""
        if not self._enabled:
            return []
        try:
            cols = "symbol, quantity, entry_price, stop_loss, take_profit, side, leverage"
            resp = (
                self._client.table("positions")
                .select(cols)
                .eq("mode", mode)
                .eq("market_type", market_type)
                .execute()
            )
            return resp.data or []
        except Exception as e:
            logger.debug("讀取 positions 失敗: %s", e)
            return []

    def delete_position(self, symbol: str, mode: str = "live",
                        market_type: str = "spot",
                        side: str = "long") -> None:
        if not self._enabled:
            return
        try:
            (self._client.table("positions").delete()
             .eq("symbol", symbol).eq("mode", mode)
             .eq("market_type", market_type).eq("side", side)
             .execute())
        except Exception as e:
            logger.debug("刪除 position 失敗: %s", e)

    # ─── Loan Health ───

    def insert_loan_health(self, loan_data: dict) -> int | None:
        """寫入 loan health 快照，返回 row id 供後續更新 action_taken。"""
        if not self._enabled:
            return None
        try:
            resp = self._client.table("loan_health").insert({
                "loan_coin": loan_data.get("loan_coin", ""),
                "collateral_coin": loan_data.get("collateral_coin", ""),
                "ltv": loan_data.get("ltv", 0),
                "total_debt": loan_data.get("total_debt", 0),
                "collateral_amount": loan_data.get("collateral_amount", 0),
                "action_taken": loan_data.get("action_taken", "none"),
            }).execute()
            if resp.data:
                return resp.data[0].get("id")
            return None
        except Exception as e:
            logger.debug("寫入 loan_health 失敗: %s", e)
            return None

    def update_loan_health_action(self, row_id: int, action: str) -> None:
        """更新 loan_health 記錄的 action_taken（AI 核准後才標記）。"""
        if not self._enabled or row_id is None:
            return
        try:
            self._client.table("loan_health").update({
                "action_taken": action,
            }).eq("id", row_id).execute()
        except Exception as e:
            logger.debug("更新 loan_health action 失敗: %s", e)

    # ─── Loan Adjust History（從幣安 API 同步）───

    def sync_loan_adjustments(self, rows: list[dict]) -> int:
        """同步借貸 LTV 調整歷史，返回新增筆數。利用 UNIQUE 約束去重。"""
        if not self._enabled or not rows:
            return 0
        inserted = 0
        for row in rows:
            try:
                self._client.table("loan_adjust_history").insert({
                    "loan_coin": row.get("loanCoin", ""),
                    "collateral_coin": row.get("collateralCoin", ""),
                    "direction": row.get("direction", ""),
                    "amount": float(row.get("amount", 0)),
                    "pre_ltv": float(row.get("preLTV", 0)),
                    "after_ltv": float(row.get("afterLTV", 0)),
                    "adjust_time": datetime.fromtimestamp(
                        int(row.get("adjustTime", 0)) / 1000, tz=timezone.utc
                    ).isoformat(),
                }).execute()
                inserted += 1
            except Exception as e:
                # 唯一約束衝突表示已同步，跳過
                if "duplicate" in str(e).lower() or "23505" in str(e):
                    continue
                logger.debug("寫入 loan_adjust_history 失敗: %s", e)
        return inserted

    # ─── Bot Logs（批次寫入）───

    def insert_log(self, level: str, module: str, message: str) -> None:
        if not self._enabled:
            return
        # 每筆日誌帶上 Python 端時間戳 + 遞增毫秒偏移
        # JS Date 只有毫秒精度，微秒會被截斷，所以用毫秒
        from datetime import timedelta
        with self._log_lock:
            self._log_seq += 1
            ts = datetime.now(timezone.utc) + timedelta(milliseconds=self._log_seq % 1000)
            self._log_buffer.append({
                "level": level,
                "module": module,
                "message": message[:2000],
                "created_at": ts.isoformat(),
            })
        self._maybe_flush_logs()

    def _maybe_flush_logs(self) -> None:
        """條件性批次寫入日誌（每 5 秒或每 20 筆）。"""
        now = time.monotonic()
        with self._log_lock:
            should_flush = (
                len(self._log_buffer) >= self._log_flush_size
                or (self._log_buffer and now - self._last_log_flush >= self._log_flush_interval)
            )
            if not should_flush:
                return
            batch = self._log_buffer[:]
            self._log_buffer.clear()
            self._last_log_flush = now

        try:
            self._client.table("bot_logs").insert(batch).execute()
        except Exception as e:
            logger.debug("批次寫入 bot_logs 失敗 (%d 筆): %s", len(batch), e)

    def flush_logs(self) -> None:
        """強制清空日誌緩衝。"""
        if not self._enabled:
            return
        with self._log_lock:
            batch = self._log_buffer[:]
            self._log_buffer.clear()
            self._last_log_flush = time.monotonic()
        if batch:
            try:
                self._client.table("bot_logs").insert(batch).execute()
            except Exception as e:
                logger.debug("強制寫入 bot_logs 失敗: %s", e)

    # ─── Market Snapshots ───

    def insert_market_snapshot(self, symbol: str, price: float) -> None:
        if not self._enabled:
            return
        try:
            self._client.table("market_snapshots").insert({
                "symbol": symbol,
                "price": price,
            }).execute()
        except Exception as e:
            logger.debug("寫入 market_snapshots 失敗: %s", e)

    # ─── Account Balances ───

    def insert_balances(self, balances: dict[str, float],
                        usdt_values: dict[str, float | None],
                        snapshot_id: str) -> None:
        """批次寫入帳戶餘額快照。"""
        if not self._enabled or not balances:
            return
        rows = []
        for currency, free in balances.items():
            uv = usdt_values.get(currency)
            rows.append({
                "currency": currency,
                "free": free,
                "usdt_value": uv if uv is not None else 0,
                "snapshot_id": snapshot_id,
            })
        try:
            self._client.table("account_balances").insert(rows).execute()
        except Exception as e:
            logger.debug("寫入 account_balances 失敗: %s", e)

    # ─── Bot Status / 心跳 ───

    def get_last_cycle_num(self) -> int:
        """從 bot_status 取得上次最大 cycle_num，用於重啟接續。"""
        if not self._enabled:
            return 0
        try:
            resp = (
                self._client.table("bot_status")
                .select("cycle_num")
                .order("cycle_num", desc=True)
                .limit(1)
                .execute()
            )
            if resp.data:
                return resp.data[0].get("cycle_num", 0)
        except Exception as e:
            logger.debug("讀取 bot_status cycle_num 失敗: %s", e)
        return 0

    def update_bot_status(self, cycle_num: int, status: str = "running",
                          config_ver: int = 0, pairs: list[str] | None = None,
                          uptime_sec: int = 0) -> None:
        if not self._enabled:
            return
        try:
            self._client.table("bot_status").insert({
                "cycle_num": cycle_num,
                "status": status,
                "config_ver": config_ver,
                "pairs": pairs or [],
                "uptime_sec": uptime_sec,
            }).execute()
        except Exception as e:
            logger.debug("寫入 bot_status 失敗: %s", e)

    # ─── Futures Funding ───

    def insert_futures_funding(self, symbol: str, funding_rate: float,
                               funding_fee: float, position_size: float) -> None:
        if not self._enabled:
            return
        try:
            self._client.table("futures_funding").insert({
                "symbol": symbol,
                "funding_rate": funding_rate,
                "funding_fee": funding_fee,
                "position_size": position_size,
            }).execute()
        except Exception as e:
            logger.debug("寫入 futures_funding 失敗: %s", e)

    # ─── Futures Margin ───

    def insert_futures_margin(self, wallet_balance: float,
                              available_balance: float,
                              unrealized_pnl: float,
                              margin_balance: float,
                              margin_ratio: float) -> None:
        if not self._enabled:
            return
        try:
            self._client.table("futures_margin").insert({
                "total_wallet_balance": wallet_balance,
                "available_balance": available_balance,
                "total_unrealized_pnl": unrealized_pnl,
                "total_margin_balance": margin_balance,
                "margin_ratio": margin_ratio,
            }).execute()
        except Exception as e:
            logger.debug("寫入 futures_margin 失敗: %s", e)
