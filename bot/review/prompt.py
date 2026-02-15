"""復盤專用 Prompt 構建。"""

from __future__ import annotations

from bot.review.collector import ReviewData, WeeklyStats

REVIEW_SYSTEM_PROMPT = """你是一位資深加密貨幣交易系統審計員。你的任務是對自動化交易機器人的每日表現進行全面復盤，找出問題、肯定優點，並提出可執行的改進建議。

## 你的角色

你是交易系統的**外部審計者**，而非交易決策者。你需要客觀、嚴謹地分析：
1. 策略信號是否有效
2. AI 決策是否合理
3. 風控執行是否到位
4. 系統配置是否需要調整
5. 交易決策 Prompt 本身是否有改進空間

## 評估維度

### 1. 策略準確率 (strategy_accuracy)
- 各策略在不同時間框架的信號品質
- 信號是否與後續走勢一致
- 策略間的矛盾頻率
- 是否有策略持續無效，應考慮停用

### 2. 風控執行 (risk_execution)
- 停損停利設定是否合理
- 倉位大小是否適當
- 每日虧損限制是否觸及
- 是否有應攔截卻放行、或應放行卻攔截的情況

### 3. 損益表現 (pnl_performance)
- 整體盈虧
- 勝率與盈虧比
- 平均持倉時間是否合理
- 是否有明顯可改進的出入場時機

### 4. Prompt 品質 (prompt_quality)
- 決策 Prompt 的規則是否清晰
- 是否有遺漏的重要場景
- 信心度門檻和覆蓋規則是否合理
- 多時間框架整合邏輯是否完善

## 回傳格式

你必須在回答的最後輸出一個 JSON 區塊：

```json
{
  "summary": "完整的 Markdown 格式復盤報告（包含標題、分段、重點標記）",
  "scores": {
    "strategy_accuracy": 0.65,
    "risk_execution": 0.80,
    "pnl_performance": 0.45,
    "prompt_quality": 0.70,
    "overall": 0.65
  },
  "suggestions": [
    {
      "category": "strategy",
      "priority": "high",
      "title": "建議標題",
      "detail": "詳細說明",
      "action": "具體可執行動作"
    }
  ]
}
```

### 評分標準
- 0.0~0.3: 差（有嚴重問題需要立即修正）
- 0.3~0.5: 不足（有明顯改進空間）
- 0.5~0.7: 尚可（基本正常但可優化）
- 0.7~0.9: 良好（表現穩定，小幅調優即可）
- 0.9~1.0: 優秀（幾乎無需調整）

### suggestions 類別
- `strategy`: 策略相關（停用、新增、參數調整）
- `risk`: 風控相關（停損停利、倉位、每日限額）
- `config`: 系統配置（交易對、時間框架、間隔）
- `prompt`: Prompt 改進（規則調整、新增場景）

### suggestions 優先級
- `high`: 影響盈虧或風險的重大問題，需優先處理
- `medium`: 可改善表現的優化建議
- `low`: 錦上添花的微調
"""


