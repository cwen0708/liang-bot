# 代碼審查報告

> 審查日期：2025-02
> 審查範圍：Bot 核心交易流程、風控模組、LLM 決策引擎、合約模組
> 審查方式：三位獨立代理人平行審查（安全性、流程邏輯、代碼品質）

---

## 審查摘要

| 優先級 | 問題數 | 狀態 |
|--------|--------|------|
| P0（立即修復） | 4 | ✅ 全部修復 |
| P1（本週修復） | 2 | ✅ 全部修復 |
| P2（近期改善） | 2 | ✅ 全部修復 |
| P3（觀察/建議） | 多項 | 🔲 待評估 |

---

## Agent 1: 安全性審查

### 🔴 高風險

1. **OHLCV 快取記憶體洩漏** (P0-4) ✅ 已修復
   - 位置：`bot/data/fetcher.py` — `_ohlcv_cache`
   - 問題：TTL 快取條目只在讀取時檢查是否過期，但從未主動清理。長時間運行的 daemon 會累積大量過期條目，導致記憶體無限增長。
   - 修復：在每次 `fetch_ohlcv()` 查詢快取前，先遍歷清理所有過期條目。

2. **LLM 輸入/輸出大小無限制** (P2) ✅ 已修復
   - 位置：`bot/llm/decision_engine.py`、`bot/llm/summarizer.py`
   - 問題：策略結論和 LLM 回覆沒有長度上限。若策略產生異常大量文字，可能導致 Claude CLI 呼叫超時或費用異常。
   - 修復：在 summarizer 層從源頭控制（reasoning 500 字截斷、evidence 限 5 條、指標限 8 個）+ action 白名單驗證。不做 prompt 粗暴截斷（正常 ~4k 字元，截斷反而導致 LLM 誤判）。

3. **日誌中的財務資訊** (P2) ✅ 已修復
   - 位置：`bot/logging_config/logger.py`
   - 問題：本地日誌檔案（`data/logs/bot.log`）和 console 輸出包含精確餘額、數量等敏感資訊，可能被他人直接查看。
   - 修復：本地日誌（console + file）透過 `_MaskingFormatter` 遮罩 qty/balance/available 精確數字；Supabase 僅限本人存取，保留完整數據供遠端除錯。

### 🟡 中風險

4. **配置參數缺乏範圍驗證**
   - 位置：`bot/config/settings.py`
   - 問題：`max_position_pct`、`max_daily_loss_pct`、`leverage` 等關鍵風控參數從 config.yaml / Supabase 載入時沒有範圍檢查。前端誤設（如 `max_position_pct=10.0`）會直接生效。
   - 建議：在 `Settings.from_dict()` 或各 dataclass `__post_init__` 中加入 `assert 0 < x < 1` 類型的基本範圍驗證。

5. **LLM JSON 解析過於寬鬆**
   - 位置：`bot/llm/decision_engine.py:_parse_decision()`
   - 問題：使用 regex 提取 JSON，若 LLM 回覆中包含多個 JSON 區塊，會取第一個。此外 `action` 值沒有白名單驗證（雖然後續 `action_map.get()` 會 fallback 為 HOLD）。
   - 建議：加入 `action in ("BUY", "SELL", "HOLD", "SHORT", "COVER")` 明確驗證。

6. **借貸 AI 審核的 JSON 解析**
   - 位置：`bot/app.py:_loan_protect()`、`_loan_take_profit()`
   - 問題：用字串 split 解析 markdown code block，不夠穩健。若 LLM 回覆格式變化可能解析失敗。
   - 建議：統一使用 `_parse_decision()` 類似的 regex 方式，或提取為共用的 `_parse_json_response()` 工具函數。

### 🟢 低風險

7. **交易所 API 密鑰保護** — ✅ 正確，只在 `.env` 中
8. **Supabase service_role key** — ✅ 正確，`.env` + RLS 規則
9. **retry 裝飾器** — ✅ 所有交易所呼叫都有
10. **Paper 模式隔離** — ✅ paper/live 模式正確分離

---

## Agent 2: 流程邏輯審查

### 🔴 高風險

