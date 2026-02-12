# 系統流程說明文件

> **Binance Spot Trading Bot** — 完整交易決策流程與架構說明

## 目錄

1. [系統架構總覽](#系統架構總覽)
2. [元件關係圖](#元件關係圖)
3. [主交易迴圈](#主交易迴圈)
4. [單一交易對處理流程](#單一交易對處理流程)
5. [多時間框架分析 (MTF)](#多時間框架分析-mtf)
6. [LLM 決策引擎](#llm-決策引擎)
7. [風控管理與持倉週期](#風控管理與持倉週期)
8. [訂單執行與停損停利](#訂單執行與停損停利)
9. [合約交易流程](#合約交易流程)
10. [資料流與 Supabase 整合](#資料流與-supabase-整合)
11. [配置熱重載](#配置熱重載)
12. [借貸監控 (Loan Guard)](#借貸監控-loan-guard)

---

## 系統架構總覽

系統由三大部分組成：Python Bot（核心交易引擎）、Supabase（資料中介）、Vue 3 Frontend（監控介面）。Bot 不暴露任何 HTTP 端口，所有資料交換透過 Supabase PostgreSQL + Realtime。

```mermaid
graph LR
    subgraph "Python Bot (Daemon)"
        A[TradingBot] --> B[DataFetcher]
        A --> C[StrategyRouter]
        A --> D[LLM DecisionEngine]
        A --> E[RiskManager]
        A --> F[OrderExecutor]
    end

    subgraph "Supabase (PostgreSQL)"
        G[(bot_config)]
        H[(strategy_verdicts)]
        I[(llm_decisions)]
        J[(orders)]
        K[(positions)]
        L[(bot_status)]
        M[(market_snapshots)]
        N[(account_balances)]
        O[(bot_logs)]
    end

    subgraph "Vue 3 Frontend"
        P[Dashboard]
        Q[Trading]
        R[Strategy]
        S[Config]
    end

    B -- "ccxt" --> EX[Binance API]
    F -- "ccxt" --> EX
    A -- "寫入" --> H & I & J & K & L & M & N & O
    A -- "讀取配置" --> G
    S -- "寫入配置" --> G
    P & Q & R -- "Realtime 訂閱" --> H & I & J & K & L
```

---

## 元件關係圖

```mermaid
classDiagram
    class TradingBot {
        +settings: Settings
        +exchange: BinanceClient
        +data_fetcher: DataFetcher
        +strategies: list~Strategy~
        +risk_manager: RiskManager
        +executor: OrderExecutor
        +llm_engine: LLMDecisionEngine
        +router: StrategyRouter
        +run()
        -_process_symbol()
        -_process_futures_symbol()
        -_make_decision()
        -_fetch_mtf_summary()
    }

    class Settings {
        +exchange: ExchangeConfig
        +spot: SpotConfig
        +futures: FuturesConfig
        +llm: LLMConfig
        +mtf: MultiTimeframeConfig
        +horizon_risk: HorizonRiskConfig
        +load() Settings
        +from_dict() Settings
    }

    class StrategyRouter {
        +collect(verdict)
        +get_verdicts() list
        +clear()
    }

    class LLMDecisionEngine {
        +decide() LLMDecision
        -_parse_decision()
        -_fallback_decision()
    }

    class RiskManager {
        +pre_calculate_metrics() RiskMetrics
        +evaluate() RiskOutput
        -_get_horizon_params()
        -_calc_sl_tp_distance()
    }

    class DataFetcher {
        +fetch_ohlcv()
        +fetch_multi_timeframe()
        -_ohlcv_cache
    }

    TradingBot --> Settings
    TradingBot --> StrategyRouter
    TradingBot --> LLMDecisionEngine
    TradingBot --> RiskManager
    TradingBot --> DataFetcher
    TradingBot --> OrderExecutor

    class OrderExecutor {
        +execute()
        +place_sl_tp()
        +cancel_sl_tp()
    }
```

---

## 主交易迴圈

Bot 啟動後進入無限迴圈，每 `check_interval_seconds` 秒（預設 60 秒）執行一輪分析。

```mermaid
flowchart TD
    START([Bot 啟動]) --> INIT[初始化<br/>載入配置 / 建立策略 / 恢復持倉]
    INIT --> CYCLE_START["═══ 第 N 輪分析開始 ═══"]

    CYCLE_START --> HOT_RELOAD{Supabase<br/>配置版本變更?}
    HOT_RELOAD -- "是" --> APPLY_CFG[套用新配置<br/>策略/合約熱重載]
    HOT_RELOAD -- "否" --> SPOT_LOOP
    APPLY_CFG --> SPOT_LOOP

    SPOT_LOOP["遍歷現貨交易對<br/>spot.pairs"]
    SPOT_LOOP --> PROCESS_SPOT["_process_symbol()<br/>（詳見下一節）"]
    PROCESS_SPOT --> MORE_SPOT{還有交易對?}
    MORE_SPOT -- "是" --> PROCESS_SPOT
    MORE_SPOT -- "否" --> FUTURES_CHECK

    FUTURES_CHECK{合約模組啟用?}
    FUTURES_CHECK -- "是" --> FUTURES_LOOP["遍歷合約交易對<br/>futures.pairs"]
    FUTURES_LOOP --> PROCESS_FUTURES["_process_futures_symbol()"]
    PROCESS_FUTURES --> MORE_FUTURES{還有交易對?}
    MORE_FUTURES -- "是" --> PROCESS_FUTURES
    MORE_FUTURES -- "否" --> MARGIN_SNAP[記錄合約保證金快照]
    MARGIN_SNAP --> LOAN_CHECK
    FUTURES_CHECK -- "否" --> LOAN_CHECK

    LOAN_CHECK{借貸監控啟用?}
    LOAN_CHECK -- "是" --> LOAN_GUARD["_check_loan_health()<br/>LTV 監控 + AI 審核"]
    LOAN_CHECK -- "否" --> BALANCE_SNAP
    LOAN_GUARD --> BALANCE_SNAP

    BALANCE_SNAP[寫入帳戶餘額快照]
    BALANCE_SNAP --> HEARTBEAT[更新 bot_status 心跳<br/>flush 日誌到 Supabase]
    HEARTBEAT --> SLEEP["sleep(check_interval_seconds)"]
    SLEEP --> CYCLE_START
```

### 初始化流程

```
TradingBot.__init__()
├── Settings.load()                # 載入 .env + config.yaml
├── SupabaseWriter()               # 建立 Supabase 連線
├── load_config()                  # 從 Supabase 讀取線上配置（覆蓋本地）
├── BinanceClient()                # ccxt 交易所連線
├── DataFetcher()                  # K 線資料抓取器
├── _create_all_strategies()       # 建立 OHLCV + OrderFlow 策略
├── RiskManager(spot, horizon_risk) # 風控（含 Horizon 配置）
├── OrderExecutor()                # 訂單執行器
├── _restore_positions()           # 從 Supabase 恢復持倉
├── StrategyRouter()               # 策略結論收集器
├── LLMDecisionEngine()            # Claude LLM 決策引擎
└── _init_futures()                # 合約模組（若啟用）
```

---

## 單一交易對處理流程

`_process_symbol()` 是現貨交易的核心方法，包含 7 個步驟：

```mermaid
flowchart TD
    START(["_process_symbol(symbol)"]) --> STEP1

    %% Step 1: 抓取 K 線
    STEP1["① 抓取 K 線 (OHLCV)"]
    STEP1 --> CHECK_DATA{K 線充足?}
    CHECK_DATA -- "不足" --> SKIP_RETURN([跳過此交易對])
    CHECK_DATA -- "充足" --> GET_PRICE["取得現價 + 寫入 market_snapshot"]

    %% Step 2: 停損停利
    GET_PRICE --> STEP2["② 停損停利檢查"]
    STEP2 --> LIVE_OCO{Live + 有 OCO?}
    LIVE_OCO -- "是" --> SYNC_OCO[檢查交易所 OCO 是否成交]
    LIVE_OCO -- "否" --> PAPER_SLTP
    SYNC_OCO --> OCO_FILLED{已成交?}
    OCO_FILLED -- "是" --> SKIP_RETURN
    OCO_FILLED -- "否" --> PAPER_SLTP
    PAPER_SLTP["Paper 模式: 輪詢價格判斷 SL/TP"]
    PAPER_SLTP --> SLTP_HIT{觸發?}
    SLTP_HIT -- "是" --> EXEC_SELL_SLTP["執行賣出 (SL/TP)"]
    EXEC_SELL_SLTP --> SKIP_RETURN
    SLTP_HIT -- "否" --> STEP3

    %% Step 3: 策略結論收集
    STEP3["③ 收集策略結論"]
    STEP3 --> OF_FEED["3a. 訂單流資料收集<br/>(每輪都執行)"]
    OF_FEED --> SLOT_CHECK["3b. Slot 防重複<br/>minutes_since_midnight ÷ interval_n"]
    SLOT_CHECK --> RUN_STRATS["執行各策略 generate_verdict()"]
    RUN_STRATS --> COLLECT["router.collect(verdict)<br/>寫入 strategy_verdicts 表"]
    COLLECT --> HAS_VERDICTS{有策略結論?}
    HAS_VERDICTS -- "無" --> SKIP_RETURN
    HAS_VERDICTS -- "有" --> STEP4

    %% Step 4: 風控預計算
    STEP4["④ 預計算風控指標<br/>(僅 BUY 信號)"]
    STEP4 --> RISK_PRE["計算 ATR / SL / TP / R:R<br/>Fibonacci / 支撐壓力 / 布林帶"]

    %% Step 5: MTF
    RISK_PRE --> STEP5["⑤ 多時間框架摘要<br/>_fetch_mtf_summary()"]
    STEP5 --> MTF_ENABLED{MTF 啟用?}
    MTF_ENABLED -- "是" --> FETCH_MTF["抓取 1d/4h/1h/15m K 線<br/>計算各 TF 技術指標<br/>生成 Markdown 表格"]
    MTF_ENABLED -- "否" --> STEP6
    FETCH_MTF --> STEP6

    %% Step 6: LLM 決策
    STEP6["⑥ LLM 決策"]
    STEP6 --> LLM_CALL["Claude LLM 分析<br/>策略結論 + 倉位 + 風控 + MTF"]
    LLM_CALL --> LLM_OUT{LLM 結果}
    LLM_OUT -- "HOLD" --> HOLD_RETURN(["HOLD 不動作"])
    LLM_OUT -- "信心 < 門檻" --> HOLD_RETURN
    LLM_OUT -- "BUY/SELL" --> STEP7

    %% Step 7: 風控 + 執行
    STEP7["⑦ 風控評估 + 執行"]
    STEP7 --> IS_BUY{BUY or SELL?}
    IS_BUY -- "BUY" --> RISK_EVAL["風控評估<br/>Horizon 倉位 / SL / TP<br/>LLM size_pct 取保守值"]
    RISK_EVAL --> APPROVED{通過?}
    APPROVED -- "否" --> RISK_REJECT(["風控拒絕"])
    APPROVED -- "是" --> EXEC_BUY["執行買入 + 掛 SL/TP<br/>寫入 orders + positions"]
    IS_BUY -- "SELL" --> EXEC_SELL["執行賣出<br/>取消 SL/TP 掛單<br/>計算 PnL"]
```

---

## 多時間框架分析 (MTF)

MTF 模組在 LLM 決策前額外抓取多個時間框架的 K 線，計算技術指標摘要。

### 配置

```python
@dataclass(frozen=True)
class MultiTimeframeConfig:
    enabled: bool = True
    timeframes: tuple[str, ...] = ("1d", "4h", "1h", "15m")
    candle_limit: int = 50
    cache_ttl_seconds: int = 300   # 5 分鐘 TTL 快取
```

### 流程

```mermaid
flowchart LR
    subgraph "_fetch_mtf_summary()"
        A[檢查 MTF 啟用] --> B["fetch_multi_timeframe()<br/>抓取 4 個 TF 的 K 線"]
        B --> C["compute_mtf_summary(df, tf)<br/>計算每個 TF 的技術指標"]
        C --> D["summarize_multi_timeframe()<br/>生成 Markdown 表格"]
    end

    subgraph "TTL 快取機制"
        B --> CACHE{快取命中?}
        CACHE -- "命中" --> USE_CACHE[直接返回快取 DataFrame]
        CACHE -- "未命中" --> API_CALL[呼叫 Binance API 抓取]
        API_CALL --> STORE[存入快取 + 時間戳]
    end
```

### TimeframeSummary 計算項目

每個時間框架計算以下指標，封裝為 `TimeframeSummary` dataclass：

| 指標 | 計算方式 | 用途 |
|------|---------|------|
| `trend` | SMA20 vs SMA50 斜率 | 趨勢方向 (bullish/bearish/neutral) |
| `rsi_14` | RSI(14) | 超買/超賣判斷 |
| `macd_direction` | MACD histogram 方向 | 動量方向 |
| `bb_pct_b` | Bollinger %B | 價格在布林帶中的位置 |
| `volume_trend` | 近期 vs 歷史成交量比 | 量能變化 |
| `atr_pct` | ATR(14) / close × 100 | 波動幅度 |

### 輸出範例

```markdown
## 多時間框架分析

| 時間框架 | 趨勢 | RSI | MACD方向 | BB%B | 成交量 | 波幅(ATR%) |
|----------|------|-----|---------|------|--------|-----------|
| 1d       | 看漲 | 55  | 多頭    | 0.63 | 放量   | 2.35%     |
| 4h       | 盤整 | 48  | 中性    | 0.45 | 持平   | 1.82%     |
| 1h       | 看跌 | 32  | 空頭    | 0.15 | 縮量   | 1.20%     |
| 15m      | 看漲 | 28(超賣) | 多頭 | 0.08 | 放量 | 0.85%     |

**多框架偏多**: 2/4 時間框架看漲。
```

---

## LLM 決策引擎

所有非 HOLD 信號強制經過 Claude LLM 審核。LLM 扮演策略仲裁者，綜合所有資訊做最終判斷。

### 決策流程

```mermaid
flowchart TD
    INPUT["輸入:<br/>策略結論 + 倉位狀態<br/>+ 風控指標 + MTF 摘要"]
    INPUT --> SUMMARIZE["摘要化<br/>summarize_verdicts()<br/>summarize_portfolio()<br/>summarize_risk_metrics()<br/>summarize_multi_timeframe()"]
    SUMMARIZE --> BUILD_PROMPT["build_decision_prompt()<br/>組建完整提示詞"]
    BUILD_PROMPT --> CALL_LLM["Claude CLI 呼叫<br/>claude -p --model sonnet"]
    CALL_LLM --> PARSE["解析 JSON 回傳"]
    PARSE --> VALIDATE

    VALIDATE{驗證決策}
    VALIDATE --> CHECK_OVERRIDE{LLM 方向<br/>有策略支持?}
    CHECK_OVERRIDE -- "有" --> ACCEPT[接受決策]
    CHECK_OVERRIDE -- "無 + 信心≥0.7" --> OVERRIDE["有條件覆蓋<br/>倉位縮半"]
    CHECK_OVERRIDE -- "無 + 信心<0.7" --> HOLD_FALLBACK["強制 HOLD"]

    CALL_LLM -- "失敗" --> HOLD_FAIL["HOLD<br/>(LLM 失敗不 fallback 執行)"]

    subgraph "LLM 回傳格式"
        JSON["```json<br/>{<br/>  action: BUY/SELL/HOLD<br/>  confidence: 0.75<br/>  stop_loss_pct: 0.03<br/>  take_profit_pct: 0.06<br/>  position_size_pct: 0.02<br/>  horizon: medium<br/>  reasoning: ...<br/>}<br/>```"]
    end
```

### LLM 提示詞結構

```
┌─────────────────────────────────┐
│ SYSTEM_PROMPT                   │  角色定義 + 決策原則 + 風控紅線
│  - 多時間框架共振指引            │  + 持倉週期判斷標準
│  - Horizon SL/TP/倉位表          │  + JSON 回傳格式
├─────────────────────────────────┤
│ 交易對: BTC/USDT                │
│ 現價: 95000.00 USDT             │
├─────────────────────────────────┤
│ 倉位狀態                        │  可用餘額 / 持倉 / 每日 PnL
├─────────────────────────────────┤
│ 多時間框架分析                   │  1d/4h/1h/15m 技術指標表格
├─────────────────────────────────┤
│ 策略結論                        │  各策略的信號 + 信心 + 推理
├─────────────────────────────────┤
│ 風控指標                        │  SL/TP/R:R + Fib + 支撐壓力 + BB
└─────────────────────────────────┘
```

---

## 風控管理與持倉週期

### Horizon 動態風控

LLM 回傳 `horizon`（short/medium/long），風控根據此值調整所有參數。

```mermaid
flowchart LR
    LLM_HORIZON["LLM 回傳<br/>horizon: short/medium/long"]
    LLM_HORIZON --> GET_PARAMS["_get_horizon_params(horizon)"]
    GET_PARAMS --> PARAMS["取得對應參數:<br/>sl_multiplier / tp_multiplier<br/>sl_pct / tp_pct<br/>size_factor / min_rr"]
    PARAMS --> CALC_SL["SL = ATR × sl_multiplier<br/>或 price × sl_pct"]
    PARAMS --> CALC_TP["TP = ATR × tp_multiplier<br/>或 price × tp_pct"]
    PARAMS --> CALC_SIZE["倉位 = base × size_factor<br/>取 min(風控計算, LLM 建議)"]
    PARAMS --> CHECK_RR["R:R ≥ min_rr?"]
```

### 三種持倉週期參數

| 參數 | Short (短線) | Medium (中線) | Long (長線) |
|------|-------------|--------------|------------|
| SL 倍率 (ATR×) | 1.0 | 1.5 | 2.5 |
| TP 倍率 (ATR×) | 2.0 | 3.0 | 5.0 |
| SL 固定 % | 2% | 3% | 5% |
| TP 固定 % | 4% | 6% | 15% |
| 倉位因子 | 1.2 (較大) | 1.0 (標準) | 0.6 (較小) |
| 最低 R:R | 1.5 | 2.0 | 2.5 |

### 風控評估流程

```mermaid
flowchart TD
    EVAL["evaluate(signal, symbol, price, balance, horizon, llm_size_pct)"]
    EVAL --> IS_BUY{BUY?}
    IS_BUY -- "SELL" --> CHECK_POS_SELL{持有?}
    CHECK_POS_SELL -- "是" --> APPROVE_SELL[通過: 返回持有數量]
    CHECK_POS_SELL -- "否" --> REJECT_SELL[拒絕: 未持有]

    IS_BUY -- "BUY" --> DAILY_PNL{每日虧損<br/>超限?}
    DAILY_PNL -- "超限" --> REJECT1[拒絕]
    DAILY_PNL -- "OK" --> MAX_POS{持倉數<br/>已滿?}
    MAX_POS -- "已滿" --> REJECT2[拒絕]
    MAX_POS -- "OK" --> DUP_CHECK{已持有<br/>此幣?}
    DUP_CHECK -- "是" --> REJECT3[拒絕]
    DUP_CHECK -- "否" --> CALC

    CALC["計算倉位大小"]
    CALC --> BASE["base = balance × max_position_pct / price"]
    BASE --> HORIZON_ADJ["× size_factor (horizon)"]
    HORIZON_ADJ --> LLM_ADJ["min(風控計算, LLM建議)"]
    LLM_ADJ --> SL_TP["計算 Horizon SL/TP"]
    SL_TP --> APPROVE[通過: 返回 quantity + SL + TP]
```

---

## 訂單執行與停損停利

### 買入執行流程

```mermaid
sequenceDiagram
    participant Bot as TradingBot
    participant Exec as OrderExecutor
    participant Risk as RiskManager
    participant Ex as Binance
    participant DB as Supabase

    Bot->>Exec: execute(BUY, symbol, risk_output)
    Exec->>Ex: create_order(market buy)
    Ex-->>Exec: order result
    Bot->>Exec: place_sl_tp(symbol, qty, TP, SL)
    Exec->>Ex: create OCO order
    Ex-->>Exec: tp_order_id, sl_order_id
    Bot->>Risk: add_position(symbol, qty, price, tp_id, sl_id)
    Bot->>DB: insert_order(order)
    Bot->>DB: upsert_position(symbol, position_data)
```

### 停損停利機制

| 模式 | 機制 | 說明 |
|------|------|------|
| Live | OCO 掛單 | 交易所自動執行，Bot 輪詢檢查成交狀態 |
| Paper | 輪詢比價 | 每輪 cycle 用現價比對 SL/TP 價位 |

---

## 合約交易流程

合約交易支援 **做多 (BUY)** 和 **做空 (SHORT)**，信號轉換邏輯如下：

```mermaid
flowchart TD
    SIGNAL["LLM/策略信號"]
    SIGNAL --> IS_SELL{SELL?}

    IS_SELL -- "SELL + 持有多倉" --> CLOSE_LONG["SELL (平多)"]
    IS_SELL -- "SELL + 無持倉" --> OPEN_SHORT["SHORT (開空)"]

    SIGNAL --> IS_BUY{BUY?}
    IS_BUY -- "BUY + 持有空倉" --> CLOSE_SHORT["COVER (平空)"]
    IS_BUY -- "BUY + 無持倉" --> OPEN_LONG["BUY (開多)"]

    SIGNAL --> IS_DIRECT{SHORT/COVER?}
    IS_DIRECT --> PASS_THROUGH["直接使用"]
```

### 合約額外風控

- **保證金比率** > 80%：禁止開新倉
- **清算價距離** < 5%：強制考慮平倉
- **資金費率** > 0.1%（方向不利）：謹慎開倉
- 同一幣對不可同時持有多倉和空倉

---

## 資料流與 Supabase 整合

```mermaid
flowchart LR
    subgraph "Bot 寫入 (service_role key)"
        W1[strategy_verdicts] --> |"每輪每策略"| DB
        W2[llm_decisions] --> |"每輪有信號時"| DB
        W3[orders] --> |"交易執行時"| DB
        W4[positions] --> |"開/平倉時 upsert"| DB
        W5[bot_status] --> |"每輪心跳"| DB
        W6[market_snapshots] --> |"每輪每幣"| DB
        W7[account_balances] --> |"每輪"| DB
        W8[bot_logs] --> |"批次 flush"| DB
    end

    subgraph DB["Supabase PostgreSQL"]
        direction TB
        T1[(bot_config)]
        T2[(各資料表)]
    end

    subgraph "Bot 讀取"
        R1[load_config] --> |"每輪開頭"| DB
        R2[load_positions] --> |"啟動時恢復"| DB
    end

    subgraph "Frontend 讀取 (anon key + RLS)"
        F1[Realtime 訂閱] --> |"即時推送"| DB
        F2[Query] --> |"歷史查詢"| DB
    end

    subgraph "Frontend 寫入"
        F3[ConfigPage] --> |"寫入 bot_config"| DB
    end
```

### 日誌批次寫入

Bot 日誌使用自定義 `SupabaseLogHandler`，緩衝到 50 條或 30 秒後批次寫入 Supabase，避免頻繁 API 呼叫。每輪 cycle 結束時強制 `flush_logs()`。

---

## 配置熱重載

Bot 每輪 cycle 開頭從 Supabase `bot_config` 表讀取最新配置。前端 ConfigPage 修改配置後寫入新版本，Bot 下一輪自動偵測版本變更並套用。

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant DB as Supabase
    participant Bot as TradingBot

    FE->>DB: INSERT bot_config (version=N+1)
    Note over Bot: 下一輪 cycle 開始
    Bot->>DB: load_config()
    DB-->>Bot: config_json (version=N+1)
    Bot->>Bot: Settings.from_dict(new_cfg)

    alt 策略清單/參數變更
        Bot->>Bot: _create_all_strategies()
        Bot->>Bot: 清除 slot 快取
    end

    alt 合約啟用/停用變更
        Bot->>Bot: _init_futures() 或清除
    end
```

### 可熱重載項目

| 項目 | 說明 |
|------|------|
| 交易對 (pairs) | 下一輪立即生效 |
| 策略清單/參數 | 重建策略實例 + 清除 slot |
| 風控參數 | SL/TP/倉位/每日虧損限制 |
| LLM 啟停 | 下一輪決策方式切換 |
| MTF 啟停 | 多時間框架分析開關 |
| Horizon 參數 | SL/TP 倍率、倉位因子 |
| 合約模組 | 啟用/停用/槓桿/交易對 |
| 借貸監控 | 啟用/停用/LTV 閾值 |

---

## 借貸監控 (Loan Guard)

每輪 cycle 結束後檢查借貸 LTV，4 層判定自動再平衡。

```mermaid
flowchart TD
    CHECK["檢查借貸 LTV"]
    CHECK --> LTV_EVAL{LTV 值}

    LTV_EVAL -- "≥ danger (75%)" --> PROTECT["🔴 _loan_protect()<br/>買入 + 質押（低買）"]
    LTV_EVAL -- "≥ target (65%)" --> WARN["🟡 警告<br/>接近危險線"]
    LTV_EVAL -- "≤ low (40%)" --> PROFIT["🟢 _loan_take_profit()<br/>減質押 + 賣出（高賣）"]
    LTV_EVAL -- "中間 (40~65%)" --> SAFE["🔵 安全<br/>無需操作"]

    PROTECT --> AI_CHECK{LLM AI 審核}
    PROFIT --> AI_CHECK
    AI_CHECK -- "批准" --> EXECUTE[執行再平衡操作]
    AI_CHECK -- "拒絕" --> SKIP[跳過]
```

---

## 策略 Slot 防重複機制

為避免同一時間區間內策略重複執行，使用時間 slot 機制：

```
slot = minutes_since_midnight ÷ interval_n

例如 interval_n=60（每小時）:
  09:00~09:59 → slot 9
  10:00~10:59 → slot 10

同一 slot 內只執行一次策略，slot 變化即觸發。
```

---

## 完整單輪 Cycle 時序圖

```mermaid
sequenceDiagram
    participant Bot as TradingBot
    participant DB as Supabase
    participant BN as Binance API
    participant Strat as Strategies
    participant LLM as Claude LLM
    participant Risk as RiskManager

    Note over Bot: ═══ Cycle N 開始 ═══

    Bot->>DB: load_config() [熱重載檢查]
    DB-->>Bot: config_json

    loop 每個現貨交易對
        Bot->>BN: fetch_ohlcv(symbol)
        BN-->>Bot: K 線 DataFrame

        Bot->>Risk: check_stop_loss_take_profit()
        Risk-->>Bot: HOLD/SELL

        Bot->>Strat: generate_verdict(df)
        Strat-->>Bot: StrategyVerdict[]

        opt 有非 HOLD 信號 + BUY
            Bot->>Risk: pre_calculate_metrics()
            Risk-->>Bot: RiskMetrics
        end

        opt MTF 啟用
            Bot->>BN: fetch_multi_timeframe()
            BN-->>Bot: 4×DataFrame
            Bot->>Bot: compute_mtf_summary() × 4
        end

        Bot->>LLM: decide(verdicts, portfolio, risk, mtf)
        LLM-->>Bot: LLMDecision{action, confidence, horizon}

        opt BUY 通過
            Bot->>Risk: evaluate(BUY, horizon, llm_size_pct)
            Risk-->>Bot: RiskOutput
            Bot->>BN: market_order(BUY) + OCO(SL/TP)
            Bot->>DB: insert_order + upsert_position
        end
    end

    Bot->>DB: insert_balances()
    Bot->>DB: update_bot_status(heartbeat)
    Bot->>DB: flush_logs()

    Note over Bot: sleep(60s) → Cycle N+1
```

---

## 檔案索引

| 檔案 | 職責 |
|------|------|
| `bot/app.py` | 主交易迴圈、流程編排 |
| `bot/config/settings.py` | 配置管理（frozen dataclass） |
| `bot/data/fetcher.py` | K 線抓取 + TTL 快取 |
| `bot/strategy/router.py` | 策略結論收集器 |
| `bot/strategy/base.py` | 策略基類 |
| `bot/strategy/*.py` | 各策略實現 |
| `bot/llm/decision_engine.py` | LLM 決策引擎 |
| `bot/llm/prompts.py` | 提示詞模板 |
| `bot/llm/summarizer.py` | 資料摘要化 |
| `bot/llm/schemas.py` | LLMDecision / PortfolioState |
| `bot/risk/manager.py` | 現貨風控（Horizon 動態） |
| `bot/risk/futures_manager.py` | 合約風控 |
| `bot/risk/metrics.py` | RiskMetrics dataclass |
| `bot/execution/executor.py` | 現貨訂單執行 |
| `bot/execution/futures_executor.py` | 合約訂單執行 |
| `bot/utils/indicators.py` | 技術指標計算 |
| `bot/db/supabase_client.py` | Supabase 寫入層 |
| `bot/exchange/binance_client.py` | ccxt 現貨封裝 |
| `bot/exchange/futures_client.py` | ccxt 合約封裝 |
