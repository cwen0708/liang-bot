"""訂單管理 — 追蹤所有開放訂單與持倉狀態。"""

import json
from pathlib import Path

from bot.config.settings import PROJECT_ROOT
from bot.logging_config import get_logger

logger = get_logger("execution.order_manager")

STATE_FILE = PROJECT_ROOT / "data" / "state.json"


class OrderManager:
    """持倉與訂單狀態管理，支援 JSON 持久化。"""

    def __init__(self) -> None:
        self._orders: list[dict] = []
        self._load_state()

    def add_order(self, order: dict) -> None:
        """記錄已成交的訂單。"""
        self._orders.append(order)
        self._save_state()
        logger.info("記錄訂單: %s %s %s", order["side"], order["symbol"], order["id"])

    def get_orders(self, symbol: str | None = None) -> list[dict]:
        """取得訂單紀錄，可依幣對過濾。"""
        if symbol:
            return [o for o in self._orders if o["symbol"] == symbol]
        return list(self._orders)

    def clear_orders(self) -> None:
        """清除所有訂單紀錄。"""
        self._orders.clear()
        self._save_state()

    def _save_state(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"orders": self._orders}, f, indent=2, default=str)

    def _load_state(self) -> None:
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._orders = data.get("orders", [])
                logger.info("載入 %d 筆歷史訂單", len(self._orders))
            except (json.JSONDecodeError, KeyError):
                logger.warning("狀態檔損壞，重新初始化")
                self._orders = []
