# 開發日誌

## 2026-02-11 — 策略分層間隔 + 前端 UI 全面重構

### 一、策略分層間隔機制（後端）

**目標**：不同策略以不同頻率執行，避免 OHLCV 策略每秒都跑浪費資源。

**實作方式**：
- 每個策略設有 `interval_n`（分鐘）：`tia_orderflow=1`、其餘 OHLCV 策略=60
- 改用 slot-based 機制：`slot = minutes_since_midnight // interval_n`
- 主循環以 `last_slot` dict 做去重，同一 slot 內不重複執行
- 所有觸發的 verdict 一次送給 LLM（LLM 決定權重）
- LLM 失敗 → HOLD（移除舊的 weighted_vote fallback）
- 移除 `weight`、`fallback_weights`、`weighted_vote` 邏輯
- 單一 risk config（不再拆分）

**驗證**：Bot 重啟後 log 確認：
- 第一輪：所有策略執行（`last_slot=-1`）
- 第二輪：僅 `tia_orderflow` 執行（OHLCV 策略因 slot 相同被跳過）

---

### 二、前端 UI 全面重構

#### 2.1 全局字體放大
- `style.css` 新增 `html { font-size: 17px }`
- 所有頁面字體升級：
  - 頁面標題：`text-2xl md:text-3xl`
  - 內容正文：`text-base`
  - 副標籤：`text-sm`
  - 底部 Tab：`text-xs`
  - 圖表 fontSize：11

#### 2.2 Dashboard 重構
- **頂部 3 卡片**：狀態/總資產/持倉 → **總資產/現貨/借貸**
  - 總資產 = 帳戶現貨 + 借貸淨值
  - 現貨 = `bot.totalUsdt`
  - 借貸 = `bot.netLoanValue`（質押 USDT 估值 - 負債）
- **區段順序**：帳戶餘額 → 借貸餘額（原「借貸健康度」，已改名移位） → 最近訂單 & AI 決策
- 借貸餘額改用 `bot.loans`（store 已去重），避免 `useRealtimeTable` 原始記錄重複問題
- LTV 顏色門檻：danger >= 75%、warning >= 70%、success < 40%

#### 2.3 TradingPage 調整
- 移除「持倉中」區塊（移至 OrdersPage）
- 圖表下方新增價格卡片 grid（`grid-cols-2 md:grid-cols-4`）

#### 2.4 OrdersPage 完全重寫
- 統一 Position + Order 列表（`UnifiedItem` interface）
- 仿 StrategyPage 過濾 UI：
  - Row 1：左「訂單」標題 + 右下拉選幣種
  - Row 2：左按鈕組（全部/持倉中/掛單中/已關閉）+ 右上下箭頭切換
- 桌面版：表格 + Mobile：卡片
- 按時間降序排列

#### 2.5 其餘頁面字體升級
- StrategyPage、LoanGuardPage、ConfigPage、LogsPage 全部完成字體放大

#### 2.6 App.vue 側邊欄修正
- 新增 `bot.fetchLoans()` 在 `onMounted`
- 側邊欄加上 `h-screen sticky top-0`：固定不隨內容滾動

#### 2.7 Store 擴充（stores/bot.ts）
- 新增 `loans` ref、`netLoanValue` computed、`totalAssets` computed
- 新增 `fetchLoans()`：查詢 `loan_health`，按 `collateral_coin/loan_coin` 去重
- Realtime 訂閱 `loan_health` INSERT 事件

---

### 三、技術筆記

| 項目 | 說明 |
|------|------|
| Windows PowerShell | `powershell -Command "Set-Location '...'; pnpm run build 2>&1"` 可正確捕獲輸出 |
| 借貸去重 | `fetchLoans` 用 `Set<string>` 以 `collateral_coin/loan_coin` 為 key 去重 |
| latestPrices key | 格式為 `'BTC/USDT'`，LoanHealth 的 `collateral_coin` 為 `'BTC'`，需拼接 |
| Sidebar 固定 | `h-screen sticky top-0` on `<aside>`，搭配 `flex min-h-screen` 父容器 |
| 圖表時區 | `getTimezoneOffset() * -60` 加到 Unix timestamp |

### 四、修改檔案清單

- `frontend/src/style.css` — 全局字體
- `frontend/src/stores/bot.ts` — loans/netLoanValue/totalAssets
- `frontend/src/App.vue` — fetchLoans + sidebar sticky + 字體
- `frontend/src/pages/DashboardPage.vue` — 3 卡片 + 借貸餘額重構
- `frontend/src/pages/TradingPage.vue` — 移除持倉 + 新增價格卡片
- `frontend/src/pages/OrdersPage.vue` — 完全重寫
- `frontend/src/pages/StrategyPage.vue` — 字體升級
- `frontend/src/pages/LoanGuardPage.vue` — 字體升級
- `frontend/src/pages/ConfigPage.vue` — 字體升級
- `frontend/src/pages/LogsPage.vue` — 字體升級
- `.claude/skills/frontend-design/SKILL.md` — 從 gep-token 複製
- `.claude/skills/web-design-guidelines/SKILL.md` — 從 gep-token 複製
