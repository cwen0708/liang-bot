<script setup lang="ts">
import { ref, computed } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { Order, LLMDecision } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()
const { rows: orders, loading } = useRealtimeTable<Order>('orders', { limit: 200, filter: { column: 'market_type', value: 'spot' } })

const filterMode = computed(() => bot.globalMode)
const filterSymbol = ref('')
const filterStatus = ref('')
const viewMode = ref<'pairs' | 'flat'>('pairs')
const expandedOrderId = ref<number | null>(null)
const expandedPairId = ref<string | null>(null)
const collapsedSymbols = ref(new Set<string>())

// ─── Trade Pair types & helpers ────────────────────────────
interface TradePair {
  id: string
  symbol: string
  buyOrder: Order
  sellOrder: Order | null
  entryPrice: number
  exitPrice: number | null
  quantity: number
  pnl: number
  pnlPct: number
  holdDurationMs: number
  status: 'closed' | 'open'
}

function quantityMatch(a: number, b: number): boolean {
  if (a === b) return true
  const max = Math.max(Math.abs(a), Math.abs(b))
  return max === 0 || Math.abs(a - b) / max < 1e-6
}

function formatDuration(ms: number): string {
  if (ms <= 0) return '-'
  const totalMin = Math.floor(ms / 60000)
  if (totalMin < 60) return `${totalMin}m`
  const hours = Math.floor(totalMin / 60)
  const mins = totalMin % 60
  if (hours < 24) return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
  const days = Math.floor(hours / 24)
  const remHours = hours % 24
  return remHours > 0 ? `${days}d ${remHours}h` : `${days}d`
}

// ─── Positions filtered by mode (spot only) ────────────────
const filteredPositions = computed(() => {
  return bot.spotPositions.filter(p => (p.mode ?? 'live') === filterMode.value)
})

// ─── Orders filtered by mode + spot only ───────────────────
const spotOrders = computed(() => {
  return orders.value.filter(o =>
    (o.mode ?? 'live') === filterMode.value && (o.market_type ?? 'spot') === 'spot'
  )
})

// ─── Flat view: filtered orders ────────────────────────────
const filteredOrders = computed(() => {
  let result = [...spotOrders.value]
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  if (filterSymbol.value) {
    result = result.filter(o => o.symbol === filterSymbol.value)
  }
  if (filterStatus.value === 'filled') {
    result = result.filter(o => o.status === 'filled' || o.status === 'closed')
  } else if (filterStatus.value === 'cancelled') {
    result = result.filter(o => o.status === 'cancelled')
  }
  return result
})

// ─── Trade pairs: FIFO quantity matching ───────────────────
const tradePairs = computed<TradePair[]>(() => {
  const filled = spotOrders.value.filter(o => o.status === 'filled' || o.status === 'closed')

  const bySymbol = new Map<string, Order[]>()
  for (const o of filled) {
    const list = bySymbol.get(o.symbol) || []
    list.push(o)
    bySymbol.set(o.symbol, list)
  }

  const pairs: TradePair[] = []

  for (const [symbol, symbolOrders] of bySymbol) {
    const sorted = [...symbolOrders].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    )
    const unmatchedBuys: Order[] = []

    for (const order of sorted) {
      if (order.side === 'buy') {
        unmatchedBuys.push(order)
      } else {
        const matchIdx = unmatchedBuys.findIndex(buy => quantityMatch(buy.quantity, order.quantity))
        if (matchIdx >= 0) {
          const buyOrder = unmatchedBuys.splice(matchIdx, 1)[0]!
          const pnl = (order.price - buyOrder.price) * order.quantity
          const pnlPct = buyOrder.price > 0 ? ((order.price - buyOrder.price) / buyOrder.price) * 100 : 0
          const holdMs = new Date(order.created_at).getTime() - new Date(buyOrder.created_at).getTime()
          pairs.push({
            id: `pair-${buyOrder.id}`,
            symbol, buyOrder, sellOrder: order,
            entryPrice: buyOrder.price, exitPrice: order.price,
            quantity: order.quantity, pnl, pnlPct,
            holdDurationMs: holdMs, status: 'closed',
          })
        }
      }
    }

    // Unmatched buys = open positions
    for (const buyOrder of unmatchedBuys) {
      const currentPrice = bot.latestPrices[symbol] ?? buyOrder.price
      const pnl = (currentPrice - buyOrder.price) * buyOrder.quantity
      const pnlPct = buyOrder.price > 0 ? ((currentPrice - buyOrder.price) / buyOrder.price) * 100 : 0
      pairs.push({
        id: `pair-${buyOrder.id}`,
        symbol, buyOrder, sellOrder: null,
        entryPrice: buyOrder.price, exitPrice: null,
        quantity: buyOrder.quantity, pnl, pnlPct,
        holdDurationMs: Date.now() - new Date(buyOrder.created_at).getTime(),
        status: 'open',
      })
    }
  }

  return pairs.sort((a, b) => {
    if (a.status !== b.status) return a.status === 'open' ? -1 : 1
    const aTime = a.sellOrder?.created_at ?? a.buyOrder.created_at
    const bTime = b.sellOrder?.created_at ?? b.buyOrder.created_at
    return new Date(bTime).getTime() - new Date(aTime).getTime()
  })
})

