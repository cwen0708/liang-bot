<script setup lang="ts">
import { ref, computed, onMounted, reactive, watch, nextTick } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import type { BotConfig } from '@/types'

// ─── Schema ─────────────────────────────────────────────────
type FieldType = 'string' | 'number' | 'integer' | 'boolean' | 'select' | 'percent' | 'tags'

interface SchemaEntry {
  label: string
  desc?: string
  type?: FieldType
  options?: string[]
  step?: number
}

const DEFAULT_SCHEMA: Record<string, SchemaEntry> = {
  // Spot（現貨交易 + 風控）
  'spot':                             { label: '現貨交易', desc: '現貨交易參數與風控設定（合併交易與風險管理）' },
  'spot.mode':                        { label: '交易模式', type: 'select', options: ['paper', 'live'], desc: 'paper = 模擬交易（不實際下單），live = 真實交易。切換前請確認資金與策略已就緒' },
  'spot.pairs':                       { label: '交易對', type: 'tags', desc: 'Bot 監控的幣安現貨交易對清單，格式為 BTC/USDT。每輪 cycle 會依序掃描所有交易對' },
  'spot.timeframe':                   { label: 'K 線週期', type: 'select', options: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'], desc: '策略分析使用的 K 線時間框架。較短週期信號多但雜訊大，較長週期信號穩但反應慢' },
  'spot.check_interval_seconds':      { label: '檢查間隔', type: 'integer', desc: '每輪交易循環之間的等待秒數。過短會增加 API 請求頻率，過長可能錯過進場時機' },
  'spot.max_position_pct':            { label: '最大部位', type: 'percent', step: 0.01, desc: '單筆交易最多使用可用餘額的百分比。例如 10% 表示每筆最多投入總資金的 10%' },
  'spot.stop_loss_pct':               { label: '停損', type: 'percent', step: 0.01, desc: '持倉虧損達此百分比時自動賣出止損。例如 3% 表示虧損超過買入價的 3% 即觸發停損' },
  'spot.take_profit_pct':             { label: '停利', type: 'percent', step: 0.01, desc: '持倉獲利達此百分比時自動賣出止盈。例如 6% 表示獲利超過買入價的 6% 即觸發停利' },
  'spot.max_open_positions':          { label: '最大持倉數', type: 'integer', desc: '同時持有的最大倉位數量。超過此數量時不會開新倉，避免過度分散資金' },
  'spot.max_daily_loss_pct':          { label: '每日虧損上限', type: 'percent', step: 0.01, desc: '當日累計虧損達此百分比時，Bot 停止開新倉直到隔日。防止單日過度損失' },
  'spot.min_risk_reward':             { label: '最低盈虧比', type: 'number', step: 0.1, desc: '開倉前的盈虧比 (R:R) 最低門檻。例如 1.5 表示潛在獲利必須是潛在虧損的 1.5 倍以上才開倉' },
  'spot.atr':                         { label: 'ATR 動態停損', desc: 'ATR（平均真實波幅）動態停損停利配置。啟用後停損停利距離會根據市場波動自動調整' },
  'spot.atr.enabled':                 { label: '啟用動態 SL/TP', type: 'boolean', desc: '啟用 ATR 動態停損停利。關閉則使用固定百分比（stop_loss_pct / take_profit_pct）。建議開啟以適應不同市場波動度' },
  'spot.atr.period':                  { label: '計算週期', type: 'integer', desc: 'ATR 計算的 K 線回看週期（預設 14）。用於動態計算停損停利距離' },
  'spot.atr.sl_multiplier':           { label: '停損倍率', type: 'number', step: 0.1, desc: '停損距離 = ATR × 此倍率。例如 1.5 表示停損設在 1.5 倍 ATR 距離' },
  'spot.atr.tp_multiplier':           { label: '停利倍率', type: 'number', step: 0.1, desc: '停利距離 = ATR × 此倍率。與停損倍率的比值決定盈虧比（例如 3.0/1.5=2.0）' },
  // Backtest
  'backtest':                         { label: '回測設定', desc: '歷史回測引擎的參數，用於驗證策略在過往數據上的表現' },
  'backtest.start_date':              { label: '起始日期', type: 'string', desc: '回測的開始日期，格式 YYYY-MM-DD' },
  'backtest.end_date':                { label: '結束日期', type: 'string', desc: '回測的結束日期，格式 YYYY-MM-DD' },
  'backtest.initial_balance':         { label: '初始餘額', type: 'number', desc: '回測起始的模擬資金（USDT），用於計算報酬率與最大回撤' },
  'backtest.commission_pct':          { label: '手續費率', type: 'percent', step: 0.001, desc: '回測中模擬的每筆交易手續費率。幣安現貨一般為 0.1%，使用 BNB 扣抵可降至 0.075%' },
  // Order Flow
  'orderflow':                        { label: 'Order Flow 設定', desc: 'Order Flow 分析引擎的參數，透過逐筆成交分析市場微結構' },
  'orderflow.bar_interval_seconds':   { label: 'Bar 間隔', type: 'integer', desc: 'Order Flow Bar 的時間間隔（秒）。較短間隔可捕捉更細微的成交變化' },
  'orderflow.tick_size':              { label: 'Tick 大小', type: 'number', step: 0.01, desc: '價格聚合的最小刻度。影響 footprint chart 的價格分層精細度' },
  'orderflow.cvd_lookback':           { label: 'CVD 回看期', type: 'integer', desc: '累積成交量差（Cumulative Volume Delta）的回看 Bar 數。用於判斷買賣力道趨勢' },
  'orderflow.zscore_lookback':        { label: 'Z-score 回看期', type: 'integer', desc: '計算成交量 Z-score 的回看期，用於偵測異常成交量（大單吸收等）' },
  'orderflow.divergence_peak_order':  { label: '背離峰值階數', type: 'integer', desc: '偵測價格與 CVD 背離時，尋找局部峰值的階數。數值越大要求越明確的峰值' },
  'orderflow.sfp_swing_lookback':     { label: 'SFP 擺動回看', type: 'integer', desc: 'Swing Failure Pattern 偵測的回看 Bar 數。SFP 用於識別假突破後的反轉信號' },
  'orderflow.absorption_lookback':    { label: '吸收量回看', type: 'integer', desc: '大單吸收偵測的回看期。當價格不動但成交量異常放大，可能代表大戶在吸收賣壓' },
  'orderflow.signal_threshold':       { label: '信號閾值', type: 'number', step: 0.1, desc: 'Order Flow 綜合信號的觸發閾值。數值越高要求越強的信號才會發出 BUY/SELL' },
  // Strategies array
  'strategies':                       { label: '現貨策略', desc: '現貨交易使用的策略清單，每個策略獨立執行並產生信號，最終由 LLM 彙整決策' },
  // LLM
  'llm':                              { label: 'LLM 決策引擎', desc: '所有非 HOLD 信號強制經過 LLM（Claude）審核，作為最終買賣決策的把關者' },
  'llm.enabled':                      { label: '啟用 LLM', type: 'boolean', desc: '開啟後所有 BUY/SELL 信號會送交 Claude 進行 AI 審核。關閉則直接執行策略信號' },
  'llm.cli_path':                     { label: 'CLI 路徑', type: 'string', desc: 'Claude CLI 的可執行檔路徑，Bot 透過此路徑呼叫 LLM 進行決策' },
  'llm.model':                        { label: '模型', type: 'string', desc: '使用的 Claude 模型名稱，例如 claude-sonnet-4-5-20250929' },
  'llm.timeout':                      { label: '逾時', type: 'integer', desc: 'LLM 呼叫的最長等待秒數。超時則視為 HOLD（不執行交易），避免阻塞交易循環' },
  'llm.min_confidence':               { label: '最低信心', type: 'number', step: 0.1, desc: 'LLM 回覆的信心分數門檻（0-1）。低於此值的決策會被降級為 HOLD' },
  // Loan Guard
  'loan_guard':                       { label: '借貸守衛', desc: '借貸再平衡機制，依 LTV（貸款價值比）自動執行保護操作' },
  'loan_guard.enabled':               { label: '啟用', type: 'boolean', desc: '開啟借貸守衛功能。啟用後會定期檢查 LTV 並在必要時自動買入質押或減倉' },
  'loan_guard.target_ltv':            { label: '目標 LTV', type: 'percent', step: 0.01, desc: '目標貸款價值比。LTV 超過此值時發出警告，接近 danger 時觸發保護' },
  'loan_guard.danger_ltv':            { label: '危險 LTV', type: 'percent', step: 0.01, desc: 'LTV 達此值時觸發緊急保護：自動買入並質押（低買策略），防止被強制清算' },
  'loan_guard.low_ltv':               { label: '低 LTV', type: 'percent', step: 0.01, desc: 'LTV 低於此值時觸發獲利：自動減質押並賣出（高賣策略），釋放多餘抵押品' },
  'loan_guard.dry_run':               { label: '模擬模式', type: 'boolean', desc: '開啟後只記錄操作意圖但不實際執行交易，用於測試 LTV 判斷邏輯是否正確' },
  // Futures
  'futures':                              { label: '合約交易', desc: 'USDT-M 永續合約交易模組。獨立於現貨，有專屬的交易對、槓桿、風控參數' },
  'futures.enabled':                      { label: '啟用合約', type: 'boolean', desc: '開啟合約交易模組。啟用後 Bot 會在每輪 cycle 額外處理合約交易對' },
  'futures.mode':                         { label: '交易模式', type: 'select', options: ['paper', 'live'], desc: 'paper = 模擬交易（不實際下單），live = 真實合約交易。合約風險較高，建議先用 paper 模式測試' },
  'futures.pairs':                        { label: '合約交易對', type: 'tags', desc: '合約交易對清單，格式為 BTC/USDT:USDT（ccxt swap 格式）' },
  'futures.leverage':                     { label: '槓桿倍數', type: 'integer', desc: '預設槓桿倍數。會被 max_leverage 上限限制。例如 3 表示 3 倍槓桿' },
  'futures.max_leverage':                 { label: '最大槓桿', type: 'integer', desc: '允許的最大槓桿倍數上限，防止意外設定過高槓桿' },
  'futures.margin_type':                  { label: '保證金模式', type: 'select', options: ['cross', 'isolated'], desc: 'cross = 全倉（共用保證金），isolated = 逐倉（每個倉位獨立保證金）' },
  'futures.timeframe':                    { label: 'K 線週期', type: 'select', options: ['1m', '5m', '15m', '30m', '1h', '4h', '1d'], desc: '合約策略分析使用的 K 線週期，可與現貨使用不同週期' },
  'futures.check_interval_seconds':       { label: '檢查間隔', type: 'integer', desc: '合約交易循環的等待秒數' },
  'futures.max_position_pct':             { label: '最大部位', type: 'percent', step: 0.01, desc: '單筆合約交易最多使用可用保證金的百分比。注意：實際名義價值 = 保證金 × 此比例 × 槓桿' },
  'futures.stop_loss_pct':                { label: '停損（固定）', type: 'percent', step: 0.01, desc: '固定百分比停損。當 use_atr_stops=true 時僅作為 ATR 數據不足時的 fallback' },
  'futures.take_profit_pct':              { label: '停利（固定）', type: 'percent', step: 0.01, desc: '固定百分比停利。當 use_atr_stops=true 時僅作為 ATR 數據不足時的 fallback' },
  'futures.max_open_positions':           { label: '最大持倉數', type: 'integer', desc: '合約同時持有的最大倉位數量（多倉+空倉合計）' },
  'futures.max_daily_loss_pct':           { label: '每日虧損上限', type: 'percent', step: 0.01, desc: '合約當日累計虧損達此比例時停止開新倉。單筆交易帳戶風險不超過此值的一半' },
  'futures.max_margin_ratio':             { label: '保證金比率警戒', type: 'percent', step: 0.01, desc: '帳戶保證金比率超過此值時拒絕開新倉，防止觸發強制清算' },
  'futures.funding_rate_threshold':       { label: '資金費率閾值', type: 'number', step: 0.001, desc: '資金費率超過此值時作為風險信號。正值表示多頭付空頭，負值反之' },
  'futures.min_risk_reward':              { label: '最低盈虧比', type: 'number', step: 0.1, desc: '開倉前的盈虧比 (R:R) 最低門檻。例如 1.5 表示潛在獲利必須是潛在虧損的 1.5 倍以上才開倉' },
  'futures.atr':                          { label: 'ATR 動態停損', desc: 'ATR（平均真實波幅）動態停損停利配置' },
  'futures.atr.period':                   { label: '計算週期', type: 'integer', desc: 'ATR 計算的 K 線回看週期。用於動態計算停損停利距離' },
  'futures.atr.sl_multiplier':            { label: '停損倍率', type: 'number', step: 0.1, desc: '停損距離 = ATR × 此倍率。例如 1.5 表示停損設在 1.5 倍 ATR 距離' },
  'futures.atr.tp_multiplier':            { label: '停利倍率', type: 'number', step: 0.1, desc: '停利距離 = ATR × 此倍率。與停損倍率的比值決定盈虧比（例如 3.0/1.5=2.0）' },
  'futures.atr.enabled':                  { label: '啟用動態 SL/TP', type: 'boolean', desc: '啟用 ATR 動態停損停利。關閉則使用固定百分比。建議開啟以適應不同市場波動度' },
  'futures.strategies':                   { label: '合約策略清單', desc: '合約專屬策略清單，為空則共用現貨策略' },
  'futures.min_confidence':               { label: 'LLM 信心門檻', type: 'number', step: 0.1, desc: '合約 LLM 決策的最低信心分數（0-1）。低於此值降級為 HOLD' },
  // Multi-Timeframe
  'mtf':                              { label: '多時間框架', desc: '多時間框架分析配置，讓 LLM 同時參考日線到 15 分鐘線的技術指標' },
  'mtf.enabled':                      { label: '啟用 MTF', type: 'boolean', desc: '啟用多時間框架分析。啟用後每輪 cycle 額外抓取多個時間框架的 K 線供 LLM 參考' },
  'mtf.timeframes':                   { label: '時間框架', desc: '要分析的時間框架清單（如 ["1d", "4h", "1h", "15m"]）' },
  'mtf.candle_limit':                 { label: 'K 線數量', type: 'integer', desc: '每個額外時間框架抓取的 K 線數量。至少 20 根才能計算技術指標' },
  'mtf.cache_ttl_seconds':            { label: '快取秒數', type: 'integer', desc: 'K 線資料快取時間（秒）。同一 cycle 內相同 symbol+TF 不重複抓取' },
  // Horizon Risk
  'horizon_risk':                     { label: '持倉週期風控', desc: '根據 LLM 建議的持倉週期（短/中/長線）動態調整風控參數' },
  'horizon_risk.short_sl_multiplier': { label: '短線停損倍率', type: 'number', step: 0.1, desc: '短線持倉的 ATR 停損倍率（預設 1.0）' },
  'horizon_risk.short_tp_multiplier': { label: '短線停利倍率', type: 'number', step: 0.1, desc: '短線持倉的 ATR 停利倍率（預設 2.0）' },
  'horizon_risk.short_sl_pct':        { label: '短線固定停損%', type: 'number', step: 0.01, desc: '短線固定百分比停損（ATR 不可用時使用）' },
  'horizon_risk.short_tp_pct':        { label: '短線固定停利%', type: 'number', step: 0.01, desc: '短線固定百分比停利（ATR 不可用時使用）' },
  'horizon_risk.short_size_factor':   { label: '短線倉位因子', type: 'number', step: 0.1, desc: '短線倉位大小乘數（>1 放大，<1 縮小）' },
  'horizon_risk.short_min_rr':        { label: '短線最低盈虧比', type: 'number', step: 0.1, desc: '短線交易的最低盈虧比門檻' },
  'horizon_risk.medium_sl_multiplier': { label: '中線停損倍率', type: 'number', step: 0.1, desc: '中線持倉的 ATR 停損倍率（預設 1.5）' },
  'horizon_risk.medium_tp_multiplier': { label: '中線停利倍率', type: 'number', step: 0.1, desc: '中線持倉的 ATR 停利倍率（預設 3.0）' },
  'horizon_risk.medium_sl_pct':       { label: '中線固定停損%', type: 'number', step: 0.01, desc: '中線固定百分比停損' },
  'horizon_risk.medium_tp_pct':       { label: '中線固定停利%', type: 'number', step: 0.01, desc: '中線固定百分比停利' },
  'horizon_risk.medium_size_factor':  { label: '中線倉位因子', type: 'number', step: 0.1, desc: '中線倉位大小乘數（預設 1.0 = 標準）' },
  'horizon_risk.medium_min_rr':       { label: '中線最低盈虧比', type: 'number', step: 0.1, desc: '中線交易的最低盈虧比門檻' },
  'horizon_risk.long_sl_multiplier':  { label: '長線停損倍率', type: 'number', step: 0.1, desc: '長線持倉的 ATR 停損倍率（預設 2.5）' },
  'horizon_risk.long_tp_multiplier':  { label: '長線停利倍率', type: 'number', step: 0.1, desc: '長線持倉的 ATR 停利倍率（預設 5.0）' },
  'horizon_risk.long_sl_pct':         { label: '長線固定停損%', type: 'number', step: 0.01, desc: '長線固定百分比停損' },
  'horizon_risk.long_tp_pct':         { label: '長線固定停利%', type: 'number', step: 0.01, desc: '長線固定百分比停利' },
  'horizon_risk.long_size_factor':    { label: '長線倉位因子', type: 'number', step: 0.1, desc: '長線倉位大小乘數（<1 縮小倉位，長線更保守）' },
  'horizon_risk.long_min_rr':         { label: '長線最低盈虧比', type: 'number', step: 0.1, desc: '長線交易的最低盈虧比門檻' },
  // Logging
  'logging':                          { label: '日誌設定', desc: 'Bot 日誌輸出的等級與儲存方式' },
  'logging.level':                    { label: '日誌等級', type: 'select', options: ['DEBUG', 'INFO', 'WARNING', 'ERROR'], desc: 'DEBUG = 所有細節，INFO = 一般運行，WARNING = 警告，ERROR = 僅錯誤。生產環境建議 INFO' },
  'logging.file_enabled':             { label: '檔案日誌', type: 'boolean', desc: '是否將日誌同時寫入本地檔案。開啟後可用於離線排查問題' },
  'logging.log_dir':                  { label: '日誌目錄', type: 'string', desc: '日誌檔案的儲存資料夾路徑。僅在檔案日誌啟用時有效' },
}

// ─── Dynamic Schema (loaded from Supabase, fallback to DEFAULT_SCHEMA) ──
const configSchema = ref<Record<string, SchemaEntry>>({ ...DEFAULT_SCHEMA })

// ─── Tree Node Types ────────────────────────────────────────
interface TreeNode {
  path: string
  key: string
  depth: number
  isLeaf: boolean
  isArray: boolean
  isObject: boolean
  value: unknown
  label: string
  desc?: string
  fieldType: FieldType | 'object' | 'array'
  schema?: SchemaEntry
  childCount?: number
  arrayIndex?: number     // if item inside array
  strategyName?: string   // preview text for strategies items
}

// ─── State ──────────────────────────────────────────────────
const supabase = useSupabase()
const configs = ref<BotConfig[]>([])
const configData = ref<Record<string, unknown>>({})
const changeNote = ref('')
const saving = ref(false)
const error = ref('')
const expandedPaths = reactive(new Set<string>())
const tagInput = ref<Record<string, string>>({})
const selectedPath = ref('')
const selectedVersionId = ref<number | null>(null)
const mobilePanel = ref<'nav' | 'editor'>('nav')

// Schema editor state
const schemaEditOpen = ref(false)
const schemaEditSaving = ref(false)
const schemaEditForm = reactive({
  label: '',
  description: '',
  field_type: '' as string,
  options: '',      // comma-separated for select
  step: null as number | null,
  sort_order: 0,
})

// Side map to track sort_order (not in SchemaEntry interface)
const schemaEditSortMap: Record<string, number> = {}

// ─── Schema Loading ─────────────────────────────────────────
async function loadSchema() {
  try {
    const { data, error: err } = await supabase
      .from('config_schema')
      .select('*')
      .order('sort_order')
    if (err || !data) throw err || new Error('No data')
    const map: Record<string, SchemaEntry> = {}
    for (const row of data) {
      const entry: SchemaEntry = { label: row.label }
      if (row.description) entry.desc = row.description
      if (row.field_type) entry.type = row.field_type as FieldType
      if (row.options) entry.options = row.options as string[]
      if (row.step != null) entry.step = Number(row.step)
      map[row.path] = entry
      schemaEditSortMap[row.path] = row.sort_order ?? 0
    }
    configSchema.value = map
  } catch {
    // Fallback to hardcoded schema
    configSchema.value = { ...DEFAULT_SCHEMA }
  }
}

// ─── Schema Edit ────────────────────────────────────────────
const FIELD_TYPE_OPTIONS = ['', 'string', 'number', 'integer', 'boolean', 'select', 'percent', 'tags'] as const

watch(selectedPath, (path) => {
  schemaEditOpen.value = false
  if (!path) return
  const s = configSchema.value[path]
  schemaEditForm.label = s?.label ?? ''
  schemaEditForm.description = s?.desc ?? ''
  schemaEditForm.field_type = s?.type ?? ''
  schemaEditForm.options = s?.options?.join(', ') ?? ''
  schemaEditForm.step = s?.step ?? null
  schemaEditForm.sort_order = 0
  if (schemaEditSortMap[path] != null) {
    schemaEditForm.sort_order = schemaEditSortMap[path]
  }
})

async function saveSchemaEntry() {
  const path = selectedPath.value
  if (!path || !schemaEditForm.label.trim()) return
  schemaEditSaving.value = true
  try {
    const optionsArr = schemaEditForm.field_type === 'select' && schemaEditForm.options.trim()
      ? schemaEditForm.options.split(',').map(s => s.trim()).filter(Boolean)
      : null
    const row = {
      path,
      label: schemaEditForm.label.trim(),
      description: schemaEditForm.description.trim(),
      field_type: schemaEditForm.field_type || null,
      options: optionsArr,
      step: schemaEditForm.step,
      sort_order: schemaEditForm.sort_order,
      updated_at: new Date().toISOString(),
    }
    const { error: err } = await supabase
      .from('config_schema')
      .upsert(row, { onConflict: 'path' })
    if (err) throw err
    // Update local schema
    const entry: SchemaEntry = { label: row.label }
    if (row.description) entry.desc = row.description
    if (row.field_type) entry.type = row.field_type as FieldType
    if (optionsArr) entry.options = optionsArr
    if (row.step != null) entry.step = row.step
    configSchema.value = { ...configSchema.value, [path]: entry }
    schemaEditSortMap[path] = row.sort_order
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    schemaEditSaving.value = false
  }
}

// ─── Data Loading ───────────────────────────────────────────
async function loadConfigs() {
  const { data } = await supabase
    .from('bot_config')
    .select('*')
    .order('version', { ascending: false })
    .limit(20)
  if (data) {
    configs.value = data as BotConfig[]
    if (data.length > 0) {
      loadVersion(data[0])
      selectedVersionId.value = data[0].id
    }
  }
}

function loadVersion(config: BotConfig) {
  configData.value = JSON.parse(JSON.stringify(config.config_json))
  selectedVersionId.value = config.id
  selectedPath.value = ''
  // expand top-level keys by default
  expandedPaths.clear()
  for (const key of Object.keys(configData.value)) {
    expandedPaths.add(key)
  }
}

function onVersionSelect(id: number) {
  const config = configs.value.find(c => c.id === id)
  if (config) loadVersion(config)
}

async function saveConfig() {
  saving.value = true
  error.value = ''
  try {
    const nextVersion = (configs.value[0]?.version ?? 0) + 1
    const { error: err } = await supabase.from('bot_config').insert({
      version: nextVersion,
      config_json: configData.value,
      changed_by: 'frontend',
      change_note: changeNote.value || `v${nextVersion} from dashboard`,
    })
    if (err) throw err
    changeNote.value = ''
    await loadConfigs()
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    saving.value = false
  }
}

// ─── Helpers ────────────────────────────────────────────────
function getByPath(obj: Record<string, unknown>, path: string): unknown {
  const parts = path.split('.')
  let cur: unknown = obj
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined
    cur = (cur as Record<string, unknown>)[p]
  }
  return cur
}

