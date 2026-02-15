"""LLM 決策引擎的 Pydantic 資料模型。"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class PositionInfo(BaseModel):
    """單一持倉資訊。"""

    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    holding_duration: str = ""
    # 合約專用欄位
    side: str = "long"           # "long" / "short"
    leverage: int = 1
    liquidation_price: float | None = None
    market_type: str = "spot"    # "spot" / "futures"
    # 入場上下文（供 LLM 出場判斷參考）
    entry_horizon: str = ""      # 入場時的 horizon (short/medium/long)
    entry_reasoning: str = ""    # 入場時 LLM 的摘要推理


class PortfolioState(BaseModel):
    """當前投資組合狀態。"""

    available_balance: float = 0.0
    used_capital_pct: float = 0.0
    positions: list[PositionInfo] = Field(default_factory=list)
    daily_realized_pnl: float = 0.0
    daily_risk_remaining: float = 0.0
    max_positions: int = 3
    current_position_count: int = 0
    # 合約專用欄位
    market_type: str = "spot"             # "spot" / "futures"
    margin_balance: float = 0.0           # 保證金餘額
    margin_ratio: float = 0.0             # 保證金比率 (0~1)
    funding_rate: float = 0.0             # 資金費率
    leverage: int = 1                     # 槓桿倍數


class OrderFlowSummary(BaseModel):
    """訂單流指標摘要（供 LLM 參考）。"""

    cvd: float = 0.0
    cvd_zscore: float = 0.0
    delta: float = 0.0
    delta_pct: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    vwap: float = 0.0


class LLMDecision(BaseModel):
    """LLM 交易決策結果。"""

    action: str = Field(description="BUY / SELL / HOLD / SHORT / COVER")
    confidence: float = Field(ge=0.0, le=1.0, description="決策信心度")
    entry_price: float | None = Field(default=0.0, description="建議進場價位")
    stop_loss: float | None = Field(default=0.0, description="具體停損價位")
    take_profit: float | None = Field(default=0.0, description="具體停利價位")
    # 保留舊欄位向後相容
    stop_loss_pct: float | None = Field(default=0.0, description="[已棄用] 停損百分比")
    take_profit_pct: float | None = Field(default=0.0, description="[已棄用] 停利百分比")
    reasoning: str | None = Field(default="", description="決策推理過程")
    position_size_pct: float | None = Field(default=0.02, description="建議倉位佔比")
    horizon: str | None = Field(default="medium", description="持倉週期: short/medium/long")

    @model_validator(mode="after")
    def _coerce_none_defaults(self) -> "LLMDecision":
        """LLM 可能回傳 null，統一轉為預設值讓下游安全使用。"""
        defaults = {
            "entry_price": 0.0, "stop_loss": 0.0, "take_profit": 0.0,
            "stop_loss_pct": 0.0, "take_profit_pct": 0.0,
            "position_size_pct": 0.02,
            "reasoning": "", "horizon": "medium",
        }
        for f, default in defaults.items():
            if getattr(self, f) is None:
                object.__setattr__(self, f, default)
        return self
