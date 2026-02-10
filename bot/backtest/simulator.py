"""模擬交易所 — 用於回測時模擬下單和成交。"""

from dataclasses import dataclass, field

from bot.logging_config import get_logger

logger = get_logger("backtest.simulator")


@dataclass
class Trade:
    timestamp: str
    symbol: str
    side: str
    price: float
    quantity: float
    commission: float
    pnl: float = 0.0


class BacktestSimulator:
    """回測用模擬交易引擎。"""

    def __init__(self, initial_balance: float, commission_pct: float = 0.001) -> None:
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.commission_pct = commission_pct
        self.holdings: dict[str, float] = {}  # {symbol: quantity}
        self.entry_prices: dict[str, float] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[float] = []

    def buy(self, symbol: str, price: float, quantity: float, timestamp: str) -> bool:
        """模擬買入。"""
        cost = price * quantity
        commission = cost * self.commission_pct
        total_cost = cost + commission

        if total_cost > self.balance:
            logger.debug("餘額不足: 需要 %.2f, 可用 %.2f", total_cost, self.balance)
            return False

        self.balance -= total_cost
        self.holdings[symbol] = self.holdings.get(symbol, 0) + quantity
        self.entry_prices[symbol] = price

        trade = Trade(
            timestamp=timestamp,
            symbol=symbol,
            side="buy",
            price=price,
            quantity=quantity,
            commission=commission,
        )
        self.trades.append(trade)
        return True

    def sell(self, symbol: str, price: float, quantity: float, timestamp: str) -> bool:
        """模擬賣出。"""
        if symbol not in self.holdings or self.holdings[symbol] < quantity:
            return False

        revenue = price * quantity
        commission = revenue * self.commission_pct
        net_revenue = revenue - commission

        entry_price = self.entry_prices.get(symbol, price)
        pnl = (price - entry_price) * quantity - commission

        self.balance += net_revenue
        self.holdings[symbol] -= quantity

        if self.holdings[symbol] <= 1e-10:
            del self.holdings[symbol]
            self.entry_prices.pop(symbol, None)

        trade = Trade(
            timestamp=timestamp,
            symbol=symbol,
            side="sell",
            price=price,
            quantity=quantity,
            commission=commission,
            pnl=pnl,
        )
        self.trades.append(trade)
        return True

    def get_portfolio_value(self, current_prices: dict[str, float]) -> float:
        """計算當前投資組合總值（含未實現損益）。"""
        holdings_value = sum(
            current_prices.get(symbol, 0) * qty
            for symbol, qty in self.holdings.items()
        )
        return self.balance + holdings_value

    def snapshot_equity(self, current_prices: dict[str, float]) -> None:
        """記錄權益曲線資料點。"""
        self.equity_curve.append(self.get_portfolio_value(current_prices))
