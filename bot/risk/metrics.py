"""風控預計算指標 — 在 LLM 決策前計算，供 AI 參考。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskMetrics:
    """預計算的風控指標集合。

    在 LLM 決策前計算，提供給 AI 作為交易決策參考。
    這些值僅供參考（advisory），最終風控仍由 evaluate() 強制執行。
    """

    # ── ATR 動態 SL/TP ──
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    sl_distance: float = 0.0
    tp_distance: float = 0.0
    risk_reward_ratio: float = 0.0
    atr_value: float = 0.0
    atr_used: bool = False

    # ── Fibonacci 回撤位 ──
    fib_levels: dict[str, float] = field(default_factory=dict)

    # ── 支撐壓力位 ──
    support_levels: list[float] = field(default_factory=list)
    resistance_levels: list[float] = field(default_factory=list)

    # ── 布林帶 ──
    bb_upper: float = 0.0
    bb_lower: float = 0.0
    bb_mid: float = 0.0
    bb_pct_b: float = 0.5

    # ── 合約專用 ──
    leverage: int = 1
    liquidation_price: float = 0.0
    account_risk_pct: float = 0.0

    # ── 驗證 ──
    passes_min_rr: bool = True
    reason: str = ""
