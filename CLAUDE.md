# Binance Spot Trading Bot

## Project Overview
Python 幣安現貨自動化交易機器人 + Vue 3 即時監控前端。
Bot 透過 ccxt 連接幣安 API，使用多策略 + LLM 決策引擎，所有數據透過 Supabase 中介，前端部署在 Firebase Hosting。

## Architecture

```
Vue 3 Frontend (SPA)          Supabase (PostgreSQL + Realtime)          Python Bot (daemon)
Firebase Hosting         ←→   資料中介（唯一橋樑）                  ←→   Binance API
liang-bot.web.app              kkukdzyyaqhfckvdhkfp                      ccxt
```

**核心設計：無 FastAPI，Bot 不暴露任何 HTTP 端口。**
- Bot 每輪 cycle 開頭從 Supabase `bot_config` 讀取最新配置
- Bot 執行策略/交易後，寫入結果到 Supabase 各表
- 前端透過 Supabase Realtime 即時接收新資料
- 前端修改配置直接寫入 `bot_config` 表

## Bot Structure (`bot/`)
- `app.py` — 主交易迴圈（run → _process_symbol → 策略 → LLM → 執行）
- `__main__.py` — CLI 入口（run, backtest, balance, validate, loan-guard）
- `config/` — 配置管理（Settings dataclass，從 .env + config.yaml / Supabase 載入）
- `exchange/` — 交易所抽象層（BaseExchange → BinanceClient，ccxt 封裝）
- `strategy/` — 策略引擎（BaseStrategy 繼承）
  - `sma_crossover.py`, `rsi_oversold.py`, `bollinger_breakout.py`, `macd_momentum.py` — OHLCV 策略
  - `vwap_reversion.py` — VWAP 均值回歸策略（price vs VWAP ±1σ band）
  - `ema_ribbon.py` — EMA 絲帶趨勢策略（EMA 8/13/21/34 排列判定）
  - `tia_orderflow.py` — Order Flow 策略（權重 50%）
  - `router.py` — 策略路由
  - `signals.py` — Signal enum (BUY/SELL/HOLD)
- `llm/` — LLM 決策引擎（Claude CLI 呼叫，所有非 HOLD 信號強制 AI 審核）
- `orderflow/` — Order Flow 分析（CVD、吸收量、SFP）
- `risk/` — 風險管理（部位大小、停損 3%、停利 6%、每日虧損上限）
- `data/` — 市場數據抓取（K 線 + aggTrade）
- `db/supabase_client.py` — SupabaseWriter（Bot 寫入 Supabase 的單一入口）
- `backtest/` — 回測引擎
- `execution/` — 訂單執行與追蹤
- `logging_config/` — 日誌系統 + Supabase 批次寫入 handler
- `utils/` — 工具函數（retry 裝飾器等）

## Frontend Structure (`frontend/`)
- Vue 3 + TypeScript + Composition API
- pnpm + Vite + TailwindCSS v4（CSS 變數主題 `--color-*`）
- Vue Router + Pinia
- @supabase/supabase-js（Realtime + query + write）
- lightweight-charts（TradingView K 線圖）

### Pages
| 頁面 | 檔案 | 功能 |
|------|------|------|
| 總覽 | `DashboardPage.vue` | Bot 狀態、總資產、持倉、餘額、最近訂單、AI 決策、借貸健康度 |
| 交易 | `TradingPage.vue` | K 線圖（即時更新）、持倉詳情 |
| 訂單 | `OrdersPage.vue` | 訂單歷史（分頁、篩選） |
| 策略 | `StrategyPage.vue` | 策略結論時間線、LLM 決策詳情（含 BUY/SELL/HOLD badge） |
| 借貸 | `LoanGuardPage.vue` | LTV 儀表盤、歷史圖表（基準線 70%） |
| 設定 | `ConfigPage.vue` | JSON 編輯器、版本歷史 |
| 日誌 | `LogsPage.vue` | 即時日誌串流（可篩選 level） |

### Key Composables
- `composables/useSupabase.ts` — Supabase 客戶端單例
- `composables/useRealtime.ts` — `useRealtimeTable()` / `useRealtimeInserts()` 封裝
- `stores/bot.ts` — Pinia store（status, positions, latestPrices, balances, totalUsdt）

## Supabase
- Organization: `Yooliang` (`vrxxpbhnqyqkvpnfzwit`)
- Project: `InCount` (`kkukdzyyaqhfckvdhkfp`)
- Migrations: `supabase/migrations/`
- CLI: `npx supabase db push` 推送 migration 到遠端
- MCP: 需認證到 Yooliang 帳號才能透過 Supabase MCP 操作

