"""下載幣安歷史 aggTrade 數據。

用法：
    python scripts/download_aggtrades.py --symbol BTCUSDT --date 2024-01-15

數據來源：Binance 公開數據
https://data.binance.vision/data/spot/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{DATE}.zip
"""

import argparse
import csv
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "aggtrades"
BASE_URL = "https://data.binance.vision/data/spot/daily/aggTrades"


def download_day(symbol: str, date_str: str) -> Path:
    """下載單日 aggTrade 數據並轉換為標準 CSV。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    output_path = DATA_DIR / f"{symbol}-{date_str}.csv"
    if output_path.exists():
        print(f"  已存在: {output_path.name}")
        return output_path

    url = f"{BASE_URL}/{symbol}/{symbol}-aggTrades-{date_str}.zip"
    print(f"  下載: {url}")

    try:
        response = urlopen(url)
        zip_data = response.read()
    except Exception as e:
        print(f"  下載失敗: {e}")
        raise

    with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
        csv_name = zf.namelist()[0]
        raw = zf.read(csv_name).decode("utf-8")

    # Binance aggTrade CSV 欄位：
    # agg_trade_id, price, quantity, first_trade_id, last_trade_id,
    # transact_time, is_buyer_maker, is_best_match
    lines = raw.strip().split("\n")

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["trade_id", "price", "quantity", "timestamp", "is_buyer_maker"])

        for line in lines:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            writer.writerow([
                parts[0],           # trade_id
                parts[1],           # price
                parts[2],           # quantity
                parts[5],           # timestamp (ms)
                parts[6].strip(),   # is_buyer_maker
            ])

    print(f"  儲存: {output_path.name} ({len(lines)} 筆)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="下載幣安歷史 aggTrade 數據")
    parser.add_argument("--symbol", default="BTCUSDT", help="交易對 (e.g., BTCUSDT)")
    parser.add_argument("--date", default=None, help="單日日期 (YYYY-MM-DD)")
    parser.add_argument("--start", default=None, help="起始日期")
    parser.add_argument("--end", default=None, help="結束日期")
    args = parser.parse_args()

    if args.date:
        download_day(args.symbol, args.date)
    elif args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            try:
                download_day(args.symbol, date_str)
            except Exception:
                pass
            current += timedelta(days=1)
    else:
        print("請指定 --date 或 --start/--end")


if __name__ == "__main__":
    main()
