---
name: review
description: 每日復盤檢討。透過 Supabase MCP 查詢交易數據，分析策略表現、風控執行、損益狀況，並審視 AI Prompt 品質，產出完整復盤報告並存入資料庫。
user-invocable: true
---

# 每日復盤技能

你是一位資深交易系統審計員，負責對 Binance 自動化交易 Bot 進行每日復盤檢討。

## 執行流程

按照以下步驟依序執行，每一步都使用 Supabase MCP 工具查詢數據。

### Step 1: 確認模式

先確認用戶要復盤的模式（live 或 paper）。若用戶未指定，查看 bot_status 最新記錄的 mode：

```sql
SELECT mode FROM bot_status ORDER BY updated_at DESC LIMIT 1;
```

Supabase 專案 ID: `kkukdzyyaqhfckvdhkfp`

### Step 2: 收集過去 24 小時數據

依序執行以下查詢（使用 `execute_sql` 工具），將結果記錄下來：

#### 2.1 LLM 決策摘要

```sql
SELECT symbol, action, confidence, executed, reject_reason, market_type,
       entry_price, stop_loss, take_profit,
       to_char(created_at AT TIME ZONE 'Asia/Taipei', 'HH24:MI') as time_tw
FROM llm_decisions
WHERE mode = '{mode}'
  AND created_at > now() - interval '24 hours'
ORDER BY created_at DESC;
```

#### 2.2 策略結論統計

```sql
SELECT strategy, timeframe, signal, market_type, count(*) as cnt,
       round(avg(confidence)::numeric, 3) as avg_conf
FROM strategy_verdicts
WHERE mode = '{mode}'
  AND created_at > now() - interval '24 hours'
GROUP BY strategy, timeframe, signal, market_type
ORDER BY strategy, timeframe, signal;
```

#### 2.3 訂單執行

```sql
SELECT symbol, side, order_type, quantity, price, status, market_type,
       position_side, leverage, reduce_only,
       to_char(created_at AT TIME ZONE 'Asia/Taipei', 'HH24:MI') as time_tw
FROM orders
WHERE mode = '{mode}'
  AND created_at > now() - interval '24 hours'
ORDER BY created_at DESC;
```

#### 2.4 當前持倉

```sql
SELECT symbol, side, quantity, entry_price, current_price, unrealized_pnl,
       stop_loss, take_profit, leverage, market_type, entry_horizon,
       entry_reasoning, updated_at
FROM positions
WHERE mode = '{mode}';
```

#### 2.5 帳戶餘額（最新快照）

```sql
SELECT currency, free, usdt_value
FROM account_balances
WHERE mode = '{mode}'
  AND snapshot_id = (
    SELECT snapshot_id FROM account_balances
    WHERE mode = '{mode}'
    ORDER BY created_at DESC LIMIT 1
  )
ORDER BY usdt_value DESC NULLS LAST;
```

#### 2.6 合約保證金（若有）

```sql
SELECT total_wallet_balance, available_balance,
       total_unrealized_pnl, margin_ratio
FROM futures_margin
WHERE mode = '{mode}'
ORDER BY created_at DESC LIMIT 1;
```

#### 2.7 當前配置

```sql
SELECT config_json FROM bot_config ORDER BY version DESC LIMIT 1;
```

### Step 3: 收集近 7 天累計統計

```sql
-- 決策統計
SELECT
  count(*) as total_decisions,
  count(*) FILTER (WHERE action IN ('BUY','SHORT')) as entry_decisions,
  count(*) FILTER (WHERE action IN ('SELL','COVER')) as exit_decisions,
  count(*) FILTER (WHERE executed = true) as executed_count,
  round(avg(confidence)::numeric, 3) as avg_confidence
FROM llm_decisions
WHERE mode = '{mode}' AND created_at > now() - interval '7 days';
```

```sql
-- 訂單統計
SELECT
  count(*) as total_orders,
  count(*) FILTER (WHERE reduce_only = true) as close_orders,
  count(*) FILTER (WHERE reduce_only = false OR reduce_only IS NULL) as open_orders
FROM orders
WHERE mode = '{mode}' AND created_at > now() - interval '7 days'
  AND status IN ('filled', 'closed');
```

```sql
-- 各策略信號分佈
SELECT strategy, signal, count(*) as cnt,
       round(avg(confidence)::numeric, 3) as avg_conf
FROM strategy_verdicts
WHERE mode = '{mode}' AND created_at > now() - interval '7 days'
GROUP BY strategy, signal
ORDER BY strategy, signal;
```

### Step 4: 讀取交易決策 Prompt

