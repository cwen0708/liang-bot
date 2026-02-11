"""執行回測腳本。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.backtest.engine import BacktestEngine
from bot.backtest.report import save_report
from bot.backtest.simulator import BacktestSimulator
from bot.config.settings import Settings
from bot.data.fetcher import DataFetcher
from bot.exchange.binance_client import BinanceClient
from bot.logging_config.logger import setup_logging
from bot.strategy.sma_crossover import SMACrossoverStrategy


def main() -> None:
    parser = argparse.ArgumentParser(description="執行策略回測")
    parser.add_argument("--symbol", default="BTC/USDT", help="交易對")
    parser.add_argument("--no-plot", action="store_true", help="不生成圖表")
    args = parser.parse_args()

    settings = Settings.load()
    setup_logging(level="INFO")

    print("初始化交易所連線...")
    exchange = BinanceClient(settings.exchange)
    fetcher = DataFetcher(exchange)

    # 下載歷史數據
    print(f"下載 {args.symbol} 歷史數據...")
    df = fetcher.fetch_historical(
        symbol=args.symbol,
        timeframe=settings.spot.timeframe,
        start_date=settings.backtest.start_date,
        end_date=settings.backtest.end_date,
    )
    print(f"已載入 {len(df)} 根 K 線")

    # 建立策略與回測引擎
    strategy = SMACrossoverStrategy(settings.strategy.params)
    engine = BacktestEngine(settings.backtest, settings.spot)

    # 執行回測
    metrics = engine.run(strategy, df, args.symbol)
    print(metrics)

    # 生成報告
    sim = BacktestSimulator(settings.backtest.initial_balance, settings.backtest.commission_pct)
    report_path = save_report(
        metrics, sim, strategy.name, args.symbol, plot=not args.no_plot
    )
    print(f"\n報告已儲存至: {report_path}")


if __name__ == "__main__":
    main()
