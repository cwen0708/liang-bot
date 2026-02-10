"""TiaBTC 訂單流策略 — 組合 CVD 背離、SFP、吸收、受困交易者評分。"""

import json
import os
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from bot.config.settings import OrderFlowConfig
from bot.data.models import FootprintLevel, OrderFlowBar
from bot.logging_config import get_logger
from bot.orderflow.absorption import AbsorptionDetector, AbsorptionDirection
from bot.orderflow.divergence import DivergenceDetector, DivergenceType
from bot.orderflow.indicators import OrderFlowIndicatorEngine
from bot.orderflow.sfp import SFPDetector, SFPDirection
from bot.orderflow.trapped import TrappedSide, TrappedTraderAnalyzer
from bot.strategy.base import OrderFlowStrategy
from bot.strategy.signals import Signal, StrategyVerdict

# 快取目錄
_CACHE_DIR = Path(os.getenv("BOT_DATA_DIR", "data")) / "orderflow_cache"

logger = get_logger("strategy.tia_orderflow")

# 組件權重
WEIGHTS = {
    "cvd_divergence": 0.40,
    "sfp": 0.30,
    "absorption": 0.20,
    "trapped": 0.10,
}


class TiaBTCOrderFlowStrategy(OrderFlowStrategy):
    """
    TiaBTC 訂單流策略。

    組合四個偵測器的評分，輸出 StrategyVerdict：
    - CVD 背離 (40%)
    - SFP (30%)
    - 吸收 (20%)
    - 受困交易者 (10%)
    """

    def __init__(self, params: dict) -> None:
        super().__init__(params)
        config = OrderFlowConfig(**{k: v for k, v in params.items() if k in OrderFlowConfig.__dataclass_fields__})

        self.signal_threshold = config.signal_threshold
        self._indicator_engine = OrderFlowIndicatorEngine(
            max_history=config.cvd_lookback,
            zscore_lookback=config.zscore_lookback,
        )
        self._divergence_detector = DivergenceDetector(
            peak_order=config.divergence_peak_order,
        )
        self._sfp_detector = SFPDetector(
            swing_lookback=config.sfp_swing_lookback,
        )
        self._absorption_detector = AbsorptionDetector(
            lookback=config.absorption_lookback,
        )
        self._trapped_analyzer = TrappedTraderAnalyzer()

        self._bars: deque[OrderFlowBar] = deque(maxlen=config.cvd_lookback)
        self._last_verdict: StrategyVerdict | None = None
        self._max_cache_bars = config.cvd_lookback  # 快取最多保留的 bar 數量

    @property
    def name(self) -> str:
        return "tia_orderflow"

    @property
    def required_bars(self) -> int:
        return 30

    def load_cache(self, symbol: str) -> int:
        """從本地快取載入歷史 bars 並 replay 進 indicator engine。返回載入數量。"""
        cache_file = _CACHE_DIR / f"{symbol.replace('/', '_')}.json"
        if not cache_file.exists():
            return 0

        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            count = 0
            for item in raw:
                bar = _bar_from_dict(item)
                self._bars.append(bar)
                self._indicator_engine.on_bar(bar)
                count += 1
            if count > 0:
                logger.info("[%s] 從快取載入 %d 根 K 線 (%s)", self.name, count, symbol)
            return count
        except Exception as e:
            logger.warning("[%s] 載入快取失敗: %s", self.name, e)
            return 0

    def _save_cache(self, symbol: str) -> None:
        """將當前 bars 寫入本地快取。"""
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file = _CACHE_DIR / f"{symbol.replace('/', '_')}.json"
            data = [_bar_to_dict(b) for b in self._bars]
            cache_file.write_text(
                json.dumps(data, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("[%s] 儲存快取失敗: %s", self.name, e)

    def on_bar(self, symbol: str, bar: OrderFlowBar) -> StrategyVerdict:
        """接收新 K 線，計算指標並產生結論。"""
        self._bars.append(bar)
        indicators = self._indicator_engine.on_bar(bar)

        # 每次新 bar 都更新快取（很快，只是寫一個小 JSON）
        self._save_cache(symbol)

        if len(self._bars) < self.required_bars:
            verdict = StrategyVerdict(
                strategy_name=self.name,
                signal=Signal.HOLD,
                confidence=0.0,
                reasoning=f"數據不足 ({len(self._bars)}/{self.required_bars})，等待更多 K 線",
                indicators=indicators,
                timestamp=bar.timestamp,
            )
            self._last_verdict = verdict
            return verdict

        bullish_score, bearish_score, evidence = self._calculate_scores()

        # 決定訊號
        signal = Signal.HOLD
        confidence = 0.0

        if bullish_score >= self.signal_threshold and bullish_score > bearish_score:
            signal = Signal.BUY
            confidence = bullish_score
        elif bearish_score >= self.signal_threshold and bearish_score > bullish_score:
            signal = Signal.SELL
            confidence = bearish_score

        reasoning = self._build_reasoning(bullish_score, bearish_score, signal)

        verdict = StrategyVerdict(
            strategy_name=self.name,
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            key_evidence=evidence,
            indicators={
                **indicators,
                "bullish_score": bullish_score,
                "bearish_score": bearish_score,
            },
            timestamp=bar.timestamp,
        )
        self._last_verdict = verdict
        return verdict

    def latest_verdict(self, symbol: str) -> StrategyVerdict | None:
        """回傳最近一次 on_bar 的結論。"""
        return self._last_verdict

    def reset(self) -> None:
        self._bars.clear()
        self._indicator_engine.reset()
        self._last_verdict = None

    def _calculate_scores(self) -> tuple[float, float, list[str]]:
        """計算看漲/看跌綜合評分。"""
        bullish = 0.0
        bearish = 0.0
        evidence: list[str] = []

        # 1. CVD 背離 (40%)
        b, s, ev = self._score_divergence()
        bullish += b * WEIGHTS["cvd_divergence"]
        bearish += s * WEIGHTS["cvd_divergence"]
        evidence.extend(ev)

        # 2. SFP (30%)
        b, s, ev = self._score_sfp()
        bullish += b * WEIGHTS["sfp"]
        bearish += s * WEIGHTS["sfp"]
        evidence.extend(ev)

        # 3. 吸收 (20%)
        b, s, ev = self._score_absorption()
        bullish += b * WEIGHTS["absorption"]
        bearish += s * WEIGHTS["absorption"]
        evidence.extend(ev)

        # 4. 受困交易者 (10%)
        b, s, ev = self._score_trapped()
        bullish += b * WEIGHTS["trapped"]
        bearish += s * WEIGHTS["trapped"]
        evidence.extend(ev)

        return bullish, bearish, evidence

    def _score_divergence(self) -> tuple[float, float, list[str]]:
        prices = self._indicator_engine.prices
        cvd_values = self._indicator_engine.cvd.values
        divergences = self._divergence_detector.detect(prices, cvd_values)

        bullish = 0.0
        bearish = 0.0
        evidence = []

        for div in divergences:
            if div.divergence_type in (DivergenceType.REGULAR_BULLISH, DivergenceType.HIDDEN_BULLISH):
                bullish = max(bullish, div.strength)
                evidence.append(f"CVD {div.divergence_type.value} (強度 {div.strength:.2f})")
            elif div.divergence_type in (DivergenceType.REGULAR_BEARISH, DivergenceType.HIDDEN_BEARISH):
                bearish = max(bearish, div.strength)
                evidence.append(f"CVD {div.divergence_type.value} (強度 {div.strength:.2f})")

        return bullish, bearish, evidence

    def _score_sfp(self) -> tuple[float, float, list[str]]:
        highs = self._indicator_engine.highs
        lows = self._indicator_engine.lows
        closes = self._indicator_engine.prices
        sfp_events = self._sfp_detector.detect(highs, lows, closes)

        bullish = 0.0
        bearish = 0.0
        evidence = []

        for event in sfp_events:
            if event.direction == SFPDirection.BULLISH:
                bullish = max(bullish, event.strength)
                evidence.append(f"SFP 看漲 @ {event.swing_price:.2f} (強度 {event.strength:.2f})")
            else:
                bearish = max(bearish, event.strength)
                evidence.append(f"SFP 看跌 @ {event.swing_price:.2f} (強度 {event.strength:.2f})")

        return bullish, bearish, evidence

    def _score_absorption(self) -> tuple[float, float, list[str]]:
        prices = self._indicator_engine.prices
        cvd_values = self._indicator_engine.cvd.values
        event = self._absorption_detector.detect(prices, cvd_values)

        if event is None:
            return 0.0, 0.0, []

        if event.direction == AbsorptionDirection.BULLISH:
            return event.strength, 0.0, [f"看漲吸收 (強度 {event.strength:.2f})"]
        else:
            return 0.0, event.strength, [f"看跌吸收 (強度 {event.strength:.2f})"]

    def _score_trapped(self) -> tuple[float, float, list[str]]:
        bars = list(self._bars)
        events = self._trapped_analyzer.detect(bars)

        bullish = 0.0
        bearish = 0.0
        evidence = []

        for event in events:
            if event.side == TrappedSide.TRAPPED_SHORTS:
                bullish = max(bullish, event.strength)
                evidence.append(f"空方受困 @ {event.trap_price:.2f}")
            else:
                bearish = max(bearish, event.strength)
                evidence.append(f"多方受困 @ {event.trap_price:.2f}")

        return bullish, bearish, evidence

    @staticmethod
    def _build_reasoning(
        bullish_score: float, bearish_score: float, signal: Signal
    ) -> str:
        parts = [f"看漲分: {bullish_score:.3f}, 看跌分: {bearish_score:.3f}"]
        if signal == Signal.BUY:
            parts.append("看漲分超過閾值且高於看跌分 → 買入")
        elif signal == Signal.SELL:
            parts.append("看跌分超過閾值且高於看漲分 → 賣出")
        else:
            parts.append("分數未達閾值 → 持有觀望")
        return "。".join(parts)


# ── 快取序列化 helpers ──────────────────────────────────────────

def _bar_to_dict(bar: OrderFlowBar) -> dict:
    """OrderFlowBar → JSON-safe dict（不含 footprint，太大且不影響策略）。"""
    return {
        "ts": bar.timestamp.isoformat(),
        "o": bar.open,
        "h": bar.high,
        "l": bar.low,
        "c": bar.close,
        "v": bar.volume,
        "bv": bar.buy_volume,
        "sv": bar.sell_volume,
        "tc": bar.trade_count,
        "vw": bar.vwap,
    }


def _bar_from_dict(d: dict) -> OrderFlowBar:
    """dict → OrderFlowBar。"""
    return OrderFlowBar(
        timestamp=datetime.fromisoformat(d["ts"]),
        open=d["o"],
        high=d["h"],
        low=d["l"],
        close=d["c"],
        volume=d["v"],
        buy_volume=d["bv"],
        sell_volume=d["sv"],
        trade_count=d["tc"],
        vwap=d["vw"],
    )