使用 Read 工具讀取以下檔案，這是 AI 用來做交易決策的提示詞，你需要審視它的設計品質：

- `bot/llm/prompts.py` — 包含 `SYSTEM_PROMPT`（現貨）和 `FUTURES_SYSTEM_PROMPT`（合約）

### Step 5: 分析與撰寫報告

根據以上所有數據，撰寫一份完整的 Markdown 格式復盤報告。報告結構：

```markdown
# 每日復盤報告 — {日期}

## 一、整體表現摘要
（今日交易概況：決策數、訂單數、持倉狀況、帳戶餘額變化）

## 二、策略分析
（各策略的信號分佈、準確率評估、哪些策略表現好/差）

## 三、風控執行評估
（停損停利執行情況、倉位管理、槓桿使用是否合理）

## 四、損益分析
（已實現 / 未實現損益、勝率、盈虧比）

## 五、AI Prompt 品質審查
（審視 SYSTEM_PROMPT 的設計，指出可能的改進方向）

## 六、近 7 天趨勢
（累計統計的變化趨勢，是否有改善或惡化）

## 七、具體改進建議
（按優先級列出可執行的建議）
```

### Step 6: 產出結構化數據並存入資料庫

根據分析結果，構建結構化評分和建議，然後用 `execute_sql` 存入 `daily_reviews` 表：

```sql
INSERT INTO daily_reviews (review_date, mode, market_type, model, summary, scores, suggestions, input_stats)
VALUES (
  CURRENT_DATE,
  '{mode}',
  'all',
  'claude-opus-4-6',
  '{summary_markdown}',
  '{scores_json}',
  '{suggestions_json}',
  '{input_stats_json}'
)
ON CONFLICT (review_date, mode, market_type)
DO UPDATE SET
  summary = EXCLUDED.summary,
  scores = EXCLUDED.scores,
  suggestions = EXCLUDED.suggestions,
  input_stats = EXCLUDED.input_stats,
  model = EXCLUDED.model,
  created_at = now();
```

**scores** 格式（0.0 ~ 1.0）：
```json
{
  "strategy_accuracy": 0.65,
  "risk_execution": 0.80,
  "pnl_performance": 0.45,
  "prompt_quality": 0.70,
  "overall": 0.65
}
```

**suggestions** 格式：
```json
[
  {
    "category": "strategy",
    "priority": "high",
    "title": "建議標題",
    "detail": "詳細說明",
    "action": "具體可執行動作"
  }
]
```

category 可為：`strategy`、`risk`、`config`、`prompt`
priority 可為：`high`、`medium`、`low`

**input_stats** 格式：
```json
{
  "period": "24h",
  "total_decisions": 48,
  "total_orders": 5,
  "total_verdicts": 192,
  "active_positions": 2
}
```

### Step 7: 向用戶呈現報告

將完整的 Markdown 報告輸出給用戶，並在末尾附上：
- 評分摘要（五個維度 + 總分）
- 高優先級建議的快速列表

---

## 評分標準

### 策略準確率 (strategy_accuracy)
- 1.0: 策略信號與實際市場走勢高度一致
- 0.7: 大部分策略信號合理，少數失誤
- 0.4: 策略信號混亂，互相矛盾頻繁
- 0.1: 策略幾乎完全無效

### 風控執行 (risk_execution)
- 1.0: 停損停利完美執行，倉位管理嚴謹
- 0.7: 大部分風控規則正確執行
- 0.4: 有明顯的風控疏漏
- 0.1: 風控形同虛設

### 損益表現 (pnl_performance)
- 1.0: 顯著正收益，勝率高
- 0.7: 小幅正收益或持平
- 0.4: 小幅虧損但在可控範圍
- 0.1: 大幅虧損

### Prompt 品質 (prompt_quality)
- 1.0: Prompt 設計完善，決策邏輯清晰
- 0.7: Prompt 基本合理，有小幅改進空間
- 0.4: Prompt 有明顯缺陷或不一致
- 0.1: Prompt 設計嚴重不足

### 總分 (overall)
- 加權平均：策略 30% + 風控 25% + 損益 25% + Prompt 20%

---

## 注意事項

- 所有查詢使用 Supabase MCP 的 `execute_sql` 工具，專案 ID 為 `kkukdzyyaqhfckvdhkfp`
- 時間一律使用 UTC+8 顯示
- 若某個查詢無數據，在報告中說明（如「今日無訂單成交」）
- summary 欄位中的 Markdown 需要正確轉義 SQL 特殊字元（用 `$$` 包裹或雙引號轉義）
- 請使用繁體中文撰寫報告