def build_review_prompt(
    data: ReviewData,
    weekly: WeeklyStats,
    trading_prompt: str,
) -> str:
    """組建完整的復盤提示詞。"""
    sections: list[str] = [REVIEW_SYSTEM_PROMPT, "\n---\n"]

    # ── 過去 24 小時數據 ──
    sections.append("# 過去 24 小時數據\n")

    # LLM 決策摘要
    sections.append("## LLM 決策摘要\n")
    if data.decisions:
        by_symbol: dict[str, list[dict]] = {}
        for d in data.decisions:
            by_symbol.setdefault(d["symbol"], []).append(d)
        for symbol, decs in sorted(by_symbol.items()):
            sections.append(f"### {symbol}")
            for d in decs:
                executed = "已執行" if d.get("executed") else "未執行"
                reject = f" ({d['reject_reason']})" if d.get("reject_reason") else ""
                sections.append(
                    f"- {d['action']} 信心={d['confidence']:.0%} {executed}{reject}"
                    f" | 進場={d.get('entry_price', 0):.2f}"
                    f" SL={d.get('stop_loss', 0):.2f}"
                    f" TP={d.get('take_profit', 0):.2f}"
                    f" | {d.get('created_at', '')[:16]}"
                )
                if d.get("reasoning"):
                    # 截斷過長的 reasoning
                    r = d["reasoning"][:200]
                    sections.append(f"  推理: {r}")
            sections.append("")
    else:
        sections.append("過去 24 小時無 LLM 決策。\n")

    # 策略結論統計
    sections.append("## 策略結論統計\n")
    if data.verdicts:
        strat_tf: dict[str, dict[str, dict[str, int]]] = {}
        for v in data.verdicts:
            name = v.get("strategy", "?")
            tf = v.get("timeframe", "?")
            sig = v.get("signal", "HOLD")
            strat_tf.setdefault(name, {}).setdefault(tf, {"BUY": 0, "SELL": 0, "HOLD": 0, "SHORT": 0, "COVER": 0})
            if sig in strat_tf[name][tf]:
                strat_tf[name][tf][sig] += 1

        sections.append("| 策略 | 時段 | BUY | SELL | SHORT | COVER | HOLD |")
        sections.append("|------|------|-----|------|-------|-------|------|")
        for name, tfs in sorted(strat_tf.items()):
            for tf, counts in sorted(tfs.items()):
                sections.append(
                    f"| {name} | {tf} | {counts.get('BUY', 0)} | {counts.get('SELL', 0)}"
                    f" | {counts.get('SHORT', 0)} | {counts.get('COVER', 0)} | {counts.get('HOLD', 0)} |"
                )
        sections.append("")
    else:
        sections.append("過去 24 小時無策略結論。\n")

    # 訂單執行
    sections.append("## 訂單執行\n")
    if data.orders:
        for o in data.orders:
            side = o.get("side", "?")
            status = o.get("status", "?")
            pos_side = o.get("position_side", "")
            lev = o.get("leverage", 1)
            reduce = " [減倉]" if o.get("reduce_only") else ""
            sections.append(
                f"- {o['symbol']} {side.upper()} {pos_side} {lev}x{reduce}"
                f" | 數量={o.get('filled', o.get('quantity', 0)):.4f}"
                f" @ ${o.get('price', 0):.2f}"
                f" | {status} | {o.get('created_at', '')[:16]}"
            )
        sections.append("")
    else:
        sections.append("過去 24 小時無訂單。\n")

    # 當前持倉
    sections.append("## 當前持倉\n")
    if data.positions:
        for p in data.positions:
            qty = p.get("quantity", 0)
            if qty <= 0:
                continue
            pnl = p.get("unrealized_pnl", 0)
            sections.append(
                f"- {p['symbol']} {p.get('side', 'long').upper()} {p.get('leverage', 1)}x"
                f" | 數量={qty:.4f} 入場=${p.get('entry_price', 0):.2f}"
                f" 現價=${p.get('current_price', 0):.2f}"
                f" | PnL={'+'if pnl>=0 else ''}{pnl:.2f}"
                f" | SL=${p.get('stop_loss', 0):.2f} TP=${p.get('take_profit', 0):.2f}"
                f" | horizon={p.get('entry_horizon', 'N/A')}"
            )
        sections.append("")
    else:
        sections.append("目前無持倉。\n")

    # ── 近 7 天趨勢 ──
    sections.append("\n---\n")
    sections.append("# 近 7 天累計統計\n")
    total_closed = weekly.win_count + weekly.loss_count
    win_rate = (weekly.win_count / total_closed * 100) if total_closed > 0 else 0
    sections.append(f"- 總決策數: {weekly.total_decisions}")
    sections.append(f"- 總訂單數: {weekly.total_orders}")
    sections.append(f"- 勝/負: {weekly.win_count}/{weekly.loss_count} (勝率 {win_rate:.0f}%)")
    sections.append(f"- 總損益: {'+'if weekly.total_pnl>=0 else ''}{weekly.total_pnl:.2f} USDT")
    sections.append(f"- 平均信心度: {weekly.avg_confidence:.0%}")
    if weekly.strategy_accuracy:
        sections.append("\n策略信號活躍度:")
        for name, acc in sorted(weekly.strategy_accuracy.items()):
            sections.append(f"- {name}: {acc:.0%} 非 HOLD 信號比率")
    sections.append("")

    # ── 系統配置 ──
    sections.append("\n---\n")
    sections.append("# 系統配置摘要\n")
    if data.config:
        _append_config_summary(sections, data.config)
    else:
        sections.append("無法載入配置。\n")

    # ── 合約保證金 ──
    if data.margin:
        sections.append("\n## 合約保證金\n")
        m = data.margin
        sections.append(f"- 錢包餘額: {m.get('total_wallet_balance', 0):.2f} USDT")
        sections.append(f"- 可用餘額: {m.get('available_balance', 0):.2f} USDT")
        sections.append(f"- 未實現損益: {m.get('total_unrealized_pnl', 0):.2f} USDT")
        sections.append(f"- 保證金比率: {m.get('margin_ratio', 0):.2%}")
        sections.append("")

    # ── 交易決策 Prompt（AI 自省核心）──
    sections.append("\n---\n")
    sections.append("# 當前交易決策 Prompt\n")
    sections.append("以下是 Bot 用來做交易決策的完整 Prompt，請評估其設計品質：\n")
    sections.append("```")
    sections.append(trading_prompt)
    sections.append("```\n")

    # ── 最終指令 ──
    sections.append("\n---\n")
    sections.append(
        "請根據以上數據進行全面復盤分析，然後在最後輸出 JSON 格式的結果"
        "（包含 summary、scores、suggestions）。\n"
    )

    return "\n".join(sections)


def _append_config_summary(sections: list[str], config: dict) -> None:
    """將配置中的關鍵參數加入 sections。"""
    spot = config.get("spot", {})
    if spot:
        sections.append(f"- 現貨交易對: {spot.get('pairs', [])}")
        sections.append(f"- 時間框架: {spot.get('timeframe', '15m')}")
        sections.append(f"- 模式: {spot.get('mode', 'live')}")
        sections.append(f"- 停損: {spot.get('stop_loss_pct', 0.03):.1%}")
        sections.append(f"- 停利: {spot.get('take_profit_pct', 0.06):.1%}")
        sections.append(f"- 最大持倉: {spot.get('max_positions', 3)}")
        sections.append(f"- 每日虧損上限: {spot.get('max_daily_loss_pct', 0.05):.1%}")

    futures = config.get("futures", {})
    if futures and futures.get("enabled"):
        sections.append(f"\n- 合約交易對: {futures.get('pairs', [])}")
        sections.append(f"- 槓桿: {futures.get('leverage', 1)}x")
        sections.append(f"- 合約模式: {futures.get('mode', 'live')}")
        sections.append(f"- 合約最大持倉比: {futures.get('max_position_pct', 0.1):.1%}")

    strategies = config.get("strategies", [])
    if strategies:
        names = [s.get("name", "?") for s in strategies]
        sections.append(f"\n- 策略清單: {', '.join(names)}")

    sections.append("")
