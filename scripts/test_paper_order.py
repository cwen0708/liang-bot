"""測試 paper 模式下單流程。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from bot.config.settings import Settings
from bot.config.constants import TradingMode
from bot.exchange.binance_client import BinanceClient
from bot.execution.executor import OrderExecutor
from bot.db.supabase_client import SupabaseWriter
from bot.risk.manager import RiskOutput
from bot.strategy.signals import Signal

# 載入設定
settings = Settings.load()
print(f"Mode: {settings.trading.mode}")
print(f"Pairs: {settings.trading.pairs}")

# 初始化
exchange = BinanceClient(settings.exchange)
executor = OrderExecutor(exchange, mode=TradingMode.PAPER)
db = SupabaseWriter()

# 取得 BTC 現價
symbol = "BTC/USDT"
ticker = exchange.get_ticker(symbol)
price = ticker["last"]
print(f"\n{symbol} price: ${price:,.2f}")

# 模擬風控輸出: 用 $5 USDT 買入
quantity = 5.0 / price
risk_output = RiskOutput(
    approved=True,
    quantity=quantity,
    stop_loss_price=price * 0.97,
    take_profit_price=price * 1.06,
    reason="test paper order",
)

print(f"Quantity: {quantity:.8f} BTC (${quantity * price:.2f} USDT)")
print(f"SL: ${risk_output.stop_loss_price:,.2f} / TP: ${risk_output.take_profit_price:,.2f}")

# 執行 paper 下單
print("\n--- Executing paper BUY ---")
order = executor.execute(Signal.BUY, symbol, risk_output)

if order:
    print(f"Order ID: {order['id']}")
    print(f"Side: {order['side']}")
    print(f"Price: ${order['price']:,.2f}")
    print(f"Amount: {order['amount']:.8f}")
    print(f"Status: {order['status']}")

    # 寫入 Supabase orders 表
    db.insert_order(
        order=order,
        mode="paper",
        cycle_id="test_paper_001",
        market_type="spot",
    )
    print("\nOrder written to Supabase!")
else:
    print("Order failed or skipped")