function setByPath(obj: Record<string, unknown>, path: string, value: unknown) {
  const parts = path.split('.')
  let cur: Record<string, unknown> = obj
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i]!
    if (cur[p] == null || typeof cur[p] !== 'object') {
      cur[p] = {}
    }
    cur = cur[p] as Record<string, unknown>
  }
  cur[parts[parts.length - 1]!] = value
}

function deleteByPath(obj: Record<string, unknown>, path: string) {
  const parts = path.split('.')
  let cur: Record<string, unknown> = obj
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i]!
    if (cur[p] == null || typeof cur[p] !== 'object') return
    cur = cur[p] as Record<string, unknown>
  }
  const lastKey = parts[parts.length - 1]!
  if (Array.isArray(cur)) {
    cur.splice(Number(lastKey), 1)
  } else {
    delete cur[lastKey]
  }
}

function inferFieldType(value: unknown): FieldType {
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return Number.isInteger(value) ? 'integer' : 'number'
  return 'string'
}

function toggleExpand(path: string) {
  if (expandedPaths.has(path)) {
    expandedPaths.delete(path)
  } else {
    expandedPaths.add(path)
  }
}

// ─── Flatten Config → TreeNode[] ────────────────────────────
function flattenConfig(
  obj: unknown,
  parentPath: string,
  depth: number,
  arrayIndex?: number
): TreeNode[] {
  const nodes: TreeNode[] = []

  if (obj == null || typeof obj !== 'object') return nodes

  const entries = Array.isArray(obj)
    ? obj.map((v, i) => [String(i), v] as const)
    : Object.entries(obj as Record<string, unknown>)

  // Hidden top-level keys (legacy, still in JSON but not shown in UI)
  const HIDDEN_KEYS = new Set(['strategy', 'trading', 'risk'])

  for (const [key, value] of entries) {
    const path = parentPath ? `${parentPath}.${key}` : key

    // Skip hidden legacy sections
    if (!parentPath && HIDDEN_KEYS.has(key)) continue

    const schema = configSchema.value[path]
    const isArr = Array.isArray(value)
    const isObj = !isArr && value !== null && typeof value === 'object'
    const isLeaf = !isArr && !isObj

    // For strategies array items, show strategy name as preview
    let strategyName: string | undefined
    if (parentPath === 'strategies' && isObj) {
      const item = value as Record<string, unknown>
      strategyName = (item.name as string) || `策略 ${key}`
    }

    let label = schema?.label ?? key
    // Array items: show index
    if (arrayIndex !== undefined || parentPath === 'strategies') {
      if (strategyName) {
        label = `[${key}] ${strategyName}`
      } else if (!schema) {
        label = `[${key}]`
      }
    }

    let fieldType: TreeNode['fieldType']
    // Schema type takes precedence (e.g. 'tags' for arrays like trading.pairs)
    if (schema?.type) fieldType = schema.type
    else if (isArr) fieldType = 'array'
    else if (isObj) fieldType = 'object'
    else fieldType = inferFieldType(value)

    // Tags are arrays but rendered inline, not as expandable nodes
    const effectiveIsLeaf = fieldType === 'tags'
    const effectiveIsArr = isArr && fieldType !== 'tags'

    const childCount = isArr
      ? (value as unknown[]).length
      : isObj
        ? Object.keys(value as Record<string, unknown>).length
        : undefined

    nodes.push({
      path,
      key,
      depth,
      isLeaf: effectiveIsLeaf || isLeaf,
      isArray: effectiveIsArr,
      isObject: isObj,
      value,
      label,
      desc: schema?.desc,
      fieldType,
      schema,
      childCount,
      arrayIndex: typeof arrayIndex === 'number' ? arrayIndex : undefined,
      strategyName,
    })

    // Recurse if expanded (but not for tags — they render inline)
    if ((effectiveIsArr || isObj) && expandedPaths.has(path)) {
      const children = flattenConfig(value, path, depth + 1, effectiveIsArr ? 0 : undefined)
      nodes.push(...children)
    }
  }
  return nodes
}

