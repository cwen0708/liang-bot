<script setup lang="ts">
import { ref, computed, watch, onUnmounted } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import { useRealtimeTable } from '@/composables/useRealtime'
import DecisionDrawer from '@/components/DecisionDrawer.vue'
import type { Order, LoanHealth, LLMDecision } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()
const { rows: recentOrders } = useRealtimeTable<Order>('orders', { limit: 5 })

const drawerDecision = ref<LLMDecision | null>(null)

// 非 HOLD 決策：直接查 DB 排除 HOLD
const actionDecisions = ref<LLMDecision[]>([])

async function fetchActionDecisions() {
  const { data } = await supabase
    .from('llm_decisions')
    .select('*')
    .neq('action', 'HOLD')
    .order('created_at', { ascending: false })
    .limit(5)
  if (data) actionDecisions.value = data as LLMDecision[]
}
fetchActionDecisions()

// Realtime: 有新的非 HOLD 決策時更新
const decisionChannel = supabase
  .channel('rt:llm_decisions:dashboard')
  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'llm_decisions' }, (payload) => {
    const d = payload.new as LLMDecision
    if (d.action === 'HOLD') return
    actionDecisions.value = [d, ...actionDecisions.value].slice(0, 5)
  })
  .subscribe()
onUnmounted(() => { supabase.removeChannel(decisionChannel) })

// 持倉 helpers
const filterMode = computed(() => bot.globalMode)

const spotPositions = computed(() =>
  bot.spotPositions.filter(p => (p.mode ?? 'live') === filterMode.value)
)

const futuresPositions = computed(() =>
  bot.futuresPositions.filter(p => (p.mode ?? 'live') === filterMode.value)
)

function livePrice(symbol: string, fallback: number): number {
  return bot.latestPrices[symbol] ?? fallback
}

function calcPnl(pos: { symbol: string; entry_price: number; quantity: number; current_price: number; side?: string; leverage?: number }) {
  const price = livePrice(pos.symbol, pos.current_price)
  const direction = pos.side === 'short' ? -1 : 1
  const pnl = (price - pos.entry_price) * pos.quantity * direction
  const pnlPct = pos.entry_price > 0 ? ((price - pos.entry_price) / pos.entry_price) * 100 * direction * (pos.leverage ?? 1) : 0
  return { pnl, pnlPct }
}

function pnlColor(val: number): string {
  return val >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'
}
const ATH_CACHE_KEY = 'ath_prices'
const ATH_TTL = 12 * 3600 * 1000 // 12h cache — 每天最多刷新 2 次

function loadATHCache(): Record<string, number> {
  try {
    const raw = localStorage.getItem(ATH_CACHE_KEY)
    if (!raw) return {}
    const { ts, data } = JSON.parse(raw)
    if (Date.now() - ts > ATH_TTL) return {}
    return data
  } catch { return {} }
}

function saveATHCache(data: Record<string, number>) {
  localStorage.setItem(ATH_CACHE_KEY, JSON.stringify({ ts: Date.now(), data }))
}

const athPrices = ref<Record<string, number>>(loadATHCache())

function stripLD(coin: string) {
  return coin.startsWith('LD') ? coin.slice(2) : coin
}

/** 只允許設定檔交易對的幣種 */
function configCoins(): Set<string> {
  const coins = new Set<string>()
  for (const pair of bot.spotPairs) {
    const base = pair.split('/')[0]
    if (base && base !== 'USDT') coins.add(base)
  }
  return coins
}

let athFetched = false
async function fetchATH(coin: string) {
  const real = stripLD(coin)
  if (athPrices.value[real]) return
  try {
    const res = await fetch(`https://api.binance.com/api/v3/klines?symbol=${real}USDT&interval=1M&limit=1000`)
    const data = await res.json()
    if (Array.isArray(data)) {
      athPrices.value[real] = Math.max(...data.map((k: any[]) => parseFloat(k[2])))
      saveATHCache(athPrices.value)
    }
  } catch { /* ignore */ }
}

/** 只對設定檔幣種抓 ATH，且整個 session 只跑一輪 */
function fetchATHForConfigPairs() {
  if (athFetched) return
  const allowed = configCoins()
  if (!allowed.size) return
  athFetched = true
  for (const coin of allowed) fetchATH(coin)
}

