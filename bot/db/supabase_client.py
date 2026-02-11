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
                       cycle_id: str = "") -> None:
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
            }).execute()
        except Exception as e:
            logger.debug("寫入 strategy_verdicts 失敗: %s", e)

    # ─── LLM Decisions ───

    def insert_llm_decision(self, symbol: str, action: str, confidence: float,
                            reasoning: str = "", model: str = "",
                            cycle_id: str = "") -> None:
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
            }).execute()
        except Exception as e:
            logger.debug("寫入 llm_decisions 失敗: %s", e)

    # ─── Orders ───

    def insert_order(self, order: dict, mode: str = "live",
                     cycle_id: str = "") -> None:
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
            }).execute()
        except Exception as e:
            logger.debug("寫入 orders 失敗: %s", e)

    # ─── Positions ───

    def upsert_position(self, symbol: str, data: dict, mode: str = "live") -> None:
        if not self._enabled:
            return
        try:
            self._client.table("positions").upsert({
                "symbol": symbol,
                "mode": mode,
                "quantity": data.get("quantity", 0),
                "entry_price": data.get("entry_price", 0),
                "current_price": data.get("current_price", 0),
                "unrealized_pnl": data.get("unrealized_pnl", 0),
                "stop_loss": data.get("stop_loss"),
                "take_profit": data.get("take_profit"),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, on_conflict="symbol,mode").execute()
        except Exception as e:
            logger.debug("寫入 positions 失敗: %s", e)

    def delete_position(self, symbol: str, mode: str = "live") -> None:
        if not self._enabled:
            return
        try:
            (self._client.table("positions").delete()
             .eq("symbol", symbol).eq("mode", mode).execute())
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

    # ─── Bot Logs（批次寫入）───

    def insert_log(self, level: str, module: str, message: str) -> None:
        if not self._enabled:
            return
        with self._log_lock:
            self._log_buffer.append({
                "level": level,
                "module": module,
                "message": message[:2000],
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