const filteredPairs = computed(() => {
  if (!filterSymbol.value) return tradePairs.value
  return tradePairs.value.filter(p => p.symbol === filterSymbol.value)
})

const pairStats = computed(() => {
  const closed = filteredPairs.value.filter(p => p.status === 'closed')
  const open = filteredPairs.value.filter(p => p.status === 'open')
  const totalPnl = closed.reduce((sum, p) => sum + p.pnl, 0)
  const wins = closed.filter(p => p.pnl > 0).length
  const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0
  return { closedCount: closed.length, openCount: open.length, totalPnl, wins, losses: closed.length - wins, winRate }
})

// ─── Symbol groups: group pairs by symbol ───────────────────
interface SymbolGroup {
  symbol: string
  pairs: TradePair[]
  totalPnl: number
  closedCount: number
  openCount: number
  winCount: number
}

const symbolGroups = computed<SymbolGroup[]>(() => {
  const map = new Map<string, TradePair[]>()
  for (const p of filteredPairs.value) {
    const list = map.get(p.symbol) || []
    list.push(p)
    map.set(p.symbol, list)
  }

  const groups: SymbolGroup[] = []
  for (const [symbol, pairs] of map) {
    // Sort within group: open first, then newest first
    pairs.sort((a, b) => {
      if (a.status !== b.status) return a.status === 'open' ? -1 : 1
      const aTime = a.sellOrder?.created_at ?? a.buyOrder.created_at
      const bTime = b.sellOrder?.created_at ?? b.buyOrder.created_at
      return new Date(bTime).getTime() - new Date(aTime).getTime()
    })
    const closed = pairs.filter(p => p.status === 'closed')
    groups.push({
      symbol,
      pairs,
      totalPnl: pairs.reduce((sum, p) => sum + p.pnl, 0),
      closedCount: closed.length,
      openCount: pairs.length - closed.length,
      winCount: closed.filter(p => p.pnl > 0).length,
    })
  }

  // Sort groups: has open positions first, then by most recent activity
  return groups.sort((a, b) => {
    if (a.openCount !== b.openCount) return a.openCount > 0 ? -1 : b.openCount > 0 ? 1 : 0
    const aLatest = a.pairs[0]?.sellOrder?.created_at ?? a.pairs[0]?.buyOrder.created_at ?? ''
    const bLatest = b.pairs[0]?.sellOrder?.created_at ?? b.pairs[0]?.buyOrder.created_at ?? ''
    return new Date(bLatest).getTime() - new Date(aLatest).getTime()
  })
})

function toggleSymbolGroup(symbol: string) {
  const s = new Set(collapsedSymbols.value)
  if (s.has(symbol)) s.delete(symbol)
  else s.add(symbol)
  collapsedSymbols.value = s
}

// ─── AI Decision: on-demand fetch ──────────────────────────
const decisionCache = ref(new Map<string, LLMDecision | null>())

async function fetchDecision(cycleId: string, symbol: string): Promise<LLMDecision | null> {
  const key = `${cycleId}:${symbol}`
  if (decisionCache.value.has(key)) return decisionCache.value.get(key)!
  const { data } = await supabase
    .from('llm_decisions')
    .select('*')
    .eq('cycle_id', cycleId)
    .eq('symbol', symbol)
    .limit(1)
    .single()
  const decision = (data as LLMDecision) ?? null
  decisionCache.value.set(key, decision)
  return decision
}

// Pair decisions (lazy loaded on expand)
const pairDecisions = ref(new Map<string, { entry: LLMDecision | null; exit: LLMDecision | null }>())

