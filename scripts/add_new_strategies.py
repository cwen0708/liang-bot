"""在 Supabase bot_config 中加入 vwap_reversion + ema_ribbon 策略（現貨 + 合約）。"""
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
    print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY 未設定")
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
    print("ERROR: bot_config 表為空！")
    sys.exit(1)

current_ver = resp.data[0]["version"]
cfg = resp.data[0]["config_json"]

print(f"當前版本: v{current_ver}")

# 2. 定義新策略條目
NEW_SPOT_STRATEGIES = [
    # VWAP Reversion × 4 時段
    {"name": "vwap_reversion", "timeframe": "15m", "params": {"period": 20, "band_mult": 1.0}},
    {"name": "vwap_reversion", "timeframe": "1h", "params": {"period": 20, "band_mult": 1.0}},
    {"name": "vwap_reversion", "timeframe": "4h", "params": {"period": 20, "band_mult": 1.0}},
    {"name": "vwap_reversion", "timeframe": "1d", "params": {"period": 20, "band_mult": 1.0}},
    # EMA Ribbon × 4 時段
    {"name": "ema_ribbon", "timeframe": "15m", "params": {"periods": [8, 13, 21, 34]}},
    {"name": "ema_ribbon", "timeframe": "1h", "params": {"periods": [8, 13, 21, 34]}},
    {"name": "ema_ribbon", "timeframe": "4h", "params": {"periods": [8, 13, 21, 34]}},
    {"name": "ema_ribbon", "timeframe": "1d", "params": {"periods": [8, 13, 21, 34]}},
]

NEW_FUTURES_STRATEGIES = [
    # VWAP Reversion × 4 時段
    {"name": "vwap_reversion", "timeframe": "15m", "params": {"period": 20, "band_mult": 1.0}},
    {"name": "vwap_reversion", "timeframe": "1h", "params": {"period": 20, "band_mult": 1.0}},
    {"name": "vwap_reversion", "timeframe": "4h", "params": {"period": 20, "band_mult": 1.0}},
    {"name": "vwap_reversion", "timeframe": "1d", "params": {"period": 20, "band_mult": 1.0}},
    # EMA Ribbon × 4 時段
    {"name": "ema_ribbon", "timeframe": "15m", "params": {"periods": [8, 13, 21, 34]}},
    {"name": "ema_ribbon", "timeframe": "1h", "params": {"periods": [8, 13, 21, 34]}},
    {"name": "ema_ribbon", "timeframe": "4h", "params": {"periods": [8, 13, 21, 34]}},
    {"name": "ema_ribbon", "timeframe": "1d", "params": {"periods": [8, 13, 21, 34]}},
]

# 3. 更新現貨策略清單
spot_strategies = cfg.get("strategies", [])
existing_spot_names = {(s.get("name"), s.get("timeframe")) for s in spot_strategies}
added_spot = 0
for s in NEW_SPOT_STRATEGIES:
    key_tuple = (s["name"], s["timeframe"])
    if key_tuple not in existing_spot_names:
        spot_strategies.append(s)
        added_spot += 1
cfg["strategies"] = spot_strategies

print(f"\n現貨策略: 原有 {len(existing_spot_names)} 條，新增 {added_spot} 條，共 {len(spot_strategies)} 條")
for s in spot_strategies:
    tf = s.get("timeframe", "(none)")
    print(f"  - {s['name']}: {tf}")

# 4. 更新合約策略清單
futures_cfg = cfg.get("futures", {})
if futures_cfg:
    futures_strategies = futures_cfg.get("strategies", [])
    existing_futures_names = {(s.get("name"), s.get("timeframe")) for s in futures_strategies}
    added_futures = 0
    for s in NEW_FUTURES_STRATEGIES:
        key_tuple = (s["name"], s["timeframe"])
        if key_tuple not in existing_futures_names:
            futures_strategies.append(s)
            added_futures += 1
    futures_cfg["strategies"] = futures_strategies
    cfg["futures"] = futures_cfg

    print(f"\n合約策略: 原有 {len(existing_futures_names)} 條，新增 {added_futures} 條，共 {len(futures_strategies)} 條")
    for s in futures_strategies:
        tf = s.get("timeframe", "(none)")
        print(f"  - {s['name']}: {tf}")
else:
    print("\n⚠ 無 futures 配置，跳過合約策略更新")

# 5. 推送新版本
new_ver = current_ver + 1
client.table("bot_config").insert({
    "version": new_ver,
    "config_json": cfg,
    "changed_by": "add_strategies_script",
    "change_note": f"add vwap_reversion + ema_ribbon (4 timeframes each) to spot & futures",
}).execute()

print(f"\n✅ 已推送 v{new_ver}")