const treeNodes = computed(() => flattenConfig(configData.value, '', 0))

// ─── Selected node computed ─────────────────────────────────
const selectedNode = computed(() => {
  if (!selectedPath.value) return null
  return treeNodes.value.find(n => n.path === selectedPath.value) ?? null
})

function selectLeaf(node: TreeNode) {
  selectedPath.value = node.path
  mobilePanel.value = 'editor'
}

// ─── Value preview for tree ─────────────────────────────────
function previewValue(node: TreeNode): string {
  if (node.fieldType === 'boolean') return node.value ? 'ON' : 'OFF'
  if (node.fieldType === 'percent' && typeof node.value === 'number') {
    return (node.value * 100).toFixed(2).replace(/\.?0+$/, '') + '%'
  }
  if (node.fieldType === 'tags' && Array.isArray(node.value)) {
    return `${(node.value as unknown[]).length} 對`
  }
  if (node.isArray) return `[ ${node.childCount} ]`
  if (node.isObject) return `{ ${node.childCount} }`
  if (node.value == null) return ''
  const s = String(node.value)
  return s.length > 30 ? s.slice(0, 28) + '...' : s
}

// ─── Value Updates ──────────────────────────────────────────
function updateValue(path: string, value: unknown) {
  setByPath(configData.value, path, value)
  // Trigger reactivity
  configData.value = { ...configData.value }
}

