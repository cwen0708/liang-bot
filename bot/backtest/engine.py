"""回測引擎 — 將歷史數據回放通過策略，模擬交易。"""

from __future__ import annotations

from typing import Union

import pandas as pd

from bot.backtest.metrics import BacktestMetrics, calculate_metrics
from bot.backtest.simulator import BacktestSimulator
from bot.config.settings import BacktestConfig, RiskConfig
from bot.logging_config import get_logger
from bot.risk.position_sizer import PercentageSizer
from bot.strategy.base import BaseStrategy, OrderFlowStrategy
from bot.strategy.signals import Signal

logger = get_logger("backtest.engine")


class BacktestEngine:
    """回測引擎。"""

    def __init__(self, config: BacktestConfig, risk_config: RiskConfig) -> None:
        self.config = config
        self.risk_config = risk_config

    def run(
        self, strategy: BaseStrategy, df: pd.DataFrame, symbol: str
    ) -> BacktestMetrics:
        """
        執行回測。

        Args:
            strategy: 交易策略實例
            df: 歷史 K 線 DataFrame (timestamp, open, high, low, close, volume)
            symbol: 交易對

        Returns:
            BacktestMetrics 績效指標
        """
        simulator = BacktestSimulator(
            initial_balance=self.config.initial_balance,
            commission_pct=self.config.commission_pct,
        )
        sizer = PercentageSizer(self.risk_config.max_position_pct)

        required = strategy.required_candles
        logger.info(
            "開始回測: %s, 策略=%s, 資料=%d 根 K 線, 初始資金=%.2f USDT",
            symbol, strategy.name, len(df), self.config.initial_balance,
        )

        for i in range(required, len(df)):
            window = df.iloc[: i + 1].copy()
            current_price = float(window["close"].iloc[-1])
            timestamp = str(window["timestamp"].iloc[-1])

            signal = strategy.generate_signal(window)

            if signal == Signal.BUY and symbol not in simulator.holdings:
                quantity = sizer.calculate(simulator.balance, current_price)
                if quantity > 0:
                    simulator.buy(symbol, current_price, quantity, timestamp)

            elif signal == Signal.SELL and symbol in simulator.holdings:
                quantity = simulator.holdings.get(symbol, 0)
                if quantity > 0:
                    simulator.sell(symbol, current_price, quantity, timestamp)

            # 檢查停損停利
            if symbol in simulator.holdings:
                entry = simulator.entry_prices.get(symbol, current_price)
                stop_loss = entry * (1 - self.risk_config.stop_loss_pct)
                take_profit = entry * (1 + self.risk_config.take_profit_pct)

                if current_price <= stop_loss or current_price >= take_profit:
                    qty = simulator.holdings[symbol]
                    simulator.sell(symbol, current_price, qty, timestamp)

            simulator.snapshot_equity({symbol: current_price})

        # 計算績效
        metrics = calculate_metrics(simulator)

        logger.info("回測完成")
        logger.info(str(metrics))

        return metrics