1. **Horizon SL/TP 不一致** (P0-1, P0-2) ✅ 已修復
   - 位置：`bot/risk/manager.py:check_stop_loss_take_profit()`、`bot/risk/futures_manager.py:check_stop_loss_take_profit()`
   - 問題：開倉時根據 LLM 指定的 `horizon`（如 `long`）計算 SL/TP（ATR×2.5 / ATR×5.0），但 `check_stop_loss_take_profit()` 每輪重新計算時固定用 `medium` horizon（ATR×1.5 / ATR×3.0），導致 SL/TP 跳變。
   - 修復：
     - `add_position()` 新增 `stop_loss_price` 和 `take_profit_price` 參數，開倉時儲存
     - `check_stop_loss_take_profit()` 優先使用儲存的價位，無儲存值時才 fallback

2. **`_last_llm_size_pct` 跨幣對污染** (P0-3) ✅ 已修復
   - 位置：`bot/app.py`
   - 問題：`_last_llm_size_pct` 是實例屬性，處理 BTC/USDT 後的 LLM 建議倉位會殘留到 ETH/USDT 的風控計算中。
   - 修復：
     - `__init__` 中初始化 `self._last_llm_size_pct = 0.0`
     - `_process_symbol()` 和 `_process_futures_symbol()` 在 `_make_decision()` 前重置為 0.0
     - 所有 `getattr(self, "_last_llm_size_pct", 0.0)` 改為 `self._last_llm_size_pct`

3. **合約覆蓋時倉位未縮半** (P1-1) ✅ 已修復
   - 位置：`bot/app.py:_execute_futures_open()`
   - 問題：現貨 `_execute_buy()` 中有 `if self._llm_override: quantity /= 2` 邏輯，但合約 `_execute_futures_open()` 沒有對應邏輯。LLM 覆蓋策略時合約不會縮半，風險不對稱。
   - 修復：在 `_execute_futures_open()` 風控通過後、下單前加入相同的覆蓋縮半邏輯。

4. **合約 SHORT/COVER 無持倉狀態驗證** (P1-2) ✅ 已修復
   - 位置：`bot/app.py:_translate_futures_signal()`
   - 問題：LLM 直接回傳 `SHORT` 或 `COVER` 時（而非由 BUY/SELL 轉換），不檢查持倉狀態。已有空倉時仍允許 SHORT，無空倉時仍允許 COVER。
   - 修復：
     - `SHORT`：檢查 `has_short`，若已有空倉 → HOLD
     - `COVER`：檢查 `not has_short`，若無空倉 → HOLD

### 🟡 中風險

5. **策略 slot 重算精度問題**
   - 位置：`bot/app.py` — `_last_strategy_slot` 機制
   - 問題：`minutes_since_midnight // interval_n` 在 UTC 整點附近可能因微小時間差導致 slot 跳變。若 bot 恰好在整點啟動，可能跳過一個 slot。
   - 影響：極端情況下某個策略在 1 個週期內不產生結論，下個週期恢復。不影響交易安全。

6. **OHLCV 快取 TTL 不一致**
   - 位置：`bot/data/fetcher.py`
   - 問題：`cache_ttl` 是參數傳入，不同呼叫者可以傳不同值。若同一個 key 被不同 TTL 存取，清理邏輯可能不一致。
   - 影響：極端情況下快取條目提前過期或延遲清理，不影響正確性。

7. **恢復持倉時不帶 SL/TP 價位**
   - 位置：`bot/app.py:_restore_positions()`
   - 問題：從 Supabase 恢復持倉時只傳 `symbol`、`qty`、`entry`，不傳 SL/TP 價位。恢復後的持倉會用 fallback medium horizon 計算 SL/TP。
   - 建議：`_restore_positions()` 和 `_restore_futures_positions()` 從 Supabase 讀取 `stop_loss` 和 `take_profit` 欄位並傳入 `add_position()`。

### 🟢 低風險

8. **BUY → SELL 邏輯** — ✅ 正確（風控 evaluate + executor）
9. **停損停利觸發流程** — ✅ 正確（paper 輪詢 + live OCO 同步）
10. **LLM 覆蓋邏輯** — ✅ 正確（高信心 ≥ 0.7 + 縮半）
11. **每日虧損限制** — ✅ 正確（只阻止 BUY，不阻止 SELL）
12. **合約清算價計算** — ✅ 合理（簡化但保守）
13. **MTF 只在有信號時觸發** — ✅ 正確（`only_on_signal` 機制）