function handleNumberInput(path: string, raw: string, isPercent: boolean) {
  const num = parseFloat(raw)
  if (isNaN(num)) return
  updateValue(path, isPercent ? num / 100 : num)
}

function handleIntegerInput(path: string, raw: string) {
  const num = parseInt(raw, 10)
  if (isNaN(num)) return
  updateValue(path, num)
}

// Tags (for trading.pairs etc.)
function addTag(path: string) {
  const input = (tagInput.value[path] || '').trim().toUpperCase()
  if (!input) return
  const arr = (getByPath(configData.value, path) as string[]) || []
  if (!arr.includes(input)) {
    updateValue(path, [...arr, input])
  }
  tagInput.value[path] = ''
}

function removeTag(path: string, index: number) {
  const arr = [...((getByPath(configData.value, path) as string[]) || [])]
  arr.splice(index, 1)
  updateValue(path, arr)
}

// Strategies array add/remove
function addStrategy() {
  const arr = (configData.value.strategies as unknown[]) || []
  const newItem = { name: 'new_strategy', interval_n: 60, params: {} }
  updateValue('strategies', [...arr, newItem])
  expandedPaths.add(`strategies.${arr.length}`)
}

function removeStrategyItem(index: number) {
  const arr = [...((configData.value.strategies as unknown[]) || [])]
  arr.splice(index, 1)
  // Clean up expanded paths for removed/shifted items
  const toRemove: string[] = []
  for (const p of expandedPaths) {
    if (p.startsWith('strategies.')) toRemove.push(p)
  }
  toRemove.forEach(p => expandedPaths.delete(p))
  updateValue('strategies', arr)
  // Re-expand existing items
  arr.forEach((_, i) => expandedPaths.add(`strategies.${i}`))
  // Clear selection if it was pointing to removed item
  if (selectedPath.value.startsWith(`strategies.${index}`)) {
    selectedPath.value = ''
  }
}

