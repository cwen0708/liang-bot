"""Tick 級回測引擎 — 用歷史 aggTrade 數據回測訂單流策略。"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from bot.backtest.metrics import BacktestMetrics, calculate_metrics
from bot.backtest.simulator import BacktestSimulator
from bot.config.settings import BacktestConfig, OrderFlowConfig, SpotConfig
from bot.data.bar_aggregator import BarAggregator
from bot.data.models import AggTrade
from bot.logging_config import get_logger
from bot.risk.position_sizer import PercentageSizer
from bot.strategy.base import OrderFlowStrategy
from bot.strategy.signals import Signal

logger = get_logger("backtest.tick_engine")


class TickBacktestEngine:
    """
    Tick 級回測引擎。

    讀取歷史 aggTrade CSV，聚合為 OrderFlowBar，
    送入 OrderFlowStrategy 執行回測。

    CSV 格式：trade_id,price,quantity,timestamp,is_buyer_maker
    """

    def __init__(
        self,
        config: BacktestConfig,
        risk_config: SpotConfig,
        orderflow_config: OrderFlowConfig,
    ) -> None:
        self.config = config
        self.risk_config = risk_config
        self.orderflow_config = orderflow_config

    def run(
        self,
        strategy: OrderFlowStrategy,
        data_path: str | Path,
        symbol: str,
    ) -> BacktestMetrics:
        """
        執行 tick 級回測。

        Args:
            strategy: 訂單流策略實例。
            data_path: aggTrade CSV 檔案路徑。
            symbol: 交易對。

        Returns:
            BacktestMetrics 績效指標。
        """
        data_path = Path(data_path)
        if not data_path.exists():
            raise FileNotFoundError(f"aggTrade 數據檔不存在: {data_path}")

        simulator = BacktestSimulator(
            initial_balance=self.config.initial_balance,
            commission_pct=self.config.commission_pct,
        )
        sizer = PercentageSizer(self.risk_config.max_position_pct)

        aggregator = BarAggregator(
            interval_seconds=self.orderflow_config.bar_interval_seconds,
            tick_size=self.orderflow_config.tick_size,
        )

        strategy.reset()
        bar_count = 0
        trade_count = 0

        logger.info(
            "開始 tick 回測: %s, 策略=%s, 數據=%s, 初始資金=%.2f USDT",
            symbol, strategy.name, data_path.name, self.config.initial_balance,
        )

        for trade in self._read_trades(data_path):
            trade_count += 1
            bar = aggregator.add_trade(trade)

            if bar is None:
                continue

            bar_count += 1
            self._process_bar(strategy, simulator, sizer, symbol, bar)

        # 處理最後未完成的 bar
        last_bar = aggregator.flush()
        if last_bar:
            bar_count += 1
            self._process_bar(strategy, simulator, sizer, symbol, last_bar)

        logger.info(
            "tick 回測完成: 處理 %d 筆 aggTrade, 聚合 %d 根 K 線", trade_count, bar_count
        )

        metrics = calculate_metrics(simulator)
        logger.info(str(metrics))
        return metrics

    def _process_bar(
        self,
        strategy: OrderFlowStrategy,
        simulator: BacktestSimulator,
        sizer: PercentageSizer,
        symbol: str,
        bar,
    ) -> None:
        """處理單根 OrderFlowBar。"""
        verdict = strategy.on_bar(symbol, bar)
        current_price = bar.close
        timestamp = str(bar.timestamp)

        if verdict.signal == Signal.BUY and symbol not in simulator.holdings:
            quantity = sizer.calculate(simulator.balance, current_price)
            if quantity > 0:
                simulator.buy(symbol, current_price, quantity, timestamp)

        elif verdict.signal == Signal.SELL and symbol in simulator.holdings:
            quantity = simulator.holdings.get(symbol, 0)
            if quantity > 0:
                simulator.sell(symbol, current_price, quantity, timestamp)

        # 停損停利
        if symbol in simulator.holdings:
            entry = simulator.entry_prices.get(symbol, current_price)
            stop_loss = entry * (1 - self.risk_config.stop_loss_pct)
            take_profit = entry * (1 + self.risk_config.take_profit_pct)

            if current_price <= stop_loss or current_price >= take_profit:
                qty = simulator.holdings[symbol]
                simulator.sell(symbol, current_price, qty, timestamp)

        simulator.snapshot_equity({symbol: current_price})

    @staticmethod
    def _read_trades(path: Path):
        """逐行讀取 aggTrade CSV。"""
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield AggTrade(
                    trade_id=int(row["trade_id"]),
                    price=float(row["price"]),
                    quantity=float(row["quantity"]),
                    timestamp=datetime.fromtimestamp(
                        float(row["timestamp"]) / 1000, tz=timezone.utc
                    ),
                    is_buyer_maker=row["is_buyer_maker"].lower() in ("true", "1"),
                )
