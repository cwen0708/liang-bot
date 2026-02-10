"""BarAggregator 單元測試。"""

from datetime import datetime, timezone

import pytest

from bot.data.bar_aggregator import BarAggregator
from bot.data.models import AggTrade, OrderFlowBar


def _make_trade(
    price: float,
    qty: float,
    is_buyer_maker: bool,
    ts_epoch: float,
    trade_id: int = 1,
) -> AggTrade:
    return AggTrade(
        trade_id=trade_id,
        price=price,
        quantity=qty,
        timestamp=datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
        is_buyer_maker=is_buyer_maker,
    )


class TestBarAggregator:
    def test_single_bar_flush(self):
        """單根 K 線內的 trades 透過 flush 輸出。"""
        agg = BarAggregator(interval_seconds=60, tick_size=1.0)

        # 同一分鐘內的 3 筆交易
        t1 = _make_trade(100.0, 1.0, False, 1000.0, 1)   # taker buy
        t2 = _make_trade(101.0, 2.0, True, 1010.0, 2)    # taker sell
        t3 = _make_trade(102.0, 0.5, False, 1020.0, 3)   # taker buy

        assert agg.add_trade(t1) is None
        assert agg.add_trade(t2) is None
        assert agg.add_trade(t3) is None

        bar = agg.flush()
        assert bar is not None
        assert bar.open == 100.0
        assert bar.high == 102.0
        assert bar.low == 100.0
        assert bar.close == 102.0
        assert bar.trade_count == 3
        assert bar.buy_volume == pytest.approx(1.5)   # t1 + t3
        assert bar.sell_volume == pytest.approx(2.0)   # t2
        assert bar.delta == pytest.approx(-0.5)
        assert bar.volume == pytest.approx(3.5)

    def test_bar_closes_on_new_interval(self):
        """新時間區間的 trade 觸發舊 K 線關閉。"""
        agg = BarAggregator(interval_seconds=60, tick_size=1.0)

        # 第一根 K 線（epoch 960~1019 -> bar_open=960）
        t1 = _make_trade(100.0, 1.0, False, 960.0, 1)
        t2 = _make_trade(101.0, 1.0, False, 1000.0, 2)

        assert agg.add_trade(t1) is None
        assert agg.add_trade(t2) is None

        # 第二根 K 線（epoch 1020 -> bar_open=1020）
        t3 = _make_trade(105.0, 2.0, True, 1020.0, 3)
        bar = agg.add_trade(t3)

        assert bar is not None
        assert bar.trade_count == 2
        assert bar.close == 101.0

    def test_footprint_aggregation(self):
        """footprint 按價格層級聚合買/賣量。"""
        agg = BarAggregator(interval_seconds=60, tick_size=1.0)

        # 同價位多筆交易
        t1 = _make_trade(100.0, 1.0, False, 1000.0, 1)   # buy @ 100
        t2 = _make_trade(100.0, 2.0, True, 1001.0, 2)    # sell @ 100
        t3 = _make_trade(101.0, 3.0, False, 1002.0, 3)   # buy @ 101

        agg.add_trade(t1)
        agg.add_trade(t2)
        agg.add_trade(t3)
        bar = agg.flush()

        assert 100.0 in bar.footprint
        assert bar.footprint[100.0].buy_volume == pytest.approx(1.0)
        assert bar.footprint[100.0].sell_volume == pytest.approx(2.0)
        assert 101.0 in bar.footprint
        assert bar.footprint[101.0].buy_volume == pytest.approx(3.0)

    def test_vwap_calculation(self):
        """VWAP 計算正確。"""
        agg = BarAggregator(interval_seconds=60, tick_size=1.0)

        t1 = _make_trade(100.0, 1.0, False, 1000.0, 1)
        t2 = _make_trade(200.0, 1.0, False, 1001.0, 2)

        agg.add_trade(t1)
        agg.add_trade(t2)
        bar = agg.flush()

        # VWAP = (100*1 + 200*1) / (1+1) = 150
        assert bar.vwap == pytest.approx(150.0)

    def test_flush_empty_returns_none(self):
        """空聚合器 flush 回傳 None。"""
        agg = BarAggregator(interval_seconds=60)
        assert agg.flush() is None

    def test_to_candle_conversion(self):
        """OrderFlowBar.to_candle() 正確轉換。"""
        agg = BarAggregator(interval_seconds=60, tick_size=1.0)
        t1 = _make_trade(100.0, 1.0, False, 1000.0, 1)
        agg.add_trade(t1)
        bar = agg.flush()

        candle = bar.to_candle()
        assert candle.open == bar.open
        assert candle.close == bar.close
        assert candle.volume == bar.volume