---

## Agent 3: 代碼品質審查

### 🟡 中風險

1. **`app.py` 過大**
   - 現況：1600+ 行，承擔主迴圈、現貨、合約、借貸四大職責
   - 建議：將借貸邏輯（`_check_loan_health` / `_loan_protect` / `_loan_take_profit`）提取到獨立模組 `bot/loan/guard.py`

2. **`_open_positions` 直接存取**
   - 位置：`bot/app.py:_build_portfolio_state()` 直接讀 `risk_manager._open_positions`
   - 問題：違反封裝，若 `_open_positions` 結構變更會影響多處
   - 建議：在 `RiskManager` 上新增 `get_all_positions()` 公開方法

3. **合約 + 現貨策略共用可能混淆**
   - 位置：`bot/app.py:_create_futures_strategies()` — 若 futures.strategies 為空則 `self._futures_strategies = self.strategies`
   - 問題：共用同一個 list 引用，若策略有內部狀態會交叉影響
   - 影響：目前策略是無狀態的（除了訂單流），暫無問題

### 🟢 低風險 / 建議

4. **型別標註完整度** — 大部分核心函數有完整標註 ✅
5. **日誌一致性** — 使用 `_L1` / `_L2` / `_L3` 前綴統一縮排 ✅
6. **錯誤處理** — 策略/MTF 失敗不阻塞主流程 ✅
7. **配置不可變** — frozen dataclass ✅
8. **retry 裝飾器** — 所有交易所 API 呼叫都有 ✅

9. **建議新增測試**：
   - `test_risk_manager.py` — 測試 SL/TP 儲存與 fallback 邏輯
   - `test_futures_risk_manager.py` — 測試合約 SL/TP + 清算價
   - `test_translate_futures_signal.py` — 測試 SHORT/COVER 狀態驗證
   - `test_ohlcv_cache.py` — 測試快取過期清理

10. **建議新增 type stub**：
    - `bot/exchange/base.py` 的 `get_ohlcv()` 返回型別標為 `pd.DataFrame`
    - `bot/risk/metrics.py` 的 `RiskMetrics` 可考慮用 `@dataclass(frozen=True)`

---

## 修復歷程

### P0 修復（已完成）

| # | 問題 | 檔案 | 修復方式 |
|---|------|------|----------|
| P0-1 | 現貨 SL/TP horizon 不一致 | `risk/manager.py`、`app.py` | `add_position()` 存 SL/TP；`check_stop_loss_take_profit()` 用存儲值 |
| P0-2 | 合約 SL/TP horizon 不一致 | `risk/futures_manager.py`、`app.py` | 同上，合約版本 |
| P0-3 | `_last_llm_size_pct` 跨幣對污染 | `app.py` | `__init__` 初始化 + 每交易對重置 + 移除 `getattr` |
| P0-4 | OHLCV 快取記憶體洩漏 | `data/fetcher.py` | 查詢前清理過期條目 |

### P1 修復（已完成）

| # | 問題 | 檔案 | 修復方式 |
|---|------|------|----------|
| P1-1 | 合約覆蓋時倉位未縮半 | `app.py` | `_execute_futures_open()` 加入 `_llm_override` 檢查 |
| P1-2 | SHORT/COVER 無持倉狀態驗證 | `app.py` | `_translate_futures_signal()` 加入 `has_short` 檢查 |

### P2 修復（已完成）

| # | 問題 | 檔案 | 修復方式 |
|---|------|------|----------|
| P2-1 | LLM 輸入/輸出大小無限制 | `llm/decision_engine.py`、`llm/summarizer.py` | action 白名單驗證、策略 reasoning 500 字截斷、evidence 限 5 條。移除 prompt/response 粗暴截斷（正常 ~4k 字元遠低於 LLM 上限，截斷反而導致誤判風險） |
| P2-2 | 日誌中的財務資訊 | `logging_config/logger.py` | 本地日誌（console + file）透過 `_MaskingFormatter` 遮罩 qty/balance/available 精確數字；Supabase 保留完整數據供遠端除錯 |
