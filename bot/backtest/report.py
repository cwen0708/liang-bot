"""回測報告生成 — 文字輸出 + 可選圖表。"""

from pathlib import Path

from bot.backtest.metrics import BacktestMetrics
from bot.backtest.simulator import BacktestSimulator
from bot.config.settings import PROJECT_ROOT
from bot.logging_config import get_logger

logger = get_logger("backtest.report")

REPORT_DIR = PROJECT_ROOT / "data" / "backtest_results"


def save_report(
    metrics: BacktestMetrics,
    simulator: BacktestSimulator,
    strategy_name: str,
    symbol: str,
    plot: bool = True,
) -> Path:
    """儲存回測報告至檔案。"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # 文字報告
    report_file = REPORT_DIR / f"{strategy_name}_{symbol.replace('/', '-')}_report.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(f"策略: {strategy_name}\n")
        f.write(f"交易對: {symbol}\n")
        f.write(str(metrics))
        f.write(f"\n\n總交易紀錄: {len(simulator.trades)} 筆\n")
        for t in simulator.trades:
            f.write(
                f"  {t.timestamp} | {t.side:4s} | {t.symbol} | "
                f"價格={t.price:.2f} | 數量={t.quantity:.8f} | "
                f"手續費={t.commission:.4f} | PnL={t.pnl:.2f}\n"
            )

    logger.info("報告已儲存: %s", report_file)

    # 權益曲線圖
    if plot and simulator.equity_curve:
        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots(figsize=(12, 6))
            ax.plot(simulator.equity_curve, linewidth=1.5)
            ax.set_title(f"Equity Curve — {strategy_name} ({symbol})")
            ax.set_xlabel("Bar")
            ax.set_ylabel("Portfolio Value (USDT)")
            ax.axhline(
                y=simulator.initial_balance,
                color="gray",
                linestyle="--",
                label="Initial Balance",
            )
            ax.legend()
            ax.grid(True, alpha=0.3)

            chart_file = REPORT_DIR / f"{strategy_name}_{symbol.replace('/', '-')}_equity.png"
            fig.savefig(chart_file, dpi=150, bbox_inches="tight")
            plt.close(fig)
            logger.info("權益曲線圖已儲存: %s", chart_file)
        except ImportError:
            logger.warning("matplotlib 未安裝，跳過圖表生成")

    return report_file
