"""LLM 決策引擎的 Pydantic 資料模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class PositionInfo(BaseModel):
    """單一持倉資訊。"""

    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    holding_duration: str = ""


class PortfolioState(BaseModel):
    """當前投資組合狀態。"""

    available_balance: float = 0.0
    used_capital_pct: float = 0.0
    positions: list[PositionInfo] = Field(default_factory=list)
    daily_realized_pnl: float = 0.0
    daily_risk_remaining: float = 0.0
    max_positions: int = 3
    current_position_count: int = 0


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

    action: str = Field(description="BUY / SELL / HOLD")
    confidence: float = Field(ge=0.0, le=1.0, description="決策信心度")
    stop_loss_pct: float = Field(default=0.03, description="建議停損百分比")
    take_profit_pct: float = Field(default=0.06, description="建議停利百分比")
    reasoning: str = Field(default="", description="決策推理過程")
    position_size_pct: float = Field(default=0.02, description="建議倉位佔比")
