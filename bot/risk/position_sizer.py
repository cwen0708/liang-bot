"""部位大小計算。"""

from bot.logging_config import get_logger

logger = get_logger("risk.position_sizer")


class PercentageSizer:
    """按帳戶資金百分比計算部位大小。"""

    def __init__(self, max_position_pct: float = 0.02) -> None:
        self.max_position_pct = max_position_pct

    def calculate(self, balance: float, price: float) -> float:
        """
        計算可買入的數量。

        Args:
            balance: 可用 USDT 餘額
            price: 當前價格

        Returns:
            買入數量（基礎幣種）
        """
        usdt_amount = balance * self.max_position_pct
        quantity = usdt_amount / price
        logger.debug(
            "部位計算: 餘額=%.2f USDT, 比例=%.1f%%, 金額=%.2f USDT, 數量=%.8f",
            balance, self.max_position_pct * 100, usdt_amount, quantity,
        )
        return quantity
