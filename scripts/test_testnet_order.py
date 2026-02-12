"""測試 Binance Testnet 真實下單。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import ccxt

# Binance Spot Testnet
exchange = ccxt.binance({
    "apiKey": "bPccTyW7B1a5Tf5zamOTcCKJEPuh0P3mgK7rhvdRkopvu4H23bHimTORt5m2iEyF",
    "secret": "7wk9xAjpXuqrwcS8f1oDQ4z2QVPSNTbFBhrk3n7KLlX4ZegcnocHRjm6Mbey7Tiu",
    "sandbox": True,
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})

print("Loading markets...")
exchange.load_markets()
print(f"Markets loaded: {len(exchange.markets)} pairs")

# 查餘額
print("\n--- Balance ---")
balance = exchange.fetch_balance()
usdt = balance.get("USDT", {})
btc = balance.get("BTC", {})
print(f"USDT: free={usdt.get('free', 0)}, total={usdt.get('total', 0)}")
print(f"BTC:  free={btc.get('free', 0)}, total={btc.get('total', 0)}")

# 取得 BTC 現價
symbol = "BTC/USDT"
ticker = exchange.fetch_ticker(symbol)
price = ticker["last"]
print(f"\n{symbol} price: ${price:,.2f}")

# 下單: 買 $10 USDT 的 BTC
quantity = round(10.0 / price, 5)
print(f"\n--- Placing BUY order ---")
print(f"Quantity: {quantity} BTC (~$10 USDT)")

try:
    order = exchange.create_market_buy_order(symbol, quantity)
    print(f"\nOrder placed!")
    print(f"  ID: {order['id']}")
    print(f"  Symbol: {order['symbol']}")
    print(f"  Side: {order['side']}")
    print(f"  Type: {order['type']}")
    print(f"  Amount: {order['amount']}")
    print(f"  Filled: {order['filled']}")
    print(f"  Price: {order.get('average', order.get('price', 'N/A'))}")
    print(f"  Status: {order['status']}")
    print(f"  Cost: {order.get('cost', 'N/A')}")
except Exception as e:
    print(f"Order failed: {e}")

# 查餘額（下單後）
print("\n--- Balance after order ---")
balance = exchange.fetch_balance()
usdt = balance.get("USDT", {})
btc = balance.get("BTC", {})
print(f"USDT: free={usdt.get('free', 0)}, total={usdt.get('total', 0)}")
print(f"BTC:  free={btc.get('free', 0)}, total={btc.get('total', 0)}")
