"""交易訊號模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    SHORT = "SHORT"    # 開空（合約專用）
    COVER = "COVER"    # 平空（合約專用）


@dataclass
class TradeSignal:
    signal: Signal
    symbol: str
    timestamp: datetime
    price: float
    indicators: dict = field(default_factory=dict)
    confidence: float = 1.0
    order_flow_context: dict = field(default_factory=dict)


@dataclass
class StrategyVerdict:
    """
    策略結論報告 — 每個策略輸出的統一格式。

    策略不直接決定交易，而是輸出結論讓 LLM 或加權投票做最終決策。
    """

    strategy_name: str
    signal: Signal
    confidence: float          # 0.0 ~ 1.0
    reasoning: str             # 文字說明（為什麼這樣判斷）
    timeframe: str = ""        # 策略使用的 K 線時間框架
    key_evidence: list[str] = field(default_factory=list)
    indicators: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
