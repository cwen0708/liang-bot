"""一次性腳本：將本地 config.yaml push 到 Supabase bot_config 表。

用法：python scripts/push_config_to_supabase.py
"""
import os
import sys
from pathlib import Path

# 確保能 import bot 模組
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import yaml

config_path = Path(__file__).resolve().parent.parent / "config.yaml"
with open(config_path, "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

url = os.getenv("SUPABASE_URL", "")
key = os.getenv("SUPABASE_SERVICE_KEY", "")
if not url or not key:
    print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_KEY 未設定")
    sys.exit(1)

from supabase import create_client
client = create_client(url, key)

# 取得目前最新版本號
resp = (
    client.table("bot_config")
    .select("version")
    .order("version", desc=True)
    .limit(1)
    .execute()
)
current_version = resp.data[0]["version"] if resp.data else 0
new_version = current_version + 1

# 插入新版本
client.table("bot_config").insert({
    "version": new_version,
    "config_json": cfg,
    "changed_by": "script",
    "change_note": "per-strategy timeframe migration",
}).execute()

print(f"OK: pushed config version {new_version} to Supabase")
print(f"  strategies:")
for s in cfg.get("strategies", []):
    tf = s.get("timeframe", "(none)")
    print(f"    - {s['name']}: timeframe={tf}")
