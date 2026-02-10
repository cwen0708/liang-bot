"""市場數據抓取與快取。"""

from pathlib import Path

import pandas as pd

from bot.config.settings import PROJECT_ROOT
from bot.exchange.base import BaseExchange
from bot.logging_config import get_logger
from bot.utils.helpers import datetime_to_timestamp

logger = get_logger("data.fetcher")

CACHE_DIR = PROJECT_ROOT / "data" / "historical"


class DataFetcher:
    """負責從交易所抓取 OHLCV 數據，並支援本地快取。"""

    def __init__(self, exchange: BaseExchange) -> None:
        self._exchange = exchange
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 100
    ) -> pd.DataFrame:
        """抓取最新的 K 線數據。"""
        logger.debug("抓取 %s %s K 線 (limit=%d)", symbol, timeframe, limit)
        return self._exchange.get_ohlcv(symbol, timeframe=timeframe, limit=limit)

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
