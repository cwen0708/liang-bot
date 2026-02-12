"""擴展 Supabase bot_config 中舊策略為多時段（與 config.yaml 同步）。"""
import os
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

url = os.getenv("SUPABASE_URL", "")
key = os.getenv("SUPABASE_SERVICE_KEY", "")
if not url or not key:
    print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    sys.exit(1)

from supabase import create_client
client = create_client(url, key)

# 1. 讀取最新配置
resp = (
    client.table("bot_config")
    .select("version, config_json")
    .order("version", desc=True)
    .limit(1)
    .execute()
)
if not resp.data:
    print("ERROR: bot_config empty")
    sys.exit(1)

current_ver = resp.data[0]["version"]
cfg = resp.data[0]["config_json"]
print(f"Current version: v{current_ver}")

# 2. 定義完整的策略清單（與 config.yaml 同步）
TIMEFRAMES = ["15m", "1h", "4h", "1d"]

FULL_SPOT_STRATEGIES = []

# SMA Crossover x4
for tf in TIMEFRAMES:
    FULL_SPOT_STRATEGIES.append({"name": "sma_crossover", "timeframe": tf, "params": {"fast_period": 10, "slow_period": 30}})

# RSI Oversold x4
for tf in TIMEFRAMES:
    FULL_SPOT_STRATEGIES.append({"name": "rsi_oversold", "timeframe": tf, "params": {"period": 14, "oversold": 30, "overbought": 70}})

# Bollinger Breakout x4
for tf in TIMEFRAMES:
    FULL_SPOT_STRATEGIES.append({"name": "bollinger_breakout", "timeframe": tf, "params": {"period": 20, "std_dev": 2.0}})

# MACD Momentum x4
for tf in TIMEFRAMES:
    FULL_SPOT_STRATEGIES.append({"name": "macd_momentum", "timeframe": tf, "params": {"fast_period": 12, "slow_period": 26, "signal_period": 9}})

# VWAP Reversion x4
for tf in TIMEFRAMES:
    FULL_SPOT_STRATEGIES.append({"name": "vwap_reversion", "timeframe": tf, "params": {"period": 20, "band_mult": 1.0}})

# EMA Ribbon x4
for tf in TIMEFRAMES:
    FULL_SPOT_STRATEGIES.append({"name": "ema_ribbon", "timeframe": tf, "params": {"periods": [8, 13, 21, 34]}})

# Order Flow (no timeframe)
FULL_SPOT_STRATEGIES.append({"name": "tia_orderflow", "params": {"signal_threshold": 0.5}})

# 3. 合約策略（同樣 6 策略 x 4 時段，無 orderflow）
FULL_FUTURES_STRATEGIES = []
for tf in TIMEFRAMES:
    FULL_FUTURES_STRATEGIES.append({"name": "sma_crossover", "timeframe": tf, "params": {"fast_period": 10, "slow_period": 30}})
for tf in TIMEFRAMES:
    FULL_FUTURES_STRATEGIES.append({"name": "rsi_oversold", "timeframe": tf, "params": {"period": 14, "oversold": 30, "overbought": 70}})
for tf in TIMEFRAMES:
    FULL_FUTURES_STRATEGIES.append({"name": "bollinger_breakout", "timeframe": tf, "params": {"period": 20, "std_dev": 2.0}})
for tf in TIMEFRAMES:
    FULL_FUTURES_STRATEGIES.append({"name": "macd_momentum", "timeframe": tf, "params": {"fast_period": 12, "slow_period": 26, "signal_period": 9}})
for tf in TIMEFRAMES:
    FULL_FUTURES_STRATEGIES.append({"name": "vwap_reversion", "timeframe": tf, "params": {"period": 20, "band_mult": 1.0}})
for tf in TIMEFRAMES:
    FULL_FUTURES_STRATEGIES.append({"name": "ema_ribbon", "timeframe": tf, "params": {"periods": [8, 13, 21, 34]}})

# 4. 替換
cfg["strategies"] = FULL_SPOT_STRATEGIES

futures_cfg = cfg.get("futures", {})
if futures_cfg:
    futures_cfg["strategies"] = FULL_FUTURES_STRATEGIES
    cfg["futures"] = futures_cfg

# 5. 推送
new_ver = current_ver + 1
client.table("bot_config").insert({
    "version": new_ver,
    "config_json": cfg,
    "changed_by": "expand_timeframes_script",
    "change_note": f"expand all 6 strategies to 4 timeframes each (spot 25, futures 24)",
}).execute()

print(f"\nSpot strategies: {len(FULL_SPOT_STRATEGIES)} entries")
for s in FULL_SPOT_STRATEGIES:
    tf = s.get("timeframe", "(none)")
    print(f"  - {s['name']}: {tf}")

print(f"\nFutures strategies: {len(FULL_FUTURES_STRATEGIES)} entries")
for s in FULL_FUTURES_STRATEGIES:
    tf = s.get("timeframe", "(none)")
    print(f"  - {s['name']}: {tf}")

print(f"\nPushed v{new_ver}")
