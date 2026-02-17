<script setup lang="ts">
import { ref, onUnmounted, computed, watch } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import { useRealtimeTable } from '@/composables/useRealtime'
import DecisionDrawer from '@/components/DecisionDrawer.vue'
import type { Order, LLMDecision, StrategyVerdict } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()
const { rows: orders, loading } = useRealtimeTable<Order>('orders', { limit: 200, filter: { column: 'market_type', value: 'spot' } })

const filterMode = computed(() => bot.globalMode)
const filterSymbol = ref('')
const viewMode = ref<'all' | 'open' | 'strategy'>('all')
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

    // Unmatched buys — cross-check with positions table to avoid ghost entries
    // positions 表用 upsert (symbol) 只保留一筆，所以每個 symbol 也只取最新的開倉單
    if (unmatchedBuys.length > 0) {
      const latestBuy = unmatchedBuys[unmatchedBuys.length - 1]!
      const stillHeld = filteredPositions.value.some(p => p.symbol === symbol)
      if (stillHeld) {
        const pos = filteredPositions.value.find(p => p.symbol === symbol)
        const entryPrice = pos?.entry_price ?? latestBuy.price
        const quantity = pos?.quantity ?? latestBuy.quantity
        const currentPrice = bot.latestPrices[symbol] ?? latestBuy.price
        const pnl = (currentPrice - entryPrice) * quantity
        const pnlPct = entryPrice > 0 ? ((currentPrice - entryPrice) / entryPrice) * 100 : 0
        pairs.push({
          id: `pair-${latestBuy.id}`,
          symbol, buyOrder: latestBuy, sellOrder: null,
          entryPrice, exitPrice: null,
          quantity, pnl, pnlPct,
          holdDurationMs: Date.now() - new Date(latestBuy.created_at).getTime(),
          status: 'open',
        })
      }
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
  const realizedPnl = closed.reduce((sum, p) => sum + p.pnl, 0)
  const unrealizedPnl = open.reduce((sum, p) => sum + p.pnl, 0)
  const totalPnl = realizedPnl + unrealizedPnl
  const wins = closed.filter(p => p.pnl > 0).length
  const winRate = closed.length > 0 ? (wins / closed.length) * 100 : 0
  return { closedCount: closed.length, openCount: open.length, realizedPnl, unrealizedPnl, totalPnl, winRate }
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

const displayGroups = computed(() => {
  if (viewMode.value === 'strategy') return symbolGroups.value.filter(g => sdHasDecisions(g.symbol))
  if (viewMode.value === 'open') return symbolGroups.value.filter(g => g.openCount > 0)
  return symbolGroups.value
})

function getDisplayPairs(group: SymbolGroup): TradePair[] {
  if (viewMode.value === 'open') return group.pairs.filter(p => p.status === 'open')
  return group.pairs
}

function toggleSymbolGroup(symbol: string) {
  const s = new Set(collapsedSymbols.value)
  if (s.has(symbol)) s.delete(symbol)
  else s.add(symbol)
  collapsedSymbols.value = s
}

// ─── AI Decision: on-demand fetch ──────────────────────────
const decisionCache = ref(new Map<string, LLMDecision | null>())

async function fetchDecision(cycleId: string, symbol: string, marketType: string = 'spot'): Promise<LLMDecision | null> {
  const key = `${cycleId}:${symbol}:${marketType}`
  if (decisionCache.value.has(key)) return decisionCache.value.get(key)!
  const { data } = await supabase
    .from('llm_decisions')
    .select('*')
    .eq('cycle_id', cycleId)
    .eq('symbol', symbol)
    .eq('market_type', marketType)
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

// ─── UI helpers ────────────────────────────────────────────

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

// ─── Mini price range chart ─────────────────────────────
interface PricePoint { x: number; color: string; label: string; price: number }
interface PriceRangeResult { points: PricePoint[]; rangeX: number; rangeW: number }

function priceRangePoints(pos: { symbol: string; entry_price: number; current_price: number; stop_loss?: number | null; take_profit?: number | null }, width: number): PriceRangeResult | null {
  const entry = pos.entry_price
  const live = livePrice(pos.symbol, pos.current_price)
  const sl = pos.stop_loss ?? 0
  const tp = pos.take_profit ?? 0

  if (!entry || !live) return null

  const corePrices: number[] = [entry, live]
  if (sl > 0) corePrices.push(sl)
  if (tp > 0) corePrices.push(tp)

  const min = Math.min(...corePrices)
  const max = Math.max(...corePrices)
  if (max <= min) return null

  const range = max - min
  const padMin = min - range * 0.15
  const padMax = max + range * 0.15
  const padRange = padMax - padMin

  const toX = (p: number) => Math.max(4, Math.min(width - 4, ((p - padMin) / padRange) * width))

  const points: PricePoint[] = []
  if (sl > 0) points.push({ x: toX(sl), color: 'var(--color-danger)', label: 'SL', price: sl })
  points.push({ x: toX(entry), color: 'var(--color-text-muted)', label: 'Entry', price: entry })
  points.push({ x: toX(live), color: 'var(--color-accent)', label: 'Live', price: live })
  if (tp > 0) points.push({ x: toX(tp), color: 'var(--color-success)', label: 'TP', price: tp })

  points.sort((a, b) => a.x - b.x)

  const slPt = points.find(p => p.label === 'SL')
  const tpPt = points.find(p => p.label === 'TP')
  let rangeX = 0, rangeW = 0
  if (slPt && tpPt) {
    rangeX = Math.min(slPt.x, tpPt.x)
    rangeW = Math.abs(tpPt.x - slPt.x)
  }

  return { points, rangeX, rangeW }
}

// ─── Copy page text ─────────────────────────────────────
const copySuccess = ref(false)

function exportPageText(): string {
  const lines: string[] = []

  // USDT balance
  const usdtBal = bot.balances.find(b => b.currency === 'USDT')
  if (usdtBal) {
    lines.push(`USDT 餘額: ${usdtBal.free.toFixed(2)}`)
    lines.push('')
  }

  // Positions
  const positions = filteredPositions.value
  if (positions.length) {
    lines.push(`持倉 (${positions.length})`)
    for (const pos of positions) {
      const { pnl, pnlPct } = calcPnl(pos)
      const price = livePrice(pos.symbol, pos.current_price)
      lines.push(`  ${pos.symbol} | 數量 ${pos.quantity.toFixed(6)} | 入場 $${pos.entry_price.toFixed(2)} | 現價 $${price.toFixed(2)} | PnL ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)} (${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)${pos.stop_loss ? ` | SL $${pos.stop_loss.toFixed(2)}` : ''}${pos.take_profit ? ` | TP $${pos.take_profit.toFixed(2)}` : ''}`)
    }
    lines.push('')
  }

  // Trade pairs
  const stats = pairStats.value
  lines.push('交易紀錄')
  lines.push(`已結 ${stats.closedCount} 筆 | 勝率 ${stats.winRate.toFixed(0)}% | 已實現 ${stats.realizedPnl >= 0 ? '+' : ''}${stats.realizedPnl.toFixed(2)} | 未實現 ${stats.unrealizedPnl >= 0 ? '+' : ''}${stats.unrealizedPnl.toFixed(2)} | 總損益 ${stats.totalPnl >= 0 ? '+' : ''}${stats.totalPnl.toFixed(2)} USDT | 持倉中 ${stats.openCount}`)
  lines.push('')

  for (const group of symbolGroups.value) {
    lines.push(`${group.symbol}`)
    lines.push(`${group.pairs.length} 筆 | ${group.openCount} 持倉中 | ${group.totalPnl >= 0 ? '+' : ''}${group.totalPnl.toFixed(2)} USDT`)
    lines.push('#\t狀態\t入場\t出場\t數量\t損益\t持倉時間')
    for (let i = 0; i < group.pairs.length; i++) {
      const pair = group.pairs[i]!
      const num = group.pairs.length - i
      const status = pair.status === 'closed' ? '已結' : '持倉中'
      const entry = `$${pair.entryPrice.toFixed(2)} (${formatDateShort(pair.buyOrder.created_at)})`
      const exit = pair.sellOrder
        ? `$${pair.exitPrice!.toFixed(2)} (${formatDateShort(pair.sellOrder.created_at)})`
        : `$${livePrice(pair.symbol, pair.entryPrice).toFixed(2)} (至今)`
      const pnlStr = `${pair.pnl >= 0 ? '+' : ''}${pair.pnl.toFixed(2)} (${pair.pnlPct >= 0 ? '+' : ''}${pair.pnlPct.toFixed(2)}%)`
      lines.push(`${num}\t${status}\t${entry}\t${exit}\t${pair.quantity.toFixed(6)}\t${pnlStr}\t${formatDuration(pair.holdDurationMs)}`)
    }
    lines.push('')
  }

  return lines.join('\n')
}

async function copyPageText() {
  const text = exportPageText()
  try {
    await navigator.clipboard.writeText(text)
    copySuccess.value = true
    setTimeout(() => { copySuccess.value = false }, 2000)
  } catch {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copySuccess.value = true
    setTimeout(() => { copySuccess.value = false }, 2000)
  }
}

// ─── Strategy Decisions (inline per symbol) ─────────────
const sdDecisions = ref<LLMDecision[]>([])
const sdLoading = ref(true)
const sdVerdictCache = new Map<string, StrategyVerdict[]>()

function sdDecisionKey(d: LLMDecision) {
  return `${d.symbol}:${d.market_type ?? 'spot'}:${d.action}`
}

async function sdFetch() {
  sdLoading.value = true
  const { data } = await supabase.rpc('get_latest_decisions', { p_mode: bot.globalMode })
  if (data) {
    sdDecisions.value = (data as LLMDecision[]).filter(d => d.market_type === 'spot')
    for (const d of sdDecisions.value) {
      const raw = (d as any).verdicts
      if (raw && Array.isArray(raw) && raw.length)
        sdVerdictCache.set(`${d.cycle_id}:${d.symbol}`, raw as StrategyVerdict[])
    }
  }
  sdLoading.value = false
}
sdFetch()
watch(() => bot.globalMode, () => sdFetch())

const sdRtChannel = supabase
  .channel('rt:llm_decisions:spot-inline')
  .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'llm_decisions' }, (payload) => {
    const r = payload.new as LLMDecision
    if ((r.mode ?? 'live') !== bot.globalMode || r.market_type !== 'spot') return
    const key = sdDecisionKey(r)
    sdDecisions.value = [r, ...sdDecisions.value.filter(d => sdDecisionKey(d) !== key)]
    if (r.cycle_id) {
      const ck = `${r.cycle_id}:${r.symbol}`
      if (!sdVerdictCache.has(ck)) {
        supabase.from('strategy_verdicts').select('*').eq('cycle_id', r.cycle_id).eq('symbol', r.symbol)
          .then(({ data }) => { if (data?.length) sdVerdictCache.set(ck, data as StrategyVerdict[]) })
      }
    }
  })
  .subscribe()