watch(() => bot.spotPairs, () => fetchATHForConfigPairs(), { immediate: true })
watch(() => bot.loans, () => fetchATHForConfigPairs(), { immediate: true })
watch(() => bot.balances, () => fetchATHForConfigPairs(), { immediate: true })

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function loanNetValue(l: LoanHealth) {
  const price = bot.latestPrices[l.collateral_coin + '/USDT'] ?? 0
  return l.collateral_amount * price - l.total_debt
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-2xl font-bold md:hidden">總覽</h2>

    <!-- Summary Cards -->
    <div class="grid grid-cols-2 md:grid-cols-3 gap-3 md:gap-4">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary) uppercase">總資產</div>
        <div class="text-xl md:text-2xl font-bold mt-1">
          <template v-if="bot.totalAssets !== null">
            ${{ bot.totalAssets.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
          </template>
          <span v-else class="text-(--color-text-muted)">--</span>
        </div>
        <div v-if="Object.keys(athPrices).length && bot.totalAssets !== null" class="text-sm text-(--color-text-secondary) mt-1 opacity-50">
          ATH ${{ (bot.balances.reduce((s, b) => s + (b.currency === 'USDT' ? b.free : b.free * (athPrices[stripLD(b.currency)] ?? 0)), 0) + bot.loans.reduce((s, l) => s + l.collateral_amount * (athPrices[l.collateral_coin] ?? 0), 0)).toLocaleString(undefined, { maximumFractionDigits: 0 }) }} / {{ ((bot.totalAssets / (bot.balances.reduce((s, b) => s + (b.currency === 'USDT' ? b.free : b.free * (athPrices[stripLD(b.currency)] ?? 0)), 0) + bot.loans.reduce((s, l) => s + l.collateral_amount * (athPrices[l.collateral_coin] ?? 0), 0))) * 100).toFixed(1) }}%
        </div>
        <div v-else class="text-sm text-(--color-text-secondary) mt-1">
          {{ bot.isOnline ? '運行中' : '離線' }} · 第 {{ bot.status?.cycle_num ?? '-' }} 輪
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary) uppercase">現貨</div>
        <div class="text-xl md:text-2xl font-bold mt-1">
          ${{ bot.totalUsdt.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
        </div>
        <div class="text-sm text-(--color-text-secondary) mt-1">
          {{ bot.balances.length }} 幣種
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary) uppercase">借貸</div>
        <div class="text-xl md:text-2xl font-bold mt-1" :class="{
          'text-(--color-success)': bot.netLoanValue !== null && bot.netLoanValue > 0,
          'text-(--color-danger)': bot.netLoanValue !== null && bot.netLoanValue < 0,
        }">
          <template v-if="bot.netLoanValue !== null">
            ${{ bot.netLoanValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
          </template>
          <span v-else class="text-(--color-text-muted)">--</span>
        </div>
        <div class="text-sm text-(--color-text-secondary) mt-1">
          {{ bot.loans.length }} 筆借貸
        </div>
      </div>
    </div>

    <!-- 帳戶餘額 (現貨) -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase">帳戶餘額</h3>
        <div v-if="bot.balances.length" class="text-sm text-(--color-text-secondary) tabular-nums">
          ${{ bot.totalUsdt.toLocaleString(undefined, { maximumFractionDigits: 0 }) }}
          <span v-if="Object.keys(athPrices).length" class="opacity-50">/ ATH ${{ bot.balances.reduce((s, b) => s + (b.currency === 'USDT' ? b.free : b.free * (athPrices[stripLD(b.currency)] ?? 0)), 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</span>
        </div>
      </div>
      <div v-if="!bot.balances.length" class="text-base text-(--color-text-secondary)">尚無餘額資料</div>
      <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="b in bot.balances" :key="b.currency"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="flex justify-between items-center">
            <span class="text-base font-bold">{{ stripLD(b.currency) }}</span>
            <span class="text-sm text-(--color-text-secondary)">${{ b.usdt_value.toLocaleString(undefined, { maximumFractionDigits: 2 }) }}</span>
          </div>
          <div class="text-sm text-(--color-text-secondary) mt-1 font-mono">{{ b.free.toFixed(8) }}</div>
        </div>
      </div>
    </div>

    <!-- 現貨持倉 -->
    <div v-if="spotPositions.length" class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase">現貨持倉</h3>
        <div class="text-sm text-(--color-text-secondary) tabular-nums">
          {{ spotPositions.length }} 筆
        </div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="pos in spotPositions" :key="pos.id"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="flex justify-between items-center">
            <span class="text-base font-bold">{{ pos.symbol.replace('/USDT', '') }}</span>
            <span class="text-sm font-bold" :class="pnlColor(calcPnl(pos).pnl)">
              {{ calcPnl(pos).pnl >= 0 ? '+' : '' }}{{ calcPnl(pos).pnl.toFixed(2) }}
            </span>
          </div>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 mt-2 text-sm text-(--color-text-secondary)">
            <div class="flex justify-between"><span>數量</span><span class="font-mono">{{ pos.quantity.toFixed(6) }}</span></div>
            <div class="flex justify-between"><span>損益</span><span class="font-mono" :class="pnlColor(calcPnl(pos).pnlPct)">{{ calcPnl(pos).pnlPct >= 0 ? '+' : '' }}{{ calcPnl(pos).pnlPct.toFixed(2) }}%</span></div>
            <div class="flex justify-between"><span>入場</span><span class="font-mono">${{ pos.entry_price.toFixed(2) }}</span></div>
            <div class="flex justify-between"><span>現價</span><span class="font-mono">${{ livePrice(pos.symbol, pos.current_price).toFixed(2) }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 合約持倉 -->
    <div v-if="futuresPositions.length" class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase">合約持倉</h3>
        <div class="text-sm text-(--color-text-secondary) tabular-nums">
          {{ futuresPositions.length }} 筆
        </div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="pos in futuresPositions" :key="pos.id"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="flex justify-between items-center">
            <div class="flex items-center gap-1.5">
              <span class="text-base font-bold">{{ pos.symbol.replace('/USDT', '') }}</span>
              <span class="px-1 py-0.5 rounded text-[10px] font-bold"
                    :class="pos.side === 'long' ? 'bg-(--color-success)/15 text-(--color-success)' : 'bg-(--color-danger)/15 text-(--color-danger)'">
                {{ pos.side === 'long' ? 'L' : 'S' }} {{ pos.leverage ?? 1 }}x
              </span>
            </div>
            <span class="text-sm font-bold" :class="pnlColor(calcPnl(pos).pnl)">
              {{ calcPnl(pos).pnl >= 0 ? '+' : '' }}{{ calcPnl(pos).pnl.toFixed(2) }}
            </span>
          </div>
          <div class="grid grid-cols-2 gap-x-3 gap-y-1 mt-2 text-sm text-(--color-text-secondary)">
            <div class="flex justify-between"><span>數量</span><span class="font-mono">{{ pos.quantity.toFixed(4) }}</span></div>
            <div class="flex justify-between"><span>損益</span><span class="font-mono" :class="pnlColor(calcPnl(pos).pnlPct)">{{ calcPnl(pos).pnlPct >= 0 ? '+' : '' }}{{ calcPnl(pos).pnlPct.toFixed(2) }}%</span></div>
            <div class="flex justify-between"><span>入場</span><span class="font-mono">${{ pos.entry_price.toFixed(2) }}</span></div>
            <div class="flex justify-between"><span>現價</span><span class="font-mono">${{ livePrice(pos.symbol, pos.current_price).toFixed(2) }}</span></div>
          </div>
        </div>
      </div>
    </div>

    <!-- 借貸餘額 -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase">借貸餘額</h3>
        <div v-if="bot.loans.length" class="text-sm text-(--color-text-secondary) tabular-nums">
          估值 ${{ bot.loans.reduce((s, l) => s + l.collateral_amount * (bot.latestPrices[l.collateral_coin + '/USDT'] ?? 0), 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) }}
          / 淨值 ${{ bot.loans.reduce((s, l) => s + l.collateral_amount * (bot.latestPrices[l.collateral_coin + '/USDT'] ?? 0) - l.total_debt, 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) }}
          / 負債 ${{ bot.loans.reduce((s, l) => s + l.total_debt, 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) }}
          <span v-if="Object.keys(athPrices).length" class="opacity-50">/ ATH ${{ bot.loans.reduce((s, l) => s + l.collateral_amount * (athPrices[l.collateral_coin] ?? 0), 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</span>
        </div>
      </div>
      <div v-if="!bot.loans.length" class="text-base text-(--color-text-secondary)">尚無借貸資料</div>
      <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="l in bot.loans" :key="`${l.collateral_coin}/${l.loan_coin}`"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="flex justify-between items-center">
            <span class="text-base font-bold">{{ l.collateral_coin }}/{{ l.loan_coin }}</span>
            <span class="text-sm font-medium" :class="{
              'text-(--color-danger)': l.ltv >= 0.75,
              'text-(--color-warning)': l.ltv >= 0.70 && l.ltv < 0.75,
              'text-(--color-success)': l.ltv < 0.4,
              'text-(--color-text-secondary)': l.ltv >= 0.4 && l.ltv < 0.70,
            }">{{ (l.ltv * 100).toFixed(1) }}%</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-x-3 gap-y-1 mt-2 text-sm text-(--color-text-secondary)">
            <div class="flex justify-between"><span>質押</span><span class="font-mono">{{ l.collateral_amount.toFixed(4) }}</span></div>
            <div class="flex justify-between"><span>負債</span><span class="font-mono">{{ l.total_debt.toFixed(2) }}</span></div>
            <div class="flex justify-between"><span>淨值</span><span class="font-mono">${{ loanNetValue(l).toFixed(2) }}</span></div>
            <div class="flex justify-between opacity-50"><span>估值</span><span class="font-mono">${{ (l.collateral_amount * (bot.latestPrices[l.collateral_coin + '/USDT'] ?? 0)).toLocaleString(undefined, { maximumFractionDigits: 0 }) }}</span></div>
          </div>
          <div v-if="l.action_taken !== 'none'" class="text-sm font-medium mt-1 text-right"
               :class="l.action_taken === 'protect' ? 'text-(--color-warning)' : 'text-(--color-success)'">
            {{ l.action_taken === 'protect' ? '保護' : '獲利了結' }}
          </div>
        </div>
      </div>
    </div>

    <!-- Orders & AI -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">最近訂單</h3>
        <div v-if="!recentOrders.length" class="text-base text-(--color-text-secondary)">尚無訂單</div>
        <div v-for="o in recentOrders" :key="o.id" class="flex justify-between items-center py-2 border-b border-(--color-border) last:border-0 text-base">
          <div>
            <span :class="o.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'" class="font-medium uppercase">
              {{ o.side === 'buy' ? '買入' : '賣出' }}
            </span>
            <span class="ml-2 text-(--color-text-secondary)">{{ o.symbol }}</span>
          </div>
          <div class="text-right">
            <div>{{ o.quantity.toFixed(6) }} @ ${{ o.price?.toFixed(2) }}</div>
            <div class="text-sm text-(--color-text-secondary)">{{ formatTime(o.created_at) }}</div>
          </div>
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">AI 決策</h3>
        <div v-if="!actionDecisions.length" class="text-base text-(--color-text-secondary)">尚無決策</div>
        <div v-for="d in actionDecisions" :key="d.id"
             class="py-2 border-b border-(--color-border) last:border-0 text-base cursor-pointer hover:bg-(--color-bg-secondary)/50 transition-colors rounded px-1 -mx-1"
             @click="drawerDecision = d">
          <div class="flex justify-between items-center">
            <div class="flex items-center gap-1.5">
              <span :class="{
                'text-(--color-success)': d.action === 'BUY' || d.action === 'COVER',
                'text-(--color-danger)': d.action === 'SELL' || d.action === 'SHORT',
              }" class="font-medium">
                {{ ({ BUY: '買入', SELL: '賣出', SHORT: '做空', COVER: '平空' } as Record<string, string>)[d.action] ?? d.action }}
              </span>
              <span class="px-1 py-0.5 rounded text-[10px] font-medium bg-(--color-bg-secondary) text-(--color-text-muted)">{{ (d.market_type ?? 'spot') === 'futures' ? '合約' : '現貨' }}</span>
            </div>
            <span class="text-(--color-text-secondary) text-sm">{{ d.symbol }} &middot; {{ (d.confidence * 100).toFixed(0) }}%</span>
          </div>
          <div class="text-sm text-(--color-text-secondary) mt-1 line-clamp-2">{{ d.reasoning }}</div>
        </div>
      </div>
    </div>

    <DecisionDrawer :decision="drawerDecision" @close="drawerDecision = null" />
  </div>
</template>
