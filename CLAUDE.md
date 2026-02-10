# Binance Spot Trading Bot

## Project Overview
Python 幣安現貨自動化交易機器人，使用 ccxt 作為交易所 API 封裝。

## Architecture
- `bot/config/` — 配置管理（.env 金鑰 + config.yaml 參數）
- `bot/exchange/` — 交易所抽象層（ccxt 封裝）
- `bot/data/` — 市場數據抓取與快取
- `bot/strategy/` — 策略引擎（繼承 BaseStrategy）
- `bot/risk/` — 風險管理（部位、停損、停利）
- `bot/execution/` — 訂單執行與追蹤
- `bot/backtest/` — 回測引擎
- `bot/logging_config/` — 日誌系統
- `bot/utils/` — 工具函數

## Commands
- `python -m bot run` — 啟動交易
- `python -m bot backtest --symbol BTC/USDT` — 執行回測
- `python -m bot balance` — 查詢餘額
- `python -m bot validate` — 驗證配置
- `pytest tests/` — 執行測試

## Key Conventions
- API 金鑰只在 `.env` 中，絕對不 hardcode
- 新策略繼承 `bot/strategy/base.py:BaseStrategy`
- 配置使用 frozen dataclass，載入後不可變
- 所有交易所 API 呼叫都有 retry 裝飾器
- 日誌使用 `get_logger(__name__)` 取得