// Generic array add/remove for non-strategies arrays
function addArrayItem(path: string) {
  const arr = (getByPath(configData.value, path) as unknown[]) || []
  // Try to infer type from existing items
  if (arr.length > 0) {
    const sample = arr[0]
    if (typeof sample === 'string') updateValue(path, [...arr, ''])
    else if (typeof sample === 'number') updateValue(path, [...arr, 0])
    else if (typeof sample === 'object') updateValue(path, [...arr, {}])
    else updateValue(path, [...arr, ''])
  } else {
    updateValue(path, [...arr, ''])
  }
}

function removeArrayItem(path: string, index: number) {
  const arr = [...((getByPath(configData.value, path) as unknown[]) || [])]
  arr.splice(index, 1)
  updateValue(path, arr)
}

function displayPercentValue(value: unknown): string {
  if (typeof value !== 'number') return ''
  return (value * 100).toFixed(2).replace(/\.?0+$/, '')
}

function formatTime(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', { hour12: false })
}

function formatTimeShort(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    hour12: false,
  })
}

onMounted(async () => {
  await loadSchema()
  await loadConfigs()
})
</script>

<template>
  <div class="p-4 md:p-6 flex flex-col gap-4 md:gap-6 md:h-[calc(100vh)] md:overflow-hidden">
    <!-- Header bar -->
    <div class="flex flex-col sm:flex-row sm:items-center gap-3 shrink-0">
      <h2 class="text-2xl md:text-3xl font-bold shrink-0">設定</h2>

      <!-- Version dropdown -->
      <select
        v-if="configs.length"
        :value="selectedVersionId"
        class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent) min-w-0 max-w-72"
        @change="onVersionSelect(Number(($event.target as HTMLSelectElement).value))"
      >
        <option v-for="c in configs" :key="c.id" :value="c.id">
          v{{ c.version }} - {{ c.changed_by }} - {{ formatTimeShort(c.created_at) }}
        </option>
      </select>

      <span class="flex-1" />

      <!-- Change note + Save -->
      <div class="flex items-center gap-2 min-w-0">
        <input
          v-model="changeNote"
          placeholder="說明..."
          class="w-40 sm:w-48 bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
        />
        <button
          @click="saveConfig"
          :disabled="saving"
          class="px-4 py-1.5 bg-(--color-accent) text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50 whitespace-nowrap shrink-0"
        >
          {{ saving ? '儲存中...' : '儲存新版本' }}
        </button>
      </div>

      <!-- Mobile toggle -->
      <button
        class="md:hidden px-3 py-1.5 bg-(--color-bg-card) border border-(--color-border) rounded-lg text-sm"
        @click="mobilePanel = mobilePanel === 'nav' ? 'editor' : 'nav'"
      >
        {{ mobilePanel === 'nav' ? '編輯面板' : '返回導航' }}
      </button>
    </div>

    <div v-if="error" class="text-(--color-danger) text-sm shrink-0">{{ error }}</div>

    <!-- Main grid -->
    <div class="grid grid-cols-1 md:grid-cols-12 gap-4 md:gap-6 min-h-0 md:flex-1">
      <!-- LEFT: Tree Navigation (read-only) -->
      <div
        class="md:col-span-4 flex flex-col min-h-0"
        :class="{ 'hidden md:flex': mobilePanel === 'editor' }"
      >
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 flex flex-col min-h-0 flex-1">
          <h3 class="text-xs font-semibold text-(--color-text-muted) uppercase tracking-wider mb-3 shrink-0">導航</h3>

          <div class="overflow-auto flex-1 min-h-0 space-y-px">
            <template v-for="node in treeNodes" :key="node.path">
              <div
                class="flex items-center gap-1.5 py-1 px-1.5 rounded-md cursor-pointer transition-colors select-none"
                :class="[
                  selectedPath === node.path
                    ? 'bg-(--color-accent-subtle) border border-(--color-accent)/30'
                    : 'hover:bg-(--color-bg-secondary) border border-transparent',
                ]"
                :style="{ paddingLeft: `${node.depth * 16 + 6}px` }"
                @click="node.isLeaf ? selectLeaf(node) : toggleExpand(node.path)"
              >
                <!-- Expand/Collapse Arrow -->
                <button
                  v-if="!node.isLeaf"
                  class="w-4 h-4 flex items-center justify-center text-(--color-text-muted) shrink-0 transition-transform"
                  :class="{ 'rotate-90': expandedPaths.has(node.path) }"
                  @click.stop="toggleExpand(node.path)"
                >
                  <svg width="10" height="10" viewBox="0 0 12 12" fill="currentColor">
                    <path d="M4 2l4 4-4 4z" />
                  </svg>
                </button>
                <span v-else class="w-4 shrink-0" />

                <!-- Label -->
                <span
                  class="text-sm truncate"
                  :class="node.isLeaf ? 'text-(--color-text-primary)' : 'font-semibold text-(--color-text-primary)'"
                >{{ node.label }}</span>

                <span class="flex-1 min-w-2" />

                <!-- Value preview (gray, small) -->
                <span class="text-xs text-(--color-text-muted) font-mono truncate max-w-24 shrink-0 text-right">
                  {{ previewValue(node) }}
                </span>
              </div>
            </template>

            <!-- Add strategy button -->
            <div
              v-if="expandedPaths.has('strategies')"
              class="py-1 px-1.5"
              :style="{ paddingLeft: '22px' }"
            >
              <button
                class="text-xs text-(--color-accent) hover:text-(--color-accent-hover) font-medium flex items-center gap-1"
                @click="addStrategy"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                新增策略
              </button>
            </div>

            <div v-if="!treeNodes.length" class="text-sm text-(--color-text-secondary) py-8 text-center">
              尚無設定資料
            </div>
          </div>
        </div>
      </div>

      <!-- RIGHT: Field Editor -->
      <div
        class="md:col-span-8 flex flex-col min-h-0"
        :class="{ 'hidden md:flex': mobilePanel === 'nav' }"
      >
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4 md:p-6 flex flex-col min-h-0 flex-1">
          <!-- Empty state -->
          <div v-if="!selectedNode" class="flex-1 flex items-center justify-center">
            <div class="text-center text-(--color-text-muted)">
              <svg class="mx-auto mb-3 opacity-30" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
              </svg>
              <p class="text-sm">從左側選擇一個欄位進行編輯</p>
            </div>
          </div>

          <!-- Editor card -->
          <div v-else class="flex flex-col gap-5 overflow-auto flex-1 min-h-0">
            <!-- Header -->
            <div>
              <div class="flex items-start justify-between gap-3">
                <div>
                  <h3 class="text-xl font-bold text-(--color-text-primary)">{{ selectedNode.label }}</h3>
                  <p class="text-xs font-mono text-(--color-text-muted) mt-1">{{ selectedNode.path }}</p>
                </div>
                <!-- Delete button for strategy items -->
                <button
                  v-if="selectedNode.depth >= 1 && selectedNode.path.match(/^strategies\.\d+$/) && selectedNode.isObject"
                  class="text-(--color-danger) hover:bg-(--color-danger-subtle) p-2 rounded-lg transition-colors shrink-0"
                  title="刪除策略"
                  @click="removeStrategyItem(Number(selectedNode.key)); selectedPath = ''"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14" />
                  </svg>
                </button>
              </div>
              <p v-if="selectedNode.desc" class="text-sm text-(--color-text-secondary) mt-2">{{ selectedNode.desc }}</p>
            </div>

            <hr class="border-(--color-border)" />

            <!-- ─── Control by type ─── -->

            <!-- Boolean toggle -->
            <div v-if="selectedNode.fieldType === 'boolean'" class="flex items-center gap-4">
              <button
                class="relative w-14 h-7 rounded-full transition-colors duration-200 ease-in-out"
                :class="selectedNode.value ? 'bg-(--color-success)' : 'bg-(--color-border)'"
                @click="updateValue(selectedNode.path, !selectedNode.value)"
              >
                <span
                  class="absolute left-0 top-1 w-5 h-5 rounded-full bg-white shadow-md transition-transform duration-200 ease-in-out"
                  :class="selectedNode.value ? 'translate-x-8' : 'translate-x-1'"
                />
              </button>
              <span class="text-sm font-medium" :class="selectedNode.value ? 'text-(--color-success)' : 'text-(--color-text-muted)'">
                {{ selectedNode.value ? '啟用' : '停用' }}
              </span>
            </div>

            <!-- Select dropdown -->
            <div v-else-if="selectedNode.fieldType === 'select' && selectedNode.schema?.options">
              <select
                :value="String(selectedNode.value)"
                class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                @change="updateValue(selectedNode.path, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="opt in selectedNode.schema.options" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </div>

            <!-- Percent input -->
            <div v-else-if="selectedNode.fieldType === 'percent'" class="flex items-center gap-2">
              <input
                type="number"
                :value="displayPercentValue(selectedNode.value)"
                :step="(selectedNode.schema?.step ?? 0.01) * 100"
                class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                @change="handleNumberInput(selectedNode.path, ($event.target as HTMLInputElement).value, true)"
              />
              <span class="text-lg text-(--color-text-muted) font-bold shrink-0">%</span>
            </div>

            <!-- Integer input -->
            <div v-else-if="selectedNode.fieldType === 'integer'">
              <input
                type="number"
                :value="selectedNode.value"
                step="1"
                class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                @change="handleIntegerInput(selectedNode.path, ($event.target as HTMLInputElement).value)"
              />
            </div>

            <!-- Number input -->
            <div v-else-if="selectedNode.fieldType === 'number'">
              <input
                type="number"
                :value="selectedNode.value"
                :step="selectedNode.schema?.step ?? 0.01"
                class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                @change="handleNumberInput(selectedNode.path, ($event.target as HTMLInputElement).value, false)"
              />
            </div>

            <!-- String input -->
            <div v-else-if="selectedNode.fieldType === 'string'">
              <input
                type="text"
                :value="String(selectedNode.value ?? '')"
                class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                @change="updateValue(selectedNode.path, ($event.target as HTMLInputElement).value)"
              />
            </div>

            <!-- Tags -->
            <div v-else-if="selectedNode.fieldType === 'tags'" class="space-y-3">
              <div class="flex flex-wrap gap-2">
                <span
                  v-for="(tag, i) in (selectedNode.value as string[])"
                  :key="tag"
                  class="inline-flex items-center gap-1.5 bg-(--color-accent-subtle) text-(--color-accent) px-3 py-1.5 rounded-full text-sm font-medium"
                >
                  {{ tag }}
                  <button
                    class="hover:text-(--color-danger) transition-colors"
                    @click="removeTag(selectedNode.path, i)"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                    </svg>
                  </button>
                </span>
              </div>
              <div class="flex gap-2">
                <input
                  type="text"
                  :value="tagInput[selectedNode.path] ?? ''"
                  placeholder="輸入後 Enter 新增..."
                  class="flex-1 bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                  @input="tagInput[selectedNode.path] = ($event.target as HTMLInputElement).value"
                  @keydown.enter.prevent="addTag(selectedNode.path)"
                />
                <button
                  class="px-4 py-2.5 bg-(--color-accent) text-white rounded-lg text-sm font-medium hover:opacity-90 shrink-0"
                  @click="addTag(selectedNode.path)"
                >
                  新增
                </button>
              </div>
            </div>

            <!-- Object/Array — show children as sub-fields -->
            <div v-else-if="selectedNode.isObject || selectedNode.isArray" class="space-y-3">
              <p class="text-sm text-(--color-text-secondary)">
                此為{{ selectedNode.isArray ? '陣列' : '物件' }}，包含 {{ selectedNode.childCount }} 個項目。
                請在左側展開後點選子欄位進行編輯。
              </p>
              <!-- Strategy item: list sub-fields inline -->
              <div
                v-if="selectedNode.path.match(/^strategies\.\d+$/)"
                class="space-y-4 mt-2"
              >
                <div
                  v-for="(sv, sk) in (selectedNode.value as Record<string, unknown>)"
                  :key="sk"
                  class="space-y-1"
                >
                  <label class="text-sm font-medium text-(--color-text-secondary)">{{ sk }}</label>
                  <input
                    v-if="typeof sv === 'string' || typeof sv === 'number'"
                    :type="typeof sv === 'number' ? 'number' : 'text'"
                    :value="sv"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-4 py-2.5 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                    @change="updateValue(`${selectedNode.path}.${sk}`, typeof sv === 'number' ? Number(($event.target as HTMLInputElement).value) : ($event.target as HTMLInputElement).value)"
                  />
                  <div v-else-if="typeof sv === 'boolean'" class="flex items-center gap-3">
                    <button
                      class="relative w-12 h-6 rounded-full transition-colors duration-200 ease-in-out"
                      :class="sv ? 'bg-(--color-success)' : 'bg-(--color-border)'"
                      @click="updateValue(`${selectedNode.path}.${sk}`, !sv)"
                    >
                      <span
                        class="absolute left-0 top-0.5 w-5 h-5 rounded-full bg-white shadow-md transition-transform duration-200 ease-in-out"
                        :class="sv ? 'translate-x-6' : 'translate-x-0.5'"
                      />
                    </button>
                    <span class="text-sm">{{ sv ? 'ON' : 'OFF' }}</span>
                  </div>
                  <p v-else class="text-xs text-(--color-text-muted) font-mono">{{ JSON.stringify(sv) }}</p>
                </div>
              </div>
            </div>

            <!-- ─── Schema Editor (collapsible) ─── -->
            <hr class="border-(--color-border)" />
            <div>
              <button
                class="flex items-center gap-2 text-xs font-semibold text-(--color-text-muted) uppercase tracking-wider hover:text-(--color-text-secondary) transition-colors"
                @click="schemaEditOpen = !schemaEditOpen"
              >
                <svg
                  width="10" height="10" viewBox="0 0 12 12" fill="currentColor"
                  class="transition-transform" :class="{ 'rotate-90': schemaEditOpen }"
                >
                  <path d="M4 2l4 4-4 4z" />
                </svg>
                Schema 設定
              </button>

              <div v-if="schemaEditOpen" class="mt-3 space-y-3">
                <!-- Label -->
                <div class="space-y-1">
                  <label class="text-xs font-medium text-(--color-text-muted)">標籤 (label)</label>
                  <input
                    v-model="schemaEditForm.label"
                    type="text"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-2 text-sm text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                  />
                </div>

                <!-- Description -->
                <div class="space-y-1">
                  <label class="text-xs font-medium text-(--color-text-muted)">說明 (description)</label>
                  <textarea
                    v-model="schemaEditForm.description"
                    rows="2"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-2 text-sm text-(--color-text-primary) focus:outline-none focus:border-(--color-accent) resize-y"
                  />
                </div>

                <!-- Field Type -->
                <div class="space-y-1">
                  <label class="text-xs font-medium text-(--color-text-muted)">欄位型別 (field_type)</label>
                  <select
                    v-model="schemaEditForm.field_type"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-2 text-sm text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                  >
                    <option v-for="ft in FIELD_TYPE_OPTIONS" :key="ft" :value="ft">
                      {{ ft || '(群組 / 無)' }}
                    </option>
                  </select>
                </div>

                <!-- Options (only for select) -->
                <div v-if="schemaEditForm.field_type === 'select'" class="space-y-1">
                  <label class="text-xs font-medium text-(--color-text-muted)">選項 (逗號分隔)</label>
                  <input
                    v-model="schemaEditForm.options"
                    type="text"
                    placeholder="paper, live"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-2 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                  />
                </div>

                <!-- Step (only for number/percent) -->
                <div v-if="schemaEditForm.field_type === 'number' || schemaEditForm.field_type === 'percent'" class="space-y-1">
                  <label class="text-xs font-medium text-(--color-text-muted)">步進 (step)</label>
                  <input
                    v-model.number="schemaEditForm.step"
                    type="number"
                    step="any"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-2 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                  />
                </div>

                <!-- Sort Order -->
                <div class="space-y-1">
                  <label class="text-xs font-medium text-(--color-text-muted)">排序 (sort_order)</label>
                  <input
                    v-model.number="schemaEditForm.sort_order"
                    type="number"
                    step="1"
                    class="w-full bg-(--color-bg-secondary) border border-(--color-border) rounded-lg px-3 py-2 text-sm font-mono text-(--color-text-primary) focus:outline-none focus:border-(--color-accent)"
                  />
                </div>

                <!-- Save button -->
                <button
                  @click="saveSchemaEntry"
                  :disabled="schemaEditSaving || !schemaEditForm.label.trim()"
                  class="px-4 py-2 bg-(--color-accent) text-white rounded-lg text-sm font-medium hover:opacity-90 disabled:opacity-50"
                >
                  {{ schemaEditSaving ? '儲存中...' : '儲存 Schema' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