async function loadPairDecisions(pair: TradePair) {
  if (pairDecisions.value.has(pair.id)) return
  const [entry, exit] = await Promise.all([
    pair.buyOrder.cycle_id ? fetchDecision(pair.buyOrder.cycle_id, pair.symbol) : Promise.resolve(null),
    pair.sellOrder?.cycle_id ? fetchDecision(pair.sellOrder.cycle_id, pair.symbol) : Promise.resolve(null),
  ])
  pairDecisions.value.set(pair.id, { entry, exit })
}

// Flat view decisions (lazy loaded on expand)
const orderDecisions = ref(new Map<number, LLMDecision | null>())

async function loadOrderDecision(order: Order) {
  if (orderDecisions.value.has(order.id)) return
  if (!order.cycle_id) {
    orderDecisions.value.set(order.id, null)
    return
  }
  const decision = await fetchDecision(order.cycle_id, order.symbol)
  orderDecisions.value.set(order.id, decision)
}

// ─── UI helpers ────────────────────────────────────────────
function toggleExpand(orderId: number, order: Order) {
  if (expandedOrderId.value === orderId) {
    expandedOrderId.value = null
  } else {
    expandedOrderId.value = orderId
    loadOrderDecision(order)
  }
}

function togglePairExpand(pair: TradePair) {
  if (expandedPairId.value === pair.id) {
    expandedPairId.value = null
  } else {
    expandedPairId.value = pair.id
    loadPairDecisions(pair)
  }
}

function selectPosition(symbol: string) {
  filterSymbol.value = filterSymbol.value === symbol ? '' : symbol
}

function statusLabel(status: string): string {
  if (status === 'filled' || status === 'closed') return '已成交'
  if (status === 'partial') return '部分成交'
  if (status === 'cancelled') return '已取消'
  return status
}

function actionLabel(action: string): string {
  if (action === 'BUY') return '買入'
  if (action === 'SELL') return '賣出'
  return '觀望'
}

function formatDate(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', { hour12: false })
}

