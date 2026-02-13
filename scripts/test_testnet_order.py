"""測試 Binance Testnet 真實下單（使用原生 SDK）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from bot.config.settings import Settings
from bot.exchange.binance_native_client import BinanceClient

settings = Settings.load()
print(f"Testnet: {settings.exchange.testnet}")

exchange = BinanceClient(settings.exchange)

# 查餘額
print("\n--- Balance ---")
balance = exchange.get_balance()
for cur, amt in sorted(balance.items()):
    if amt > 0:
        print(f"  {cur}: {amt:.8f}")

# 取得 BTC 現價
symbol = "BTC/USDT"
ticker = exchange.get_ticker(symbol)
price = ticker["last"]
print(f"\n{symbol} price: ${price:,.2f}")

# 下單: 買 $10 USDT 的 BTC
quantity = round(10.0 / price, 5)
print(f"\n--- Placing BUY order ---")
print(f"Quantity: {quantity} BTC (~$10 USDT)")

try:
    order = exchange.place_market_order(symbol, "buy", quantity)
    print(f"\nOrder placed!")
    for k, v in order.items():
        print(f"  {k}: {v}")
except Exception as e:
    print(f"Order failed: {e}")

# 查餘額（下單後）
print("\n--- Balance after order ---")
balance = exchange.get_balance()
for cur in ("USDT", "BTC"):
    print(f"  {cur}: {balance.get(cur, 0):.8f}")