onUnmounted(() => { supabase.removeChannel(sdRtChannel) })

const sdActionOrder = ['BUY', 'HOLD', 'SELL'] as const
const sdLabels: Record<string, string> = { BUY: '買入', HOLD: '觀望', SELL: '賣出' }

type SDGroupedCol = { action: string; label: string; cards: LLMDecision[] }

function sdGetGrouped(symbol: string): SDGroupedCol[] {
  return sdActionOrder.map(action => ({
    action, label: sdLabels[action] ?? action,
    cards: sdDecisions.value
      .filter(d => d.symbol === symbol && d.action === action)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .slice(0, 1),
  }))
}

function sdHasDecisions(symbol: string): boolean {
  return sdDecisions.value.some(d => d.symbol === symbol)
}

const sdAllStrategies = ['sma_crossover', 'rsi_oversold', 'bollinger_breakout', 'macd_momentum', 'vwap_reversion', 'ema_ribbon', 'tia_orderflow']
const sdStrategyShort: Record<string, string> = {
  sma_crossover: 'SMA', rsi_oversold: 'RSI', bollinger_breakout: 'BOLL',
  macd_momentum: 'MACD', vwap_reversion: 'VWAP', ema_ribbon: 'EMA', tia_orderflow: 'OFlow',
}

type SDVerdictSlot = { strategy: string; verdict: StrategyVerdict | null }

