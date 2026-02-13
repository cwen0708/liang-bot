"""快速查詢帳戶餘額。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config.settings import Settings
from bot.exchange.binance_native_client import BinanceClient
from bot.logging_config.logger import setup_logging


def main() -> None:
    settings = Settings.load()
    setup_logging(level="INFO")

    print(f"連接幣安 {'測試網' if settings.exchange.testnet else '正式網'}...")
    exchange = BinanceClient(settings.exchange)

    balance = exchange.get_balance()

    print("\n帳戶餘額:")
    print("=" * 35)
    for currency, amount in sorted(balance.items()):
        print(f"  {currency:<10s} {amount:>15.8f}")
    print("=" * 35)


if __name__ == "__main__":
    main()
