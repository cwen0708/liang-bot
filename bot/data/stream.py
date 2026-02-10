"""Binance aggTrade WebSocket 客戶端。"""

import asyncio
import json
from datetime import datetime, timezone

import websockets

from bot.data.models import AggTrade
from bot.logging_config import get_logger

logger = get_logger("data.stream")

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
BINANCE_TESTNET_WS_BASE = "wss://testnet.binance.vision/ws"


class BinanceAggTradeStream:
    """
    Binance aggTrade WebSocket 串流客戶端。

    自動重連、心跳管理。將原始 JSON 轉為 AggTrade 物件並透過 callback 發出。
    """

    def __init__(
        self,
        symbols: list[str],
        on_trade,
        testnet: bool = True,
        reconnect_delay: float = 5.0,
    ) -> None:
        """
        Args:
            symbols: 交易對列表 (e.g., ["BTCUSDT", "ETHUSDT"])。
            on_trade: 收到 AggTrade 時的 async callback。
            testnet: 是否使用測試網。
            reconnect_delay: 重連等待秒數。
        """
        self.symbols = [s.lower() for s in symbols]
        self.on_trade = on_trade
        self.testnet = testnet
        self.reconnect_delay = reconnect_delay
        self._running = False
        self._ws = None

    @property
    def url(self) -> str:
        base = BINANCE_TESTNET_WS_BASE if self.testnet else BINANCE_WS_BASE
        streams = "/".join(f"{s}@aggTrade" for s in self.symbols)
        return f"{base}/{streams}"

    async def start(self) -> None:
        """啟動 WebSocket 連線（含自動重連）。"""
        self._running = True
        logger.info("啟動 aggTrade 串流: %s", self.symbols)

        while self._running:
            try:
                await self._connect()
            except Exception as e:
                if not self._running:
                    break
                logger.warning("WebSocket 連線中斷: %s，%s 秒後重連", e, self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        """停止 WebSocket 連線。"""
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("aggTrade 串流已停止")

    async def _connect(self) -> None:
        """建立 WebSocket 連線並接收訊息。"""
        logger.info("連線 WebSocket: %s", self.url)

        async with websockets.connect(
            self.url,
            ping_interval=20,
            ping_timeout=10,
        ) as ws:
            self._ws = ws
            logger.info("WebSocket 連線成功")

            async for message in ws:
                if not self._running:
                    break

                try:
                    data = json.loads(message)
                    trade = self._parse_trade(data)
                    if trade:
                        await self.on_trade(trade)
                except Exception:
                    logger.exception("處理 aggTrade 訊息失敗")

    @staticmethod
    def _parse_trade(data: dict) -> AggTrade | None:
        """解析 Binance aggTrade WebSocket 訊息。"""
        if data.get("e") != "aggTrade":
            return None

        return AggTrade(
            trade_id=data["a"],
            price=float(data["p"]),
            quantity=float(data["q"]),
            timestamp=datetime.fromtimestamp(data["T"] / 1000, tz=timezone.utc),
            is_buyer_maker=data["m"],
        )
