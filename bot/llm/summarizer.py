"""策略結論 + 倉位狀態 + 風控指標 → Markdown 摘要。"""

from __future__ import annotations

from bot.llm.schemas import PortfolioState
from bot.risk.metrics import RiskMetrics
from bot.strategy.signals import StrategyVerdict


def summarize_verdicts(verdicts: list[StrategyVerdict]) -> str:
    """將各策略的結論轉為結構化 Markdown。"""
    if not verdicts:
        return "## 策略結論\n無可用的策略分析結果。\n"

    sections = ["## 策略結論\n"]

    for i, v in enumerate(verdicts, 1):
        section = f"""### 策略 {i}: {v.strategy_name}
- **建議**: {v.signal.value} (信心 {v.confidence:.2f})
- **推理**: {v.reasoning}
"""
        if v.key_evidence:
            section += "- **關鍵證據**:\n"
            for ev in v.key_evidence:
                section += f"  - {ev}\n"

        if v.indicators:
            section += "- **指標快照**: "
            indicator_parts = [f"{k}={v_val:.4f}" if isinstance(v_val, float) else f"{k}={v_val}"
                               for k, v_val in v.indicators.items()]
            section += ", ".join(indicator_parts[:8])  # 最多顯示 8 個
            section += "\n"

        sections.append(section)

    return "\n".join(sections)


def summarize_portfolio(state: PortfolioState) -> str:
    """將倉位狀態轉為 Markdown。"""
    lines = [
        "## 當前倉位狀態\n",
        f"- 可用 USDT 餘額: {state.available_balance:,.2f}",
        f"- 已用資金比例: {state.used_capital_pct:.1%}",
        f"- 最大可用持倉數: {state.max_positions} (已用 {state.current_position_count}/{state.max_positions})",
        f"- 今日已實現損益: {state.daily_realized_pnl:+.2f} USDT",
        f"- 今日剩餘風險額度: {state.daily_risk_remaining:,.2f} USDT",
    ]

    # 合約專用資訊
    if state.market_type == "futures":
        lines.append(f"- 保證金餘額: {state.margin_balance:,.2f} USDT")
        lines.append(f"- 保證金比率: {state.margin_ratio:.1%}")
        lines.append(f"- 槓桿倍數: {state.leverage}x")
        if state.funding_rate:
            lines.append(f"- 資金費率: {state.funding_rate:+.4%}")

    if state.positions:
        if state.market_type == "futures":
            lines.append("\n| 幣對 | 方向 | 槓桿 | 數量 | 入場價 | 現價 | 未實現損益 | 清算價 |")
            lines.append("|------|------|------|------|--------|------|-----------|--------|")
            for pos in state.positions:
                liq = f"{pos.liquidation_price:,.2f}" if pos.liquidation_price else "N/A"
                lines.append(
                    f"| {pos.symbol} | {pos.side.upper()} | {pos.leverage}x | "
                    f"{pos.quantity:.4f} | {pos.entry_price:,.2f} | {pos.current_price:,.2f} | "
                    f"{pos.unrealized_pnl:+.2f} ({pos.unrealized_pnl_pct:+.1%}) | {liq} |"
                )
        else:
            lines.append("\n| 幣對 | 數量 | 入場價 | 現價 | 未實現損益 | 持倉時間 |")
            lines.append("|------|------|--------|------|-----------|----------|")
            for pos in state.positions:
                lines.append(
                    f"| {pos.symbol} | {pos.quantity:.4f} | "
                    f"{pos.entry_price:,.2f} | {pos.current_price:,.2f} | "
                    f"{pos.unrealized_pnl:+.2f} ({pos.unrealized_pnl_pct:+.1%}) | "
                    f"{pos.holding_duration} |"
                )
    else:
        lines.append("\n目前無持倉。")

    return "\n".join(lines)


def summarize_risk_metrics(
    metrics: RiskMetrics,
    symbol: str,
    current_price: float,
) -> str:
    """將預計算的風控指標轉為 Markdown（供 LLM 參考）。"""
    lines = ["## 風控指標評估\n"]

    # ATR + SL/TP + R:R
    sl_pct = metrics.sl_distance / current_price if current_price > 0 else 0
    tp_pct = metrics.tp_distance / current_price if current_price > 0 else 0
    mode = "ATR 動態" if metrics.atr_used else "固定百分比"

    lines.append(f"### 停損停利（{mode}）")
    lines.append(f"- 停損價: {metrics.stop_loss_price:,.2f} (-{sl_pct:.2%})")
    lines.append(f"- 停利價: {metrics.take_profit_price:,.2f} (+{tp_pct:.2%})")
    lines.append(f"- 盈虧比 (R:R): {metrics.risk_reward_ratio:.2f}")
    if metrics.atr_used:
        lines.append(f"- ATR: {metrics.atr_value:,.2f}")

    # Fibonacci
    fib = metrics.fib_levels
    if fib:
        lines.append("\n### Fibonacci 回撤位")
        fib_parts = []
        for ratio in ["0.236", "0.382", "0.500", "0.618", "0.786"]:
            if ratio in fib:
                fib_parts.append(f"{ratio}: {fib[ratio]:,.2f}")
        if fib_parts:
            lines.append("- " + " | ".join(fib_parts))

    # 支撐壓力位
    if metrics.support_levels or metrics.resistance_levels:
        lines.append("\n### 支撐壓力位")
        if metrics.support_levels:
            levels = ", ".join(f"{p:,.2f}" for p in metrics.support_levels)
            lines.append(f"- 支撐: {levels}")
        if metrics.resistance_levels:
            levels = ", ".join(f"{p:,.2f}" for p in metrics.resistance_levels)
            lines.append(f"- 壓力: {levels}")

    # 布林帶
    if metrics.bb_upper > 0:
        lines.append("\n### 布林帶")
        lines.append(
            f"- 上軌: {metrics.bb_upper:,.2f} | "
            f"中軌: {metrics.bb_mid:,.2f} | "
            f"下軌: {metrics.bb_lower:,.2f}"
        )
        if metrics.bb_pct_b <= 0.2:
            bb_hint = "接近下軌（超賣區）"
        elif metrics.bb_pct_b >= 0.8:
            bb_hint = "接近上軌（超買區）"
        else:
            bb_hint = "中間區域"
        lines.append(f"- %B: {metrics.bb_pct_b:.2f}（{bb_hint}）")

    # 合約專用
    if metrics.leverage > 1:
        lines.append("\n### 合約風險")
        lines.append(f"- 槓桿: {metrics.leverage}x")
        lines.append(f"- 預估清算價: {metrics.liquidation_price:,.2f}")
        lines.append(f"- 帳戶風險: {metrics.account_risk_pct:.2%} (單筆)")

    # 警告
    if not metrics.passes_min_rr or metrics.reason:
        lines.append(f"\n⚠️ **風控警告**: {metrics.reason}")

    return "\n".join(lines)
