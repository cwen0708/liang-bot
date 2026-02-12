"""Testnet 監控腳本 — 每 30 分鐘查 Supabase，共 10 次。"""
import os, sys, time
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
sb = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))

INTERVAL = 30 * 60
CHECKS = 10

def check(n):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{'='*60}", flush=True)
    print(f"[#{n}] {now}", flush=True)
    print(f"{'='*60}", flush=True)

    # Bot status
    res = sb.table("bot_status").select("*").order("updated_at", desc=True).limit(1).execute()
    if res.data:
        s = res.data[0]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(s["updated_at"])).total_seconds()
        tag = "ONLINE" if age < 300 else f"OFFLINE ({int(age)}s)"
        print(f"  Bot: {tag} | cycle={s['cycle_num']} | uptime={s['uptime_sec']//60}min", flush=True)

    # Futures orders (latest 5)
    res = sb.table("orders").select("*").eq("market_type", "futures").order("created_at", desc=True).limit(5).execute()
    testnet_count = sum(1 for o in res.data if not o["exchange_id"].startswith("paper_"))
    paper_count = len(res.data) - testnet_count
    print(f"  Futures orders: {testnet_count} TESTNET / {paper_count} PAPER (latest 5)", flush=True)
    for o in res.data:
        eid = o["exchange_id"]
        tag = "TESTNET" if not eid.startswith("paper_") else "PAPER"
        print(f"    [{tag}] {o['side'].upper():4s} {o['symbol']:12s} qty={o['quantity']:.6f} @ {o['price']} | {o['created_at'][:19]}", flush=True)

    # Spot orders (latest 3)
    res = sb.table("orders").select("*").eq("market_type", "spot").order("created_at", desc=True).limit(3).execute()
    testnet_count = sum(1 for o in res.data if not o["exchange_id"].startswith("paper_"))
    paper_count = len(res.data) - testnet_count
    print(f"  Spot orders: {testnet_count} TESTNET / {paper_count} PAPER (latest 3)", flush=True)
    for o in res.data:
        eid = o["exchange_id"]
        tag = "TESTNET" if not eid.startswith("paper_") else "PAPER"
        print(f"    [{tag}] {o['side'].upper():4s} {o['symbol']:12s} @ {o['price']} | {o['created_at'][:19]}", flush=True)

    # Futures margin
    res = sb.table("futures_margin").select("*").order("created_at", desc=True).limit(1).execute()
    if res.data:
        m = res.data[0]
        print(f"  Margin: wallet={m['total_wallet_balance']:.2f} ratio={m['margin_ratio']:.4f} mode={m.get('mode','?')}", flush=True)

    # Recent errors (last 30 min)
    res = sb.table("bot_logs").select("level,message,created_at").eq("level", "ERROR").order("created_at", desc=True).limit(3).execute()
    if res.data:
        print(f"  Errors: {len(res.data)} recent", flush=True)
        for l in res.data:
            msg = l["message"].split("\n")[0][:100]
            print(f"    {l['created_at'][:19]} {msg}", flush=True)
    else:
        print(f"  Errors: none", flush=True)
    print(flush=True)

if __name__ == "__main__":
    print(f"Testnet monitor: {CHECKS}x every {INTERVAL//60}min", flush=True)
    for i in range(1, CHECKS + 1):
        try:
            check(i)
        except Exception as e:
            print(f"  Check failed: {e}", flush=True)
        if i < CHECKS:
            time.sleep(INTERVAL)
    print("Monitor done.", flush=True)