function sdGetVerdictSlots(d: LLMDecision): SDVerdictSlot[] {
  const matched = sdVerdictCache.get(`${d.cycle_id}:${d.symbol}`) ?? []
  const best = new Map<string, StrategyVerdict>()
  for (const v of matched) {
    const prev = best.get(v.strategy)
    if (!prev || v.confidence > prev.confidence) best.set(v.strategy, v)
  }
  return sdAllStrategies.map(s => ({ strategy: s, verdict: best.get(s) ?? null }))
}

function sdTimeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小時前`
  return `${Math.floor(diff / 86400)}天前`
}

function sdSignalLabel(s: string) { return s === 'BUY' ? '買入' : s === 'SELL' ? '賣出' : '觀望' }
function sdBadgeClass(a: string) { return a === 'BUY' ? 'text-(--color-success)' : a === 'SELL' ? 'text-(--color-danger)' : 'text-(--color-text-secondary)' }
function sdDotColor(a: string) { return a === 'BUY' ? 'var(--color-success)' : a === 'SELL' ? 'var(--color-danger)' : 'var(--color-text-secondary)' }
function sdBarColor(s: string) { return s === 'BUY' ? 'var(--color-success)' : s === 'SELL' ? 'var(--color-danger)' : 'var(--color-text-secondary)' }

function sdCardStyle(d: LLMDecision): Record<string, string> {
  const now = new Date(); const dt = new Date(d.created_at)
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() - new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()).getTime()) / 86400000)
  const style: Record<string, string> = {}
  if (days === 0) style.borderColor = 'color-mix(in srgb, var(--color-warning) 60%, transparent)'
  if (days > 0) style.opacity = ({ 1: '0.8', 2: '0.6', 3: '0.4' } as Record<number, string>)[days] ?? '0.4'
  return style
}

const sdDrawerDecision = ref<LLMDecision | null>(null)
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">

    <!-- Header -->
    <h2 class="text-2xl font-bold md:hidden">現貨</h2>

    <!-- Toolbar -->
    <div class="flex items-center justify-end gap-2 flex-wrap">
      <!-- Copy button -->
      <button
        class="p-1.5 rounded-lg transition-colors"
        :class="copySuccess ? 'text-(--color-success) bg-(--color-success)/10' : 'text-(--color-text-secondary) hover:bg-(--color-bg-secondary)'"
        title="複製頁面資訊"
        @click="copyPageText"
      >
        <svg v-if="!copySuccess" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
      </button>
      <!-- View mode toggle -->
      <div class="inline-flex rounded-lg bg-(--color-bg-secondary) p-0.5">
        <button
          v-for="opt in ([['all', '全部'], ['open', '持倉'], ['strategy', '策略']] as const)"
          :key="opt[0]"
          class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
          :class="viewMode === opt[0]
            ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
            : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
          @click="viewMode = opt[0]"
        >{{ opt[1] }}</button>
      </div>
    </div>

    <!-- Summary Cards -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-2 md:gap-3">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3 shadow-sm dark:shadow-none">
        <div class="text-xs text-(--color-text-secondary)">USDT 餘額</div>
        <div class="text-lg md:text-xl font-bold font-mono mt-0.5">
          {{ bot.balances.find(b => b.currency === 'USDT')?.free.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '--' }}
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3 shadow-sm dark:shadow-none">
        <div class="text-xs text-(--color-text-secondary)">已實現損益</div>
        <div class="text-lg md:text-xl font-bold font-mono mt-0.5" :class="pnlColor(pairStats.realizedPnl)">
          {{ pnlSign(pairStats.realizedPnl) }}{{ pairStats.realizedPnl.toFixed(2) }}
        </div>
        <div class="text-[11px] text-(--color-text-muted) mt-0.5">勝率 {{ pairStats.winRate.toFixed(0) }}%</div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3 shadow-sm dark:shadow-none">
        <div class="text-xs text-(--color-text-secondary)">未實現損益</div>
        <div class="text-lg md:text-xl font-bold font-mono mt-0.5" :class="pnlColor(pairStats.unrealizedPnl)">
          {{ pnlSign(pairStats.unrealizedPnl) }}{{ pairStats.unrealizedPnl.toFixed(2) }}
        </div>
        <div class="text-[11px] text-(--color-text-muted) mt-0.5">{{ pairStats.openCount }} 筆持倉中</div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3 shadow-sm dark:shadow-none">
        <div class="text-xs text-(--color-text-secondary)">總損益</div>
        <div class="text-lg md:text-xl font-bold font-mono mt-0.5" :class="pnlColor(pairStats.totalPnl)">
          {{ pnlSign(pairStats.totalPnl) }}{{ pairStats.totalPnl.toFixed(2) }}
        </div>
        <div class="text-[11px] text-(--color-text-muted) mt-0.5">USDT</div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3 shadow-sm dark:shadow-none">
        <div class="text-xs text-(--color-text-secondary)">總資產</div>
        <div class="text-lg md:text-xl font-bold font-mono mt-0.5">
          {{ bot.totalUsdt?.toLocaleString('en', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '--' }}
        </div>
        <div class="text-[11px] text-(--color-text-muted) mt-0.5">USDT</div>
      </div>
    </div>

      <!-- ===== Positions: horizontal scroll ===== -->
      <section v-if="viewMode !== 'strategy'">
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
              <!-- Mini price range chart -->
              <div v-if="priceRangePoints(pos, 190)" class="mt-2 pt-2 border-t border-(--color-border)/30">
                <svg width="190" height="24" class="w-full" viewBox="0 0 190 24">
                  <line x1="0" y1="12" x2="190" y2="12" stroke="var(--color-border)" stroke-width="1" />
                  <rect v-if="priceRangePoints(pos, 190)!.rangeW > 0"
                    :x="priceRangePoints(pos, 190)!.rangeX" y="8" :width="priceRangePoints(pos, 190)!.rangeW"
                    height="8" rx="3" fill="var(--color-text-muted)" opacity="0.1"
                  />
                  <template v-for="pt in priceRangePoints(pos, 190)!.points" :key="pt.label">
                    <template v-if="pt.label === 'Live'">
                      <circle :cx="pt.x" cy="12" r="5" :fill="pt.color" opacity="0.15" />
                      <circle :cx="pt.x" cy="12" r="3.5" :fill="pt.color" />
                      <text :x="pt.x" y="22" text-anchor="middle" :fill="pt.color" font-size="7" font-weight="600">{{ pt.label }}</text>
                    </template>
                    <template v-else>
                      <circle :cx="pt.x" cy="12" r="2.5" :fill="pt.color" />
                      <text :x="pt.x" y="22" text-anchor="middle" :fill="pt.color" font-size="7" font-weight="500">{{ pt.label }}</text>
                    </template>
                  </template>
                </svg>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== PAIRS VIEW (grouped by symbol) ===== -->
      <section class="flex flex-col min-h-0">
        <div class="flex flex-col gap-3">
          <div v-if="loading" class="text-base text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">載入中...</div>
          <div v-else-if="!displayGroups.length" class="text-base text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">
            {{ viewMode === 'open' ? '目前無持倉中的交易' : '無交易紀錄' }}
          </div>

          <!-- Symbol groups -->
          <div
            v-for="group in displayGroups"
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
                <span class="text-sm text-(--color-text-secondary)">{{ group.closedCount > 0 ? `${(group.winCount / group.closedCount * 100).toFixed(0)}% 勝率` : `${group.openCount} 持倉中` }}</span>
              </div>
              <span class="font-bold text-sm" :class="pnlColor(group.totalPnl)">{{ pnlSign(group.totalPnl) }}{{ group.totalPnl.toFixed(2) }} USDT</span>
            </div>

            <!-- Group content (collapsible) -->
            <div v-if="!collapsedSymbols.has(group.symbol)" class="border-t border-(--color-border)">

              <!-- Inline strategy decisions for this symbol -->
              <div v-if="sdHasDecisions(group.symbol)" class="border-b border-(--color-border)/50">
                <!-- Desktop: 3 action columns -->
                <div class="hidden md:grid grid-cols-[1fr_1fr_1fr] gap-0">
                  <template v-for="(col, ci) in sdGetGrouped(group.symbol)" :key="col.action">
                    <div class="px-1.5 py-2" :class="{ 'border-r border-(--color-border)/30': ci < 2 }">
                      <div class="flex items-center gap-1 mb-1 px-0.5">
                        <div class="w-1.5 h-1.5 rounded-full" :style="{ backgroundColor: sdDotColor(col.action) }"></div>
                        <span class="text-[9px] font-semibold" :class="sdBadgeClass(col.action)">{{ col.label }}</span>
                      </div>
                      <div v-for="d in col.cards" :key="d.id"
                        class="sd-card relative border border-(--color-border) rounded-lg cursor-pointer hover:border-(--color-accent)/50 transition-colors h-[90px] overflow-hidden"
                        :style="sdCardStyle(d)" @click="sdDrawerDecision = d">
                        <div class="absolute inset-x-0 bottom-0 flex items-end justify-center gap-[3px] h-full px-1.5 pb-0.5 pointer-events-none">
                          <div v-for="slot in sdGetVerdictSlots(d)" :key="slot.strategy" class="flex-1 rounded-t-sm"
                            :style="{ height: slot.verdict ? `${Math.max(slot.verdict.confidence * 100, 8)}%` : '8%', backgroundColor: slot.verdict ? sdBarColor(slot.verdict.signal) : 'var(--color-border)', opacity: slot.verdict ? '0.10' : '0.04' }"
                            :title="`${sdStrategyShort[slot.strategy] ?? slot.strategy}: ${slot.verdict ? (slot.verdict.confidence * 100).toFixed(0) + '% ' + sdSignalLabel(slot.verdict.signal) : '-'}`"
                          ></div>
                        </div>
                        <span class="absolute top-0 right-0 text-[9px] text-(--color-text-muted) bg-(--color-bg-card)/80 px-1 py-px rounded">{{ sdTimeAgo(d.created_at) }}</span>
                        <div class="relative z-10 p-1.5 h-full flex flex-col overflow-hidden">
                          <span v-if="d.executed === false" class="text-[9px] px-1 py-px rounded bg-(--color-warning-subtle) text-(--color-warning) font-medium self-start mb-0.5 shrink-0">攔截</span>
                          <div class="text-[11px] text-(--color-text-secondary) leading-relaxed line-clamp-3 flex-1">{{ d.reasoning }}</div>
                        </div>
                      </div>
                      <div v-if="!col.cards.length" class="rounded-lg border border-dashed flex items-center justify-center h-[90px]"
                        :style="{ borderColor: `color-mix(in srgb, ${sdDotColor(col.action)} 20%, transparent)` }">
                        <span class="text-[9px] text-(--color-text-muted) opacity-30">-</span>
                      </div>
                    </div>
                  </template>
                </div>
                <!-- Mobile: 3 columns -->
                <div class="md:hidden grid grid-cols-3 gap-0">
                  <template v-for="(col, ci) in sdGetGrouped(group.symbol)" :key="col.action">
                    <div class="px-1.5 py-2" :class="{ 'border-r border-(--color-border)/30': ci < 2 }">
                      <div class="flex items-center gap-1 mb-1 px-0.5">
                        <div class="w-1.5 h-1.5 rounded-full" :style="{ backgroundColor: sdDotColor(col.action) }"></div>
                        <span class="text-[9px] font-semibold" :class="sdBadgeClass(col.action)">{{ col.label }}</span>
                      </div>
                      <div v-for="d in col.cards" :key="d.id"
                        class="sd-card relative border border-(--color-border) rounded-lg cursor-pointer h-[80px] overflow-hidden"
                        :style="sdCardStyle(d)" @click="sdDrawerDecision = d">
                        <div class="absolute inset-x-0 bottom-0 flex items-end justify-center gap-[3px] h-full px-1.5 pb-0.5 pointer-events-none">
                          <div v-for="slot in sdGetVerdictSlots(d)" :key="slot.strategy" class="flex-1 rounded-t-sm"
                            :style="{ height: slot.verdict ? `${Math.max(slot.verdict.confidence * 100, 8)}%` : '8%', backgroundColor: slot.verdict ? sdBarColor(slot.verdict.signal) : 'var(--color-border)', opacity: slot.verdict ? '0.10' : '0.04' }"
                          ></div>
                        </div>
                        <span class="absolute top-0 right-0 text-[9px] text-(--color-text-muted) bg-(--color-bg-card)/80 px-1 py-px rounded">{{ sdTimeAgo(d.created_at) }}</span>
                        <div class="relative z-10 p-1.5 h-full flex flex-col overflow-hidden">
                          <div class="text-[11px] text-(--color-text-secondary) leading-relaxed line-clamp-3 flex-1">{{ d.reasoning }}</div>
                        </div>
                      </div>
                      <div v-if="!col.cards.length" class="rounded-lg border border-dashed flex items-center justify-center h-[80px]"
                        :style="{ borderColor: `color-mix(in srgb, ${sdDotColor(col.action)} 20%, transparent)` }">
                        <span class="text-[9px] text-(--color-text-muted) opacity-30">-</span>
                      </div>
                    </div>
                  </template>
                </div>
              </div>

              <!-- Desktop table -->
              <table v-if="viewMode !== 'strategy'" class="w-full text-base hidden md:table">
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
                  <template v-for="(pair, idx) in getDisplayPairs(group)" :key="pair.id">
                    <tr
                      class="border-t border-(--color-border)/50 cursor-pointer transition-colors"
                      :class="expandedPairId === pair.id ? 'bg-(--color-bg-secondary)/70' : 'hover:bg-(--color-bg-secondary)/50'"
                      @click="togglePairExpand(pair)"
                    >
                      <td class="px-4 py-2 text-sm text-(--color-text-muted)">{{ getDisplayPairs(group).length - idx }}</td>
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
                          <div
                            class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm transition-colors"
                            :class="pairDecisions.get(pair.id)?.entry ? 'cursor-pointer hover:bg-(--color-bg-secondary)/80' : ''"
                            @click.stop="pairDecisions.get(pair.id)?.entry && (sdDrawerDecision = pairDecisions.get(pair.id)!.entry)"
                          >
                            <div class="flex items-center gap-2 mb-1.5">
                              <span class="text-xs font-bold text-(--color-success)">開倉決策</span>
                              <span v-if="pairDecisions.get(pair.id)?.entry" class="text-xs text-(--color-text-secondary)">
                                信心 {{ (pairDecisions.get(pair.id)!.entry!.confidence * 100).toFixed(0) }}%
                              </span>
                            </div>
                            <p v-if="pairDecisions.get(pair.id)?.entry" class="text-(--color-text-primary) leading-relaxed whitespace-pre-wrap text-sm">{{ pairDecisions.get(pair.id)!.entry!.reasoning }}</p>
                            <p v-else class="text-(--color-text-secondary)">無 AI 決策記錄</p>
                          </div>
                          <div
                            class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm transition-colors"
                            :class="pairDecisions.get(pair.id)?.exit ? 'cursor-pointer hover:bg-(--color-bg-secondary)/80' : ''"
                            @click.stop="pairDecisions.get(pair.id)?.exit && (sdDrawerDecision = pairDecisions.get(pair.id)!.exit)"
                          >
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
              <div v-if="viewMode !== 'strategy'" class="md:hidden p-3 space-y-2">
                <div
                  v-for="(pair, idx) in getDisplayPairs(group)"
                  :key="pair.id"
                  class="bg-(--color-bg-secondary) rounded-lg p-3"
                  @click="togglePairExpand(pair)"
                >
                  <div class="flex justify-between items-center mb-2">
                    <div class="flex items-center gap-2">
                      <span class="text-xs text-(--color-text-muted)">#{{ getDisplayPairs(group).length - idx }}</span>
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
                  <div v-if="expandedPairId === pair.id" class="mt-2 pt-2 border-t border-(--color-border)/50 space-y-2" @click.stop>
                    <div v-if="!pairDecisions.has(pair.id)" class="text-xs text-(--color-text-secondary)">載入 AI 決策...</div>
                    <template v-else>
                      <div
                        :class="pairDecisions.get(pair.id)?.entry ? 'cursor-pointer active:bg-(--color-bg-card)/50 rounded-lg -mx-1 px-1 py-0.5' : ''"
                        @click="pairDecisions.get(pair.id)?.entry && (sdDrawerDecision = pairDecisions.get(pair.id)!.entry)"
                      >
                        <div class="flex items-center gap-2 mb-1">
                          <span class="text-xs font-bold text-(--color-success)">開倉決策</span>
                          <span v-if="pairDecisions.get(pair.id)?.entry" class="text-xs text-(--color-text-secondary)">信心 {{ (pairDecisions.get(pair.id)!.entry!.confidence * 100).toFixed(0) }}%</span>
                        </div>
                        <p v-if="pairDecisions.get(pair.id)?.entry" class="text-sm text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ pairDecisions.get(pair.id)!.entry!.reasoning }}</p>
                        <p v-else class="text-xs text-(--color-text-secondary)">無 AI 決策記錄</p>
                      </div>
                      <div
                        :class="pairDecisions.get(pair.id)?.exit ? 'cursor-pointer active:bg-(--color-bg-card)/50 rounded-lg -mx-1 px-1 py-0.5' : ''"
                        @click="pairDecisions.get(pair.id)?.exit && (sdDrawerDecision = pairDecisions.get(pair.id)!.exit)"
                      >
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

    <DecisionDrawer :decision="sdDrawerDecision" @close="sdDrawerDecision = null" />
  </div>
</template>

<style scoped>
.sd-card {
  box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.04);
  text-shadow: 0 0 1px #999;
}
.sd-card:hover {
  box-shadow: 0 2px 4px 0 rgb(0 0 0 / 0.06);
}
</style>
