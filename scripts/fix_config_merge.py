"""緊急修復：從 Supabase v22 恢復完整配置，只更新 strategies 的 timeframe。"""
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

# 1. 讀取 v22（推送前的版本，含完整 futures 等配置）
resp = (
    client.table("bot_config")
    .select("version, config_json")
    .order("version", desc=True)
    .limit(5)
    .execute()
)

print("最近版本:")
for row in resp.data:
    v = row["version"]
    cfg = row["config_json"]
    has_futures = "futures" in cfg
    strats = cfg.get("strategies", [])
    strat_names = [s.get("name", "?") for s in strats]
    has_tf = any(s.get("timeframe") for s in strats)
    print(f"  v{v}: futures={'Y' if has_futures else 'N'}, strategies={strat_names}, has_timeframe={has_tf}")

# 找到最近一個有 futures 配置的版本
base_cfg = None
base_ver = None
for row in resp.data:
    if "futures" in row["config_json"]:
        base_cfg = row["config_json"]
        base_ver = row["version"]
        break

if base_cfg is None:
    print("ERROR: 找不到含 futures 的配置版本！")
    sys.exit(1)

print(f"\n以 v{base_ver} 為基礎（含 futures 配置）")

# 2. 只更新 strategies 的 timeframe（interval_n → timeframe）
TIMEFRAME_MAP = {
    "sma_crossover": "1h",
    "rsi_oversold": "15m",
    "bollinger_breakout": "4h",
    "macd_momentum": "1d",
}

strategies = base_cfg.get("strategies", [])
for s in strategies:
    name = s.get("name", "")
    # 移除舊的 interval_n
    s.pop("interval_n", None)
    # 加入 timeframe（若有對應）
    if name in TIMEFRAME_MAP:
        s["timeframe"] = TIMEFRAME_MAP[name]

base_cfg["strategies"] = strategies

# 同樣處理 futures.strategies（如果有）
futures_cfg = base_cfg.get("futures", {})
if futures_cfg and futures_cfg.get("strategies"):
    for s in futures_cfg["strategies"]:
        name = s.get("name", "")
        s.pop("interval_n", None)
        if name in TIMEFRAME_MAP:
            s["timeframe"] = TIMEFRAME_MAP[name]

# 3. 推送新版本
current_ver = resp.data[0]["version"]
new_ver = current_ver + 1

client.table("bot_config").insert({
    "version": new_ver,
    "config_json": base_cfg,
    "changed_by": "fix_script",
    "change_note": f"restore from v{base_ver} + add per-strategy timeframe",
}).execute()

print(f"\nOK: pushed v{new_ver} (based on v{base_ver})")
print(f"  futures.enabled = {futures_cfg.get('enabled', 'N/A')}")
print(f"  futures.pairs = {futures_cfg.get('pairs', [])}")
print(f"  strategies:")
for s in strategies:
    tf = s.get("timeframe", "(none)")
    print(f"    - {s['name']}: timeframe={tf}")
