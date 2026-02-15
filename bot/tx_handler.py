"""台指期（TX）分析處理模組 — 純分析，不執行交易。

透過 Yahoo Finance 抓取台灣加權指數 OHLCV，
套用現有 OHLCV 策略產生信號，寫入 Supabase 供前端顯示。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from bot.config.constants import DataFeedType, TF_MINUTES
from bot.logging_config import get_logger
from bot.strategy.router import StrategyRouter
from bot.strategy.signals import Signal

if TYPE_CHECKING:
    import pandas as pd
    from bot.config.settings import Settings
    from bot.data.fetcher import DataFetcher
    from bot.db.supabase_client import SupabaseWriter
    from bot.strategy.base import Strategy

logger = get_logger("tx_handler")

_L1 = "  "
_L2 = "    "

# 台灣期貨交易時段（UTC+8）
# 日盤：08:45-13:45（週一至週五）
# 夜盤：15:00-05:00 隔日（週一至週五開盤，跨至週二至週六凌晨）
_TZ_UTC8 = timezone(timedelta(hours=8))


def is_tx_session_active() -> bool:
    """檢查當前是否在台灣期貨交易時段（日盤 08:45-13:45 / 夜盤 15:00-05:00 UTC+8）。"""
    now = datetime.now(_TZ_UTC8)
    wd = now.weekday()  # 0=Mon ... 6=Sun
    t = now.hour * 60 + now.minute  # 當天分鐘數

    # 日盤 08:45-13:45（週一到週五）
    if wd < 5 and 525 <= t <= 825:
        return True

    # 夜盤 15:00-23:59（週一到週五下午開盤）
    if wd < 5 and t >= 900:
        return True

    # 夜盤 00:00-05:00（跨日部分，週二到週六凌晨）
    if 0 < wd <= 5 and t < 300:
        return True

    return False


class TXAnalysisHandler:
    """台灣加權指數分析處理器（純分析，不交易）。"""

    def __init__(
        self,
        settings: Settings,
        data_fetcher: DataFetcher,
        db: SupabaseWriter,
    ) -> None:
        self._settings = settings
        self._data_fetcher = data_fetcher
        self._db = db
        self._last_strategy_slot: dict[str, int] = {}

    def process_symbol(
        self,
        symbol: str,
        cycle_id: str,
        cycle: int,
        strategies: list[Strategy],
    ) -> None:
        """對 TX 跑所有 OHLCV 策略，寫入 verdicts 到 Supabase。"""
        display_name = self._settings.tx.display_name
        mode = self._settings.spot.mode.value

        # 只取 OHLCV 策略（排除 orderflow）
        ohlcv_strategies = [s for s in strategies if s.data_feed_type == DataFeedType.OHLCV]
        if not ohlcv_strategies:
            return

        # ── Slot 防重複 ──
        min_tf_min = min(
            (TF_MINUTES.get(s.timeframe, 9999) for s in ohlcv_strategies),
            default=15,
        )
        now = datetime.now(timezone.utc)
        minutes_since_midnight = now.hour * 60 + now.minute
        slot = minutes_since_midnight // min_tf_min
        last = self._last_strategy_slot.get(symbol, -1)
        if slot == last:
            return
        self._last_strategy_slot[symbol] = slot

        # ── 按 timeframe 分組 ──
        tx_timeframes = set(self._settings.tx.timeframes)
        tf_groups: dict[str, list[Strategy]] = {}
        for s in ohlcv_strategies:
            tf = s.timeframe or "1h"
            # 只跑 TX config 中指定的 timeframe
            if tf not in tx_timeframes:
                continue
            tf_groups.setdefault(tf, []).append(s)

        if not tf_groups:
            return

        # ── 抓取 OHLCV ──
        tf_dataframes: dict[str, pd.DataFrame] = {}
        for tf, group in tf_groups.items():
            max_req = max(s.required_candles for s in group)
            try:
                tf_dataframes[tf] = self._data_fetcher.fetch_ohlcv(
                    symbol, timeframe=tf,
                    limit=max(max_req + 10, 100),
                    cache_ttl=60,
                )
            except Exception:
                logger.exception("%s[%s] fetch %s OHLCV failed", _L2, display_name, tf)

        if not tf_dataframes:
            logger.warning("%s[%s] 無可用 OHLCV 數據", _L1, display_name)
            return

        # ── 取得現價 ──
        finest_tf = min(tf_dataframes, key=lambda t: TF_MINUTES.get(t, 9999))
        finest_df = tf_dataframes[finest_tf]
        current_price = float(finest_df["close"].iloc[-1])
        logger.info("%s[%s] 價格: %.2f", _L1, display_name, current_price)

        # 寫入市場快照
        self._db.insert_market_snapshot(display_name, current_price, mode=mode)

        # ── 跑策略 ──
        router = StrategyRouter()
        for strategy in ohlcv_strategies:
            tf = strategy.timeframe or "1h"
            if tf not in tx_timeframes:
                continue
            df = tf_dataframes.get(tf)
            if df is None or len(df) < strategy.required_candles:
                continue
            try:
                verdict = strategy.generate_verdict(df)
            except Exception:
                logger.exception("%s[%s][%s] strategy failed", _L2, display_name, strategy.name)
                continue

            if verdict is not None:
                router.collect(verdict)
                self._db.insert_verdict(
                    display_name, strategy.name, verdict.signal.value,
                    verdict.confidence, verdict.reasoning, cycle_id,
                    market_type="tx",
                    timeframe=verdict.timeframe,
                    mode=mode,
                )
                abbr = strategy.name[:3]
                tf_label = verdict.timeframe or "?"
                sig_str = f"{verdict.signal.value} {verdict.confidence:.0%}"
                logger.info(
                    "%s[%s][%s|%-3s] %-9s — %s",
                    _L2, display_name, abbr, tf_label, sig_str,
                    verdict.reasoning[:80],
                )

        verdicts = router.get_verdicts()
        non_hold = [v for v in verdicts if v.signal != Signal.HOLD]
        if non_hold:
            logger.info(
                "%s[%s] %d non-HOLD / %d total verdicts",
                _L1, display_name, len(non_hold), len(verdicts),
            )
