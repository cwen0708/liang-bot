"""回測模組測試。"""

import pytest

from bot.backtest.metrics import calculate_metrics
from bot.backtest.simulator import BacktestSimulator


class TestBacktestSimulator:
    def setup_method(self):
        self.sim = BacktestSimulator(initial_balance=10000.0, commission_pct=0.001)

    def test_initial_state(self):
        assert self.sim.balance == 10000.0
        assert self.sim.holdings == {}
        assert self.sim.trades == []

    def test_buy(self):
        result = self.sim.buy("BTC/USDT", 40000.0, 0.1, "2024-01-01")
        assert result is True
        assert self.sim.holdings["BTC/USDT"] == 0.1
        # 餘額 = 10000 - (40000 * 0.1) - (4000 * 0.001) = 5996.0
        assert self.sim.balance == pytest.approx(5996.0, rel=1e-4)

    def test_buy_insufficient_balance(self):
        result = self.sim.buy("BTC/USDT", 40000.0, 1.0, "2024-01-01")
        assert result is False
        assert "BTC/USDT" not in self.sim.holdings

    def test_sell(self):
        self.sim.buy("BTC/USDT", 40000.0, 0.1, "2024-01-01")
        result = self.sim.sell("BTC/USDT", 42000.0, 0.1, "2024-01-02")
        assert result is True
        assert "BTC/USDT" not in self.sim.holdings
        assert len(self.sim.trades) == 2

    def test_sell_without_holdings(self):
        result = self.sim.sell("BTC/USDT", 40000.0, 0.1, "2024-01-01")
        assert result is False

    def test_portfolio_value(self):
        self.sim.buy("BTC/USDT", 40000.0, 0.1, "2024-01-01")
        value = self.sim.get_portfolio_value({"BTC/USDT": 42000.0})
        # 餘額 + 持倉市值
        expected = self.sim.balance + 42000.0 * 0.1
        assert value == pytest.approx(expected, rel=1e-4)

    def test_pnl_calculation(self):
        self.sim.buy("BTC/USDT", 40000.0, 0.1, "2024-01-01")
        self.sim.sell("BTC/USDT", 44000.0, 0.1, "2024-01-02")
        sell_trade = self.sim.trades[-1]
        # PnL = (44000 - 40000) * 0.1 - 手續費
        assert sell_trade.pnl > 0


class TestMetrics:
    def test_calculate_metrics_no_trades(self):
        sim = BacktestSimulator(10000.0)
        sim.equity_curve = [10000.0] * 10
        metrics = calculate_metrics(sim)
        assert metrics.total_return_pct == pytest.approx(0.0)
        assert metrics.total_trades == 0

    def test_calculate_metrics_with_profit(self):
        sim = BacktestSimulator(10000.0)
        sim.equity_curve = [10000.0 + i * 10 for i in range(100)]
        # 模擬一筆獲利交易
        from bot.backtest.simulator import Trade
        sim.trades.append(Trade("2024-01-01", "BTC/USDT", "sell", 42000, 0.1, 4.2, pnl=200))
        metrics = calculate_metrics(sim)
        assert metrics.total_return_pct > 0
        assert metrics.winning_trades == 1
