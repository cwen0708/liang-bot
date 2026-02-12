"""市場數據抓取與快取。"""

import time
from pathlib import Path

import pandas as pd

from bot.config.settings import PROJECT_ROOT
from bot.exchange.base import BaseExchange
from bot.logging_config import get_logger
from bot.utils.helpers import datetime_to_timestamp

logger = get_logger("data.fetcher")

CACHE_DIR = PROJECT_ROOT / "data" / "historical"


class DataFetcher:
    """負責從交易所抓取 OHLCV 數據，並支援本地快取與 TTL 記憶體快取。"""

    def __init__(self, exchange: BaseExchange) -> None:
        self._exchange = exchange
        # TTL 記憶體快取: key="SYMBOL|TF" → (DataFrame, monotonic_time)
        self._ohlcv_cache: dict[str, tuple[pd.DataFrame, float]] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100,
        cache_ttl: float = 0,
    ) -> pd.DataFrame:
        """抓取最新的 K 線數據。

        Args:
            cache_ttl: 若 > 0，使用記憶體快取（秒）。0 = 每次都抓（向後相容）。
        """
        if cache_ttl > 0:
            key = f"{symbol}|{timeframe}"
            now = time.monotonic()

            # 清理過期條目（避免記憶體洩漏）
            expired = [k for k, v in self._ohlcv_cache.items() if (now - v[1]) >= cache_ttl]
            for k in expired:
                del self._ohlcv_cache[k]

            entry = self._ohlcv_cache.get(key)
            if entry and (now - entry[1]) < cache_ttl:
                logger.debug("OHLCV 快取命中: %s %s", symbol, timeframe)
                return entry[0]

        logger.debug("抓取 %s %s K 線 (limit=%d)", symbol, timeframe, limit)
        df = self._exchange.get_ohlcv(symbol, timeframe=timeframe, limit=limit)

        if cache_ttl > 0:
            self._ohlcv_cache[f"{symbol}|{timeframe}"] = (df, time.monotonic())

        return df

    def fetch_multi_timeframe(
        self,
        symbol: str,
        timeframes: tuple[str, ...],
        limit: int = 50,
        cache_ttl: float = 300,
    ) -> dict[str, pd.DataFrame]:
        """抓取多個時間框架的 OHLCV，使用記憶體快取。

        Returns:
            {timeframe: DataFrame}，失敗的 TF 會靜默跳過。
        """
        result: dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            try:
                df = self.fetch_ohlcv(symbol, timeframe=tf, limit=limit, cache_ttl=cache_ttl)
                if not df.empty:
                    result[tf] = df
            except Exception as e:
                logger.warning("MTF 抓取 %s %s 失敗: %s", symbol, tf, e)
        return result

    def clear_ohlcv_cache(self) -> None:
        """清空記憶體快取。"""
        self._ohlcv_cache.clear()

    def fetch_historical(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """下載歷史 K 線數據（分批），支援 CSV 快取。"""
        cache_file = self._cache_path(symbol, timeframe, start_date, end_date)

        if use_cache and cache_file.exists():
            logger.info("從快取載入: %s", cache_file.name)
            df = pd.read_csv(cache_file, parse_dates=["timestamp"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            return df

        logger.info("下載歷史數據: %s %s (%s ~ %s)", symbol, timeframe, start_date, end_date)

        start_ts = datetime_to_timestamp(pd.Timestamp(start_date, tz="UTC"))
        end_ts = datetime_to_timestamp(pd.Timestamp(end_date, tz="UTC"))

        all_data: list[pd.DataFrame] = []
        current_since = start_ts

        while current_since < end_ts:
            df_chunk = self._exchange.get_ohlcv(
                symbol, timeframe=timeframe, limit=1000, since=current_since
            )
            if df_chunk.empty:
                break

            all_data.append(df_chunk)

            last_ts = int(df_chunk["timestamp"].iloc[-1].timestamp() * 1000)
            if last_ts <= current_since:
                break
            current_since = last_ts + 1

            logger.debug(
                "已下載 %d 根 K 線，最新: %s",
                sum(len(d) for d in all_data),
                df_chunk["timestamp"].iloc[-1],
            )

        if not all_data:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.concat(all_data, ignore_index=True)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        # 過濾範圍
        end_dt = pd.Timestamp(end_date, tz="UTC")
        df = df[df["timestamp"] <= end_dt].reset_index(drop=True)

        # 儲存快取
        if use_cache:
            df.to_csv(cache_file, index=False)
            logger.info("已快取 %d 根 K 線至 %s", len(df), cache_file.name)

        return df

    @staticmethod
    def _cache_path(symbol: str, timeframe: str, start: str, end: str) -> Path:
        safe_symbol = symbol.replace("/", "-")
        return CACHE_DIR / f"{safe_symbol}_{timeframe}_{start}_{end}.csv"