function formatDateShort(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function livePrice(symbol: string, fallback: number): number {
  return bot.latestPrices[symbol] ?? fallback
}

function calcPnl(pos: { symbol: string; entry_price: number; quantity: number; current_price: number; side?: string }) {
  const price = livePrice(pos.symbol, pos.current_price)
  const direction = pos.side === 'short' ? -1 : 1
  const pnl = (price - pos.entry_price) * pos.quantity * direction
  const pnlPct = pos.entry_price > 0 ? ((price - pos.entry_price) / pos.entry_price) * 100 * direction : 0
  return { pnl, pnlPct }
}

function pnlColor(val: number): string {
  return val >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'
}

function pnlSign(val: number): string {
  return val >= 0 ? '+' : ''
}
</script>

<template>
  <div class="p-4 md:p-6 flex flex-col gap-4 md:gap-6 md:h-[calc(100vh)] md:overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between gap-2 shrink-0 flex-wrap">
      <h2 class="text-2xl md:text-3xl font-bold">現貨</h2>
      <div class="flex items-center gap-2">
        <!-- View mode toggle -->
        <div class="inline-flex rounded-lg bg-(--color-bg-secondary) p-0.5">
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="viewMode === 'pairs'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="viewMode = 'pairs'"
          >交易配對</button>
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="viewMode === 'flat'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="viewMode = 'flat'"
          >原始訂單</button>
        </div>
        <!-- Status filter (flat view only) -->
        <div v-if="viewMode === 'flat'" class="inline-flex rounded-lg bg-(--color-bg-secondary) p-0.5">
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="!filterStatus
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="filterStatus = ''"
          >全部</button>
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="filterStatus === 'filled'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="filterStatus = 'filled'"
          >已成交</button>
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="filterStatus === 'cancelled'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="filterStatus = 'cancelled'"
          >已取消</button>
        </div>
      </div>
    </div>

    <!-- Scrollable content -->
    <div class="flex flex-col gap-4 md:gap-5 min-h-0 md:flex-1 md:overflow-auto">
      <!-- ===== Positions: horizontal scroll ===== -->
      <section class="shrink-0">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-lg font-semibold text-(--color-text-primary)">持倉</h3>
          <button
            v-if="filterSymbol"
            class="text-xs text-(--color-accent) hover:underline"
            @click="filterSymbol = ''"
          >清除篩選</button>
        </div>
        <div v-if="!filteredPositions.length" class="text-sm text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">
          目前無持倉
        </div>
        <div v-else class="overflow-x-auto">
          <div class="inline-grid grid-cols-4 gap-2" style="min-width: max-content">
            <div
              v-for="pos in filteredPositions"
              :key="pos.id"
              class="w-[220px] bg-(--color-bg-card) border rounded-lg p-3 cursor-pointer transition-all"
              :class="filterSymbol === pos.symbol
                ? 'border-(--color-accent) ring-1 ring-(--color-accent)/30'
                : 'border-(--color-border) hover:border-(--color-accent)/50'"
              @click="selectPosition(pos.symbol)"
            >
              <div class="flex justify-between items-center mb-2">
                <span class="font-bold text-sm">{{ pos.symbol.replace('/USDT', '') }}</span>
                <div class="text-right">
                  <span
                    class="font-bold text-sm block"
                    :class="calcPnl(pos).pnl >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'"
                  >{{ calcPnl(pos).pnl >= 0 ? '+' : '' }}{{ calcPnl(pos).pnl.toFixed(2) }}</span>
                  <span
                    class="text-xs"
                    :class="calcPnl(pos).pnlPct >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'"
                  >{{ calcPnl(pos).pnlPct >= 0 ? '+' : '' }}{{ calcPnl(pos).pnlPct.toFixed(2) }}%</span>
                </div>
              </div>
              <div class="text-xs text-(--color-text-secondary) space-y-0.5">
                <div class="flex justify-between">
                  <span>數量</span>
                  <span class="text-(--color-text-primary) font-medium">{{ pos.quantity.toFixed(6) }}</span>
                </div>
                <div class="flex justify-between">
                  <span>入場</span>
                  <span class="text-(--color-text-primary) font-medium">${{ pos.entry_price.toFixed(2) }}</span>
                </div>
                <div class="flex justify-between">
                  <span>現價</span>
                  <span class="text-(--color-text-primary) font-medium">${{ livePrice(pos.symbol, pos.current_price).toFixed(2) }}</span>
                </div>
                <div v-if="pos.stop_loss" class="flex justify-between">
                  <span>止損</span>
                  <span class="text-(--color-danger) font-medium">${{ pos.stop_loss.toFixed(2) }}</span>
                </div>
                <div v-if="pos.take_profit" class="flex justify-between">
                  <span>止盈</span>
                  <span class="text-(--color-success) font-medium">${{ pos.take_profit.toFixed(2) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== PAIRS VIEW (grouped by symbol) ===== -->
      <section v-if="viewMode === 'pairs'" class="flex flex-col min-h-0">
        <div class="flex items-center justify-between mb-2 shrink-0">
          <h3 class="text-lg font-semibold text-(--color-text-primary)">
            交易紀錄
            <span v-if="filterSymbol" class="text-sm font-normal text-(--color-accent) ml-2">{{ filterSymbol }}</span>
          </h3>
        </div>

        <!-- Stats bar -->
        <div v-if="filteredPairs.length" class="flex items-center gap-4 text-sm mb-3 flex-wrap shrink-0">
          <span class="text-(--color-text-secondary)">已結 <span class="font-medium text-(--color-text-primary)">{{ pairStats.closedCount }}</span> 筆</span>
          <span v-if="pairStats.closedCount" class="text-(--color-text-secondary)">勝率 <span class="font-medium text-(--color-text-primary)">{{ pairStats.winRate.toFixed(0) }}%</span></span>
          <span v-if="pairStats.closedCount" :class="pnlColor(pairStats.totalPnl)">
            總損益 <span class="font-bold">{{ pnlSign(pairStats.totalPnl) }}{{ pairStats.totalPnl.toFixed(2) }} USDT</span>
          </span>
          <span v-if="pairStats.openCount" class="text-(--color-accent)">持倉中 {{ pairStats.openCount }}</span>
        </div>

        <div class="flex flex-col gap-3">
          <div v-if="loading" class="text-base text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">載入中...</div>
          <div v-else-if="!symbolGroups.length" class="text-base text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">無交易紀錄</div>

          <!-- Symbol groups -->
          <div
            v-for="group in symbolGroups"
            :key="group.symbol"
            class="bg-(--color-bg-card) border border-(--color-border) rounded-xl shadow-sm dark:shadow-none overflow-hidden"
          >
            <!-- Group header (clickable) -->
            <div
              class="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-(--color-bg-secondary)/50 transition-colors"
              @click="toggleSymbolGroup(group.symbol)"
            >
              <div class="flex items-center gap-3">
                <svg
                  xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
                  fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                  class="text-(--color-text-muted) transition-transform"
                  :class="collapsedSymbols.has(group.symbol) ? '' : 'rotate-90'"
                ><polyline points="9 18 15 12 9 6"/></svg>
                <span class="font-bold text-base text-(--color-text-primary)">{{ group.symbol }}</span>
                <span class="text-sm text-(--color-text-secondary)">{{ group.pairs.length }} 筆交易</span>
                <span v-if="group.openCount" class="px-1.5 py-0.5 rounded text-[11px] font-medium bg-(--color-accent)/20 text-(--color-accent)">{{ group.openCount }} 持倉中</span>
              </div>
              <span class="font-bold text-sm" :class="pnlColor(group.totalPnl)">{{ pnlSign(group.totalPnl) }}{{ group.totalPnl.toFixed(2) }} USDT</span>
            </div>

            <!-- Group content (collapsible) -->
            <div v-if="!collapsedSymbols.has(group.symbol)" class="border-t border-(--color-border)">
              <!-- Desktop table -->
              <table class="w-full text-base hidden md:table">
                <thead>
                  <tr class="text-(--color-text-secondary) text-left text-xs">
                    <th class="px-4 py-1.5">#</th>
                    <th class="py-1.5">狀態</th>
                    <th class="py-1.5">入場</th>
                    <th class="py-1.5">出場</th>
                    <th class="py-1.5">數量</th>
                    <th class="py-1.5">損益</th>
                    <th class="py-1.5">持倉時間</th>
                  </tr>
                </thead>
                <tbody>
                  <template v-for="(pair, idx) in group.pairs" :key="pair.id">
                    <tr
                      class="border-t border-(--color-border)/50 cursor-pointer transition-colors"
                      :class="expandedPairId === pair.id ? 'bg-(--color-bg-secondary)/70' : 'hover:bg-(--color-bg-secondary)/50'"
                      @click="togglePairExpand(pair)"
                    >
                      <td class="px-4 py-2 text-sm text-(--color-text-muted)">{{ group.pairs.length - idx }}</td>
                      <td class="py-2">
                        <span
                          class="px-2 py-0.5 rounded text-xs"
                          :class="pair.status === 'closed'
                            ? 'bg-(--color-success)/20 text-(--color-success)'
                            : 'bg-(--color-accent)/20 text-(--color-accent)'"
                        >{{ pair.status === 'closed' ? '已結' : '持倉中' }}</span>
                      </td>
                      <td class="py-2">
                        <div class="text-sm">${{ pair.entryPrice.toFixed(2) }}</div>
                        <div class="text-xs text-(--color-text-muted)">{{ formatDateShort(pair.buyOrder.created_at) }}</div>
                      </td>
                      <td class="py-2">
                        <div v-if="pair.sellOrder" class="text-sm">${{ pair.exitPrice!.toFixed(2) }}</div>
                        <div v-else class="text-sm text-(--color-text-muted)">${{ livePrice(pair.symbol, pair.entryPrice).toFixed(2) }}</div>
                        <div class="text-xs text-(--color-text-muted)">{{ pair.sellOrder ? formatDateShort(pair.sellOrder.created_at) : '至今' }}</div>
                      </td>
                      <td class="py-2 text-sm">{{ pair.quantity.toFixed(6) }}</td>
                      <td class="py-2">
                        <div class="font-bold text-sm" :class="pnlColor(pair.pnl)">{{ pnlSign(pair.pnl) }}{{ pair.pnl.toFixed(2) }}</div>
                        <div class="text-xs" :class="pnlColor(pair.pnlPct)">{{ pnlSign(pair.pnlPct) }}{{ pair.pnlPct.toFixed(2) }}%</div>
                      </td>
                      <td class="py-2 text-(--color-text-secondary) text-sm">{{ formatDuration(pair.holdDurationMs) }}</td>
                    </tr>
                    <!-- Expanded: AI decisions -->
                    <tr v-if="expandedPairId === pair.id">
                      <td colspan="7" class="pb-3 pt-0 px-4">
                        <div v-if="!pairDecisions.has(pair.id)" class="text-sm text-(--color-text-secondary)">載入 AI 決策...</div>
                        <div v-else class="grid md:grid-cols-2 gap-3">
                          <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm">
                            <div class="flex items-center gap-2 mb-1.5">
                              <span class="text-xs font-bold text-(--color-success)">開倉決策</span>
                              <span v-if="pairDecisions.get(pair.id)?.entry" class="text-xs text-(--color-text-secondary)">
                                信心 {{ (pairDecisions.get(pair.id)!.entry!.confidence * 100).toFixed(0) }}%
                              </span>
                            </div>
                            <p v-if="pairDecisions.get(pair.id)?.entry" class="text-(--color-text-primary) leading-relaxed whitespace-pre-wrap text-sm">{{ pairDecisions.get(pair.id)!.entry!.reasoning }}</p>
                            <p v-else class="text-(--color-text-secondary)">無 AI 決策記錄</p>
                          </div>
                          <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm">
                            <div class="flex items-center gap-2 mb-1.5">
                              <span class="text-xs font-bold text-(--color-danger)">平倉決策</span>
                              <span v-if="pairDecisions.get(pair.id)?.exit" class="text-xs text-(--color-text-secondary)">
                                信心 {{ (pairDecisions.get(pair.id)!.exit!.confidence * 100).toFixed(0) }}%
                              </span>
                            </div>
                            <p v-if="pairDecisions.get(pair.id)?.exit" class="text-(--color-text-primary) leading-relaxed whitespace-pre-wrap text-sm">{{ pairDecisions.get(pair.id)!.exit!.reasoning }}</p>
                            <p v-else class="text-(--color-text-secondary)">{{ pair.status === 'open' ? '尚未平倉' : '無 AI 決策記錄' }}</p>
                          </div>
                        </div>
                      </td>
                    </tr>
                  </template>
                </tbody>
              </table>

              <!-- Mobile cards -->
              <div class="md:hidden p-3 space-y-2">
                <div
                  v-for="(pair, idx) in group.pairs"
                  :key="pair.id"
                  class="bg-(--color-bg-secondary) rounded-lg p-3"
                  @click="togglePairExpand(pair)"
                >
                  <div class="flex justify-between items-center mb-2">
                    <div class="flex items-center gap-2">
                      <span class="text-xs text-(--color-text-muted)">#{{ group.pairs.length - idx }}</span>
                      <span
                        class="px-1.5 py-0.5 rounded text-[11px] font-medium"
                        :class="pair.status === 'closed'
                          ? 'bg-(--color-success)/20 text-(--color-success)'
                          : 'bg-(--color-accent)/20 text-(--color-accent)'"
                      >{{ pair.status === 'closed' ? '已結' : '持倉中' }}</span>
                    </div>
                    <div class="text-right">
                      <span class="font-bold text-sm" :class="pnlColor(pair.pnl)">{{ pnlSign(pair.pnl) }}{{ pair.pnl.toFixed(2) }}</span>
                      <span class="text-xs ml-1" :class="pnlColor(pair.pnlPct)">({{ pnlSign(pair.pnlPct) }}{{ pair.pnlPct.toFixed(2) }}%)</span>
                    </div>
                  </div>
                  <div class="grid grid-cols-3 gap-1 text-sm text-(--color-text-secondary)">
                    <div>入場 ${{ pair.entryPrice.toFixed(2) }}</div>
                    <div>{{ pair.sellOrder ? `出場 $${pair.exitPrice!.toFixed(2)}` : `現價 $${livePrice(pair.symbol, pair.entryPrice).toFixed(2)}` }}</div>
                    <div class="text-right">{{ formatDuration(pair.holdDurationMs) }}</div>
                  </div>
                  <!-- Expanded AI decisions -->
                  <div v-if="expandedPairId === pair.id" class="mt-2 pt-2 border-t border-(--color-border)/50 space-y-2">
                    <div v-if="!pairDecisions.has(pair.id)" class="text-xs text-(--color-text-secondary)">載入 AI 決策...</div>
                    <template v-else>
                      <div>
                        <div class="flex items-center gap-2 mb-1">
                          <span class="text-xs font-bold text-(--color-success)">開倉決策</span>
                          <span v-if="pairDecisions.get(pair.id)?.entry" class="text-xs text-(--color-text-secondary)">信心 {{ (pairDecisions.get(pair.id)!.entry!.confidence * 100).toFixed(0) }}%</span>
                        </div>
                        <p v-if="pairDecisions.get(pair.id)?.entry" class="text-sm text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ pairDecisions.get(pair.id)!.entry!.reasoning }}</p>
                        <p v-else class="text-xs text-(--color-text-secondary)">無 AI 決策記錄</p>
                      </div>
                      <div>
                        <div class="flex items-center gap-2 mb-1">
                          <span class="text-xs font-bold text-(--color-danger)">平倉決策</span>
                          <span v-if="pairDecisions.get(pair.id)?.exit" class="text-xs text-(--color-text-secondary)">信心 {{ (pairDecisions.get(pair.id)!.exit!.confidence * 100).toFixed(0) }}%</span>
                        </div>
                        <p v-if="pairDecisions.get(pair.id)?.exit" class="text-sm text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ pairDecisions.get(pair.id)!.exit!.reasoning }}</p>
                        <p v-else class="text-xs text-(--color-text-secondary)">{{ pair.status === 'open' ? '尚未平倉' : '無 AI 決策記錄' }}</p>
                      </div>
                    </template>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== FLAT VIEW (original orders) ===== -->
      <section v-else class="flex flex-col min-h-0 md:flex-1">
        <h3 class="text-lg font-semibold mb-2 text-(--color-text-primary)">
          訂單紀錄
          <span v-if="filterSymbol" class="text-sm font-normal text-(--color-accent) ml-2">{{ filterSymbol }}</span>
        </h3>
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 flex flex-col min-h-0 md:flex-1 shadow-sm dark:shadow-none">
          <div v-if="loading" class="text-base text-(--color-text-secondary)">載入中...</div>
          <div v-else-if="!filteredOrders.length" class="text-base text-(--color-text-secondary)">無訂單紀錄</div>

          <!-- Desktop table -->
          <div v-if="!loading && filteredOrders.length" class="hidden md:block overflow-auto flex-1 min-h-0 table-responsive">
            <table class="w-full text-base">
              <thead>
                <tr class="text-(--color-text-secondary) text-left text-sm">
                  <th class="pb-2">狀態</th>
                  <th class="pb-2">交易對</th>
                  <th class="pb-2">方向</th>
                  <th class="pb-2">數量</th>
                  <th class="pb-2">價格</th>
                  <th class="pb-2">成交量</th>
                  <th class="pb-2">AI</th>
                  <th class="pb-2">時間</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="order in filteredOrders" :key="order.id">
                  <tr
                    class="border-t border-(--color-border) cursor-pointer transition-colors"
                    :class="expandedOrderId === order.id ? 'bg-(--color-bg-secondary)/70' : 'hover:bg-(--color-bg-secondary)/50'"
                    @click="toggleExpand(order.id, order)"
                  >
                    <td class="py-2">
                      <span class="px-2 py-0.5 rounded text-xs" :class="{
                        'bg-(--color-success)/20 text-(--color-success)': order.status === 'filled' || order.status === 'closed',
                        'bg-(--color-warning)/20 text-(--color-warning)': order.status === 'partial',
                        'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': order.status === 'cancelled',
                      }">{{ statusLabel(order.status) }}</span>
                    </td>
                    <td class="py-2 font-medium">{{ order.symbol }}</td>
                    <td class="py-2">
                      <span
                        :class="order.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'"
                        class="font-medium"
                      >{{ order.side === 'buy' ? '買入' : '賣出' }}</span>
                    </td>
                    <td class="py-2">{{ order.quantity.toFixed(6) }}</td>
                    <td class="py-2">${{ order.price?.toFixed(2) ?? '-' }}</td>
                    <td class="py-2 text-(--color-text-secondary)">{{ order.filled?.toFixed(6) ?? '-' }}</td>
                    <td class="py-2">
                      <span v-if="orderDecisions.has(order.id) && orderDecisions.get(order.id)" class="px-2 py-0.5 rounded text-xs font-medium" :class="{
                        'bg-(--color-success)/20 text-(--color-success)': orderDecisions.get(order.id)!.action === 'BUY',
                        'bg-(--color-danger)/20 text-(--color-danger)': orderDecisions.get(order.id)!.action === 'SELL',
                        'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': orderDecisions.get(order.id)!.action === 'HOLD',
                      }">{{ actionLabel(orderDecisions.get(order.id)!.action) }} {{ (orderDecisions.get(order.id)!.confidence * 100).toFixed(0) }}%</span>
                      <span v-else class="text-xs text-(--color-text-secondary)">-</span>
                    </td>
                    <td class="py-2 text-(--color-text-secondary) text-sm">{{ formatDate(order.created_at) }}</td>
                  </tr>
                  <tr v-if="expandedOrderId === order.id && !orderDecisions.has(order.id)">
                    <td colspan="8" class="pb-3 pt-0 px-2">
                      <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm text-(--color-text-secondary)">載入 AI 決策...</div>
                    </td>
                  </tr>
                  <tr v-else-if="expandedOrderId === order.id && orderDecisions.get(order.id)">
                    <td colspan="8" class="pb-3 pt-0 px-2">
                      <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm">
                        <div class="flex items-center gap-2 mb-1.5">
                          <span class="font-medium text-(--color-text-primary)">AI 決策</span>
                          <span class="px-2 py-0.5 rounded text-xs font-medium" :class="{
                            'bg-(--color-success)/20 text-(--color-success)': orderDecisions.get(order.id)!.action === 'BUY',
                            'bg-(--color-danger)/20 text-(--color-danger)': orderDecisions.get(order.id)!.action === 'SELL',
                            'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': orderDecisions.get(order.id)!.action === 'HOLD',
                          }">{{ actionLabel(orderDecisions.get(order.id)!.action) }}</span>
                          <span class="text-(--color-text-secondary)">信心 {{ (orderDecisions.get(order.id)!.confidence * 100).toFixed(0) }}%</span>
                          <span class="text-(--color-text-secondary) ml-auto text-xs">{{ orderDecisions.get(order.id)!.model }}</span>
                        </div>
                        <p class="text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ orderDecisions.get(order.id)!.reasoning }}</p>
                        <div class="mt-1.5 text-xs text-(--color-text-secondary)">cycle: {{ order.cycle_id }}</div>
                      </div>
                    </td>
                  </tr>
                  <tr v-else-if="expandedOrderId === order.id && !orderDecisions.get(order.id)">
                    <td colspan="8" class="pb-3 pt-0 px-2">
                      <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm text-(--color-text-secondary)">
                        {{ order.cycle_id ? '找不到對應的 AI 決策記錄' : '此訂單無 cycle_id（停損/停利觸發）' }}
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>

          <!-- Mobile cards -->
          <div v-if="!loading && filteredOrders.length" class="md:hidden space-y-3 overflow-auto flex-1 min-h-0">
            <div
              v-for="order in filteredOrders"
              :key="order.id"
              class="bg-(--color-bg-secondary) rounded-lg p-3"
              @click="toggleExpand(order.id, order)"
            >
              <div class="flex justify-between items-center mb-2">
                <div class="flex items-center gap-2">
                  <span
                    :class="order.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'"
                    class="font-bold text-base"
                  >{{ order.side === 'buy' ? '買入' : '賣出' }}</span>
                  <span class="font-medium text-base">{{ order.symbol }}</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <span v-if="orderDecisions.has(order.id) && orderDecisions.get(order.id)" class="px-1.5 py-0.5 rounded text-xs font-medium" :class="{
                    'bg-(--color-success)/20 text-(--color-success)': orderDecisions.get(order.id)!.action === 'BUY',
                    'bg-(--color-danger)/20 text-(--color-danger)': orderDecisions.get(order.id)!.action === 'SELL',
                  }">AI {{ (orderDecisions.get(order.id)!.confidence * 100).toFixed(0) }}%</span>
                  <span class="px-2 py-0.5 rounded text-xs" :class="{
                    'bg-(--color-success)/20 text-(--color-success)': order.status === 'filled' || order.status === 'closed',
                    'bg-(--color-warning)/20 text-(--color-warning)': order.status === 'partial',
                    'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': order.status === 'cancelled',
                  }">{{ statusLabel(order.status) }}</span>
                </div>
              </div>
              <div class="grid grid-cols-2 gap-1 text-sm text-(--color-text-secondary)">
                <div>數量: {{ order.quantity.toFixed(6) }}</div>
                <div>價格: ${{ order.price?.toFixed(2) ?? '-' }}</div>
                <div>成交: {{ order.filled?.toFixed(6) ?? '-' }}</div>
                <div>{{ formatDateShort(order.created_at) }}</div>
              </div>
              <div v-if="expandedOrderId === order.id && !orderDecisions.has(order.id)" class="mt-2 pt-2 border-t border-(--color-border)/50 text-xs text-(--color-text-secondary)">
                載入 AI 決策...
              </div>
              <div v-else-if="expandedOrderId === order.id && orderDecisions.get(order.id)" class="mt-2 pt-2 border-t border-(--color-border)/50">
                <div class="flex items-center gap-2 mb-1">
                  <span class="text-xs font-medium text-(--color-text-primary)">AI 決策</span>
                  <span class="text-xs text-(--color-text-secondary)">信心 {{ (orderDecisions.get(order.id)!.confidence * 100).toFixed(0) }}%</span>
                </div>
                <p class="text-sm text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ orderDecisions.get(order.id)!.reasoning }}</p>
              </div>
              <div v-else-if="expandedOrderId === order.id && !orderDecisions.get(order.id)" class="mt-2 pt-2 border-t border-(--color-border)/50 text-xs text-(--color-text-secondary)">
                {{ order.cycle_id ? '找不到對應的 AI 決策記錄' : '此訂單無 cycle_id' }}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
