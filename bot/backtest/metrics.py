"""回測績效指標計算。"""

from dataclasses import dataclass

import numpy as np

from bot.backtest.simulator import BacktestSimulator


@dataclass
class BacktestMetrics:
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    final_balance: float
    # 訂單流附加指標
    delta_efficiency: float = 0.0  # Delta 方向與獲利方向的一致性

    def __str__(self) -> str:
        lines = (
            f"\n{'=' * 50}\n"
            f"  回測績效報告\n"
            f"{'=' * 50}\n"
            f"  總報酬率:       {self.total_return_pct:>10.2f}%\n"
            f"  年化報酬率:     {self.annualized_return_pct:>10.2f}%\n"
            f"  最大回撤:       {self.max_drawdown_pct:>10.2f}%\n"
            f"  Sharpe Ratio:   {self.sharpe_ratio:>10.2f}\n"
            f"  勝率:           {self.win_rate_pct:>10.2f}%\n"
            f"  盈虧比:         {self.profit_factor:>10.2f}\n"
            f"  總交易次數:     {self.total_trades:>10d}\n"
            f"  獲利次數:       {self.winning_trades:>10d}\n"
            f"  虧損次數:       {self.losing_trades:>10d}\n"
            f"  平均獲利:       {self.avg_win:>10.2f} USDT\n"
            f"  平均虧損:       {self.avg_loss:>10.2f} USDT\n"
            f"  最終餘額:       {self.final_balance:>10.2f} USDT\n"
        )
        if self.delta_efficiency > 0:
            lines += f"  Delta 效率:     {self.delta_efficiency:>10.2f}%\n"
        lines += f"{'=' * 50}"
        return lines


def calculate_metrics(simulator: BacktestSimulator, trading_days: int = 365) -> BacktestMetrics:
    """根據模擬器結果計算績效指標。"""
    equity = np.array(simulator.equity_curve) if simulator.equity_curve else np.array([simulator.initial_balance])

    # 總報酬
    final_value = equity[-1] if len(equity) > 0 else simulator.initial_balance
    total_return = (final_value - simulator.initial_balance) / simulator.initial_balance

    # 年化報酬（假設每根 K 線是一個時間單位）
    n_periods = max(len(equity), 1)
    annualized_return = (1 + total_return) ** (trading_days / max(n_periods, 1)) - 1

    # 最大回撤
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / np.where(peak > 0, peak, 1)
    max_drawdown = float(np.min(drawdown)) if len(drawdown) > 0 else 0.0

    # Sharpe Ratio（假設無風險利率為 0）
    if len(equity) > 1:
        returns = np.diff(equity) / equity[:-1]
        sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(trading_days)) if np.std(returns) > 0 else 0.0
    else:
        sharpe = 0.0

    # 交易統計
    sell_trades = [t for t in simulator.trades if t.side == "sell"]
    total_trades = len(sell_trades)

    wins = [t for t in sell_trades if t.pnl > 0]
    losses = [t for t in sell_trades if t.pnl <= 0]

    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0.0
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0

    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    return BacktestMetrics(
        total_return_pct=total_return * 100,
        annualized_return_pct=annualized_return * 100,
        max_drawdown_pct=max_drawdown * 100,
        sharpe_ratio=sharpe,
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        total_trades=total_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        avg_win=avg_win,
        avg_loss=avg_loss,
        final_balance=final_value,
    )