### Tables
| 資料表 | 寫入者 | 用途 |
|--------|--------|------|
| `bot_config` | 前端 | 配置版本管理（取代 config.yaml） |
| `strategy_verdicts` | Bot | 策略結論 |
| `llm_decisions` | Bot | LLM 最終決策 |
| `orders` | Bot | 訂單紀錄 |
| `positions` | Bot | 當前持倉（upsert by symbol） |
| `loan_health` | Bot | 借款 LTV 快照 |
| `bot_logs` | Bot | Bot 日誌（批次寫入） |
| `market_snapshots` | Bot | 價格快照 |
| `bot_status` | Bot | Bot 心跳 / 運行狀態 |
| `account_balances` | Bot | 帳戶餘額快照（每輪 cycle） |

## Commands

### Bot
```bash
python -m bot run                        # 啟動交易
python -m bot backtest --symbol BTC/USDT # 執行回測
python -m bot balance                    # 查詢餘額
python -m bot validate                   # 驗證配置
python -m bot loan-guard                 # 手動執行借貸監控
```

### Frontend
```bash
cd frontend
pnpm dev                                 # 開發伺服器
pnpm build                               # 生產建置（輸出到 dist/）
npx vue-tsc --noEmit                     # TypeScript 型別檢查
```

### Supabase
```bash
npx supabase db push                     # 推送 migration 到遠端
npx supabase migration new <name>        # 建立新 migration
```

### Deploy
```bash
cd frontend && pnpm build                # 先建置前端
cd .. && firebase deploy --only hosting  # 部署到 Firebase
```

### Tests
```bash
pytest tests/
```

## Key Conventions
- API 金鑰只在 `.env` 中，絕對不 hardcode（BINANCE_API_KEY, BINANCE_API_SECRET, SUPABASE_URL, SUPABASE_SERVICE_KEY）
- 前端環境變數在 `frontend/.env`（VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY）
- 新策略繼承 `bot/strategy/base.py:BaseStrategy`
- 每個 OHLCV 策略跑 4 個時段（15m, 1h, 4h, 1d），前端顯示最高信心度的時段
- 策略清單需同步更新：`config.yaml`、`app.py` / `app_futures.py` 的 `OHLCV_STRATEGY_REGISTRY`、Supabase `bot_config`、前端 `allStrategies` 陣列
- 配置使用 frozen dataclass，載入後不可變
- 所有交易所 API 呼叫都有 retry 裝飾器
- 日誌使用 `get_logger(__name__)` 取得
- Supabase DDL 變更必須透過 `supabase/migrations/` + `npx supabase db push`
- 前端時間顯示一律使用 24 小時制（`hour12: false`）
- lightweight-charts 時間戳需加上本地時區偏移（`getTimezoneOffset() * -60`）
- 桌面版頁面避免溢出：外層 `md:h-[calc(100vh)] md:overflow-hidden` + 內容 `min-h-0 flex-1`
- 所有非 HOLD 信號強制經過 LLM 審核
- Bot 透過 service_role key 繞過 RLS；前端用 anon key，受 RLS 限制

## Deployment
| 組件 | 服務 | 備註 |
|------|------|------|
| Frontend | Firebase Hosting (`liang-bot`) | `firebase deploy --only hosting` |
| Bot | 本地 / GCP Compute Engine | `python -m bot run`（daemon，不暴露端口） |
| Database | Supabase (hosted PostgreSQL) | Realtime 已啟用所有表 |

## Loan Guard
借貸再平衡機制，4 層 LTV 判定：
- `>= danger_ltv (75%)` → `_loan_protect()` 買入 + 質押（低買）
- `>= target_ltv (65%)` → 警告
- `<= low_ltv (40%)` → `_loan_take_profit()` 減質押 + 賣出（高賣）
- 中間 → 安全

執行前會透過 LLM 進行 AI 審核（除非 dry_run=true）。

## Long-term Memory
本專案使用 Claude Code 的 auto memory 機制，記憶檔案位於：
`~/.claude/projects/C--Github-binance-spot-bot/memory/MEMORY.md`

- 此檔案每次對話自動載入，記錄專案歷史脈絡、踩坑經驗、重要決策
- **遇到重大事件（架構變更、Bug 修復、策略調整、部署變更）時，必須同步更新 MEMORY.md**
- 事件紀錄格式：在「近期重大事件」區塊以日期分組，倒序排列
- 技術 know-how 歸類到對應的主題區塊（Supabase、Exchange Client、前端等）
