"""共用 pytest fixtures。"""

from datetime import datetime, timezone

import pandas as pd
import pytest


@pytest.fixture
def sample_ohlcv_df() -> pd.DataFrame:
    """生成測試用的 OHLCV DataFrame（50 根 K 線，模擬上漲趨勢後下跌）。"""
    import numpy as np

    np.random.seed(42)
    n = 60
    timestamps = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")

    # 模擬先漲後跌的價格走勢
    base_price = 40000.0
    trend_up = np.linspace(0, 3000, n // 2)
    trend_down = np.linspace(3000, 500, n // 2)
    trend = np.concatenate([trend_up, trend_down])
    noise = np.random.normal(0, 100, n)
    close = base_price + trend + noise

    data = {
        "timestamp": timestamps,
        "open": close - np.random.uniform(10, 50, n),
        "high": close + np.random.uniform(50, 200, n),
        "low": close - np.random.uniform(50, 200, n),
        "close": close,
        "volume": np.random.uniform(100, 1000, n),
    }
    return pd.DataFrame(data)


@pytest.fixture
def sample_orderflow_bars():
    """生成測試用的 OrderFlowBar 序列。"""
    import numpy as np

    from bot.data.models import FootprintLevel, OrderFlowBar

    np.random.seed(42)
    n = 60
    base_price = 48000.0
    bars = []

    for i in range(n):
        close = base_price + np.random.normal(0, 200)
        buy_vol = np.random.uniform(5, 50)
        sell_vol = np.random.uniform(5, 50)
        high = close + np.random.uniform(10, 100)
        low = close - np.random.uniform(10, 100)
        open_price = close + np.random.uniform(-50, 50)

        footprint = {
            close: FootprintLevel(price=close, buy_volume=buy_vol * 0.6, sell_volume=sell_vol * 0.6),
            close + 10: FootprintLevel(price=close + 10, buy_volume=buy_vol * 0.4, sell_volume=sell_vol * 0.4),
        }

        bars.append(OrderFlowBar(
            timestamp=datetime(2024, 1, 1, i % 24, 0, 0, tzinfo=timezone.utc),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=buy_vol + sell_vol,
            buy_volume=buy_vol,
            sell_volume=sell_vol,
            trade_count=np.random.randint(10, 100),
            vwap=close,
            footprint=footprint,
        ))
        base_price = close

    return bars


@pytest.fixture
def sample_aggtrades():
    """生成測試用的 AggTrade 列表。"""
    import numpy as np

    from bot.data.models import AggTrade

    np.random.seed(42)
    trades = []
    base_price = 48000.0

    for i in range(100):
        price = base_price + np.random.normal(0, 10)
        trades.append(AggTrade(
            trade_id=i + 1,
            price=price,
            quantity=np.random.uniform(0.001, 0.1),
            timestamp=datetime(2024, 1, 1, 0, 0, i, tzinfo=timezone.utc),
            is_buyer_maker=np.random.random() > 0.5,
        ))

    return trades
