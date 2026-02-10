"""下載歷史 K 線數據至本地快取。"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config.settings import Settings
from bot.data.fetcher import DataFetcher
from bot.exchange.binance_client import BinanceClient
from bot.logging_config.logger import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="下載歷史 K 線數據")
    parser.add_argument("--symbol", default="BTC/USDT", help="交易對")
    parser.add_argument("--timeframe", default="1h", help="時間框架")
    parser.add_argument("--start", default="2024-01-01", help="開始日期")
    parser.add_argument("--end", default="2025-01-01", help="結束日期")
    args = parser.parse_args()

    settings = Settings.load()
    setup_logging(level="INFO")

    exchange = BinanceClient(settings.exchange)
    fetcher = DataFetcher(exchange)

    print(f"下載 {args.symbol} {args.timeframe} ({args.start} ~ {args.end})...")
    df = fetcher.fetch_historical(
        symbol=args.symbol,
        timeframe=args.timeframe,
        start_date=args.start,
        end_date=args.end,
    )

    print(f"完成! 共 {len(df)} 根 K 線")
    print(f"期間: {df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]}")
    print(f"\n前 5 筆:")
    print(df.head())


if __name__ == "__main__":
    main()
