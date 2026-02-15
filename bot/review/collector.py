"""從 Supabase 收集復盤所需的數據。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from bot.logging_config import get_logger

logger = get_logger("review.collector")

TW = timezone(timedelta(hours=8))


@dataclass
class ReviewData:
    """單次復盤的原始數據。"""

    decisions: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    positions: list[dict] = field(default_factory=list)
    balance: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    margin: dict | None = None


@dataclass
class WeeklyStats:
    """近 7 天累計統計。"""

    total_decisions: int = 0
    total_orders: int = 0
    win_count: int = 0
    loss_count: int = 0
    total_pnl: float = 0.0
    avg_confidence: float = 0.0
    strategy_accuracy: dict[str, float] = field(default_factory=dict)


class ReviewDataCollector:
    """從 Supabase 收集復盤所需的數據。"""

    def __init__(self, db) -> None:
        self.db = db

    def collect(self, mode: str = "live", hours: int = 24) -> ReviewData:
        """收集過去 N 小時的數據。"""
        client = self.db._client
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        data = ReviewData()

        # LLM 決策
        res = (
            client.table("llm_decisions")
            .select("symbol,action,confidence,reasoning,executed,reject_reason,entry_price,stop_loss,take_profit,market_type,cycle_id,created_at")
            .eq("mode", mode)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
        data.decisions = res.data or []

        # 策略結論
        res = (
            client.table("strategy_verdicts")
            .select("symbol,strategy,signal,confidence,reasoning,timeframe,market_type,created_at")
            .eq("mode", mode)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
        data.verdicts = res.data or []

        # 訂單
        res = (
            client.table("orders")
            .select("symbol,side,order_type,quantity,price,filled,status,market_type,position_side,leverage,reduce_only,created_at")
            .eq("mode", mode)
            .gte("created_at", cutoff)
            .order("created_at", desc=True)
            .execute()
        )
        data.orders = res.data or []

        # 當前持倉
        res = (
            client.table("positions")
            .select("symbol,quantity,entry_price,current_price,unrealized_pnl,side,leverage,market_type,stop_loss,take_profit,entry_horizon,entry_reasoning,updated_at")
            .eq("mode", mode)
            .execute()
        )
        data.positions = res.data or []

        # 最新餘額快照
        res = (
            client.table("account_balances")
            .select("currency,free,usdt_value,snapshot_id,created_at")
            .eq("mode", mode)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )
        if res.data:
            # 取最新 snapshot_id 的所有行
            latest_snap = res.data[0].get("snapshot_id", "")
            data.balance = {
                "snapshot_id": latest_snap,
                "items": [r for r in res.data if r.get("snapshot_id") == latest_snap],
            }

        # 合約保證金（最新一筆）
        res = (
            client.table("futures_margin")
            .select("*")
            .eq("mode", mode)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        data.margin = res.data[0] if res.data else None

        # 當前配置
        cfg = self.db.load_config()
        if cfg:
            # 移除敏感資訊
            safe_cfg = {k: v for k, v in cfg.items() if k != "exchange"}
            data.config = safe_cfg

        logger.info(
            "復盤數據收集完成: %d 決策, %d 結論, %d 訂單, %d 持倉",
            len(data.decisions), len(data.verdicts), len(data.orders), len(data.positions),
        )
        return data

    def collect_weekly_stats(self, mode: str = "live") -> WeeklyStats:
        """收集近 7 天的累計統計。"""
        client = self.db._client
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        stats = WeeklyStats()

        # 決策統計
        res = (
            client.table("llm_decisions")
            .select("action,confidence,executed")
            .eq("mode", mode)
            .gte("created_at", cutoff)
            .execute()
        )
        decisions = res.data or []
        stats.total_decisions = len(decisions)
        if decisions:
            stats.avg_confidence = sum(d.get("confidence", 0) for d in decisions) / len(decisions)

        # 訂單統計（勝率計算）
        res = (
            client.table("orders")
            .select("symbol,side,price,filled,status,reduce_only,market_type,position_side")
            .eq("mode", mode)
            .gte("created_at", cutoff)
            .in_("status", ["filled", "closed"])
            .execute()
        )
        orders = res.data or []
        stats.total_orders = len(orders)

        # 配對開平倉計算勝率（簡化：reduce_only 訂單視為平倉）
        close_orders = [o for o in orders if o.get("reduce_only")]
        open_orders = [o for o in orders if not o.get("reduce_only")]
        # 粗略估算：比較平倉價與同 symbol 最近開倉價
        for co in close_orders:
            matching = [
                o for o in open_orders
                if o["symbol"] == co["symbol"]
                and o.get("position_side", "long") == co.get("position_side", "long")
            ]
            if matching:
                entry_price = matching[-1].get("price", 0)
                exit_price = co.get("price", 0)
                is_long = co.get("position_side", "long") == "long"
                pnl = (exit_price - entry_price) if is_long else (entry_price - exit_price)
                if pnl > 0:
                    stats.win_count += 1
                else:
                    stats.loss_count += 1
                stats.total_pnl += pnl * co.get("filled", co.get("quantity", 0))

        # 策略準確率（粗略：BUY 後價格上漲 = 準確）
        res = (
            client.table("strategy_verdicts")
            .select("strategy,signal,confidence")
            .eq("mode", mode)
            .gte("created_at", cutoff)
            .execute()
        )
        verdicts = res.data or []
        strat_counts: dict[str, dict[str, int]] = {}
        for v in verdicts:
            name = v.get("strategy", "")
            sig = v.get("signal", "HOLD")
            if name not in strat_counts:
                strat_counts[name] = {"total": 0, "non_hold": 0}
            strat_counts[name]["total"] += 1
            if sig != "HOLD":
                strat_counts[name]["non_hold"] += 1

        for name, counts in strat_counts.items():
            # 活躍度 = non_hold / total（信號多樣性指標）
            stats.strategy_accuracy[name] = (
                counts["non_hold"] / counts["total"] if counts["total"] > 0 else 0
            )

        logger.info(
            "7 日統計: %d 決策, %d 訂單, %d 勝 / %d 負, PnL %.2f",
            stats.total_decisions, stats.total_orders,
            stats.win_count, stats.loss_count, stats.total_pnl,
        )
        return stats
