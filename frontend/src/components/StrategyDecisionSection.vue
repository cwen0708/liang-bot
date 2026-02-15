<script setup lang="ts">
import { ref, computed, onUnmounted, watch } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import { useBotStore } from '@/stores/bot'
import DecisionDrawer from '@/components/DecisionDrawer.vue'
import type { StrategyVerdict, LLMDecision } from '@/types'

const props = defineProps<{
  marketType: 'spot' | 'futures'
  filterSymbol?: string
}>()

const bot = useBotStore()
const supabase = useSupabase()

// ─── Decisions: RPC + Realtime ──────────────────────────
const decisions = ref<LLMDecision[]>([])
const dLoading = ref(true)

function decisionKey(d: LLMDecision) {
  return `${d.symbol}:${d.market_type ?? 'spot'}:${d.action}`
}

async function fetchDecisions() {
  dLoading.value = true
  const { data } = await supabase.rpc('get_latest_decisions', { p_mode: bot.globalMode })
  if (data) {
    const all = data as LLMDecision[]
    decisions.value = all.filter(d => (d.market_type ?? 'spot') === props.marketType)
    for (const d of decisions.value) {
      const raw = (d as any).verdicts
      if (raw && Array.isArray(raw) && raw.length) {
        verdictCache.set(`${d.cycle_id}:${d.symbol}`, raw as StrategyVerdict[])
      }
    }
  }
  dLoading.value = false
}

fetchDecisions()
watch(() => bot.globalMode, () => fetchDecisions())

const rtChannel = supabase
  .channel(`rt:llm_decisions:section-${props.marketType}`)
  .on(
    'postgres_changes',
    { event: 'INSERT', schema: 'public', table: 'llm_decisions' },
    (payload) => {
      const newRow = payload.new as LLMDecision
      if ((newRow.mode ?? 'live') !== bot.globalMode) return
      if ((newRow.market_type ?? 'spot') !== props.marketType) return
      const key = decisionKey(newRow)
      decisions.value = [
        newRow,
        ...decisions.value.filter(d => decisionKey(d) !== key),
      ]
      fetchVerdictsForDecision(newRow)
    },
  )
  .subscribe()

onUnmounted(() => {
  supabase.removeChannel(rtChannel)
})

// ─── Verdicts cache ─────────────────────────────────────
const verdictCache = new Map<string, StrategyVerdict[]>()

async function fetchVerdictsForDecision(d: LLMDecision) {
  if (!d.cycle_id) return
  const key = `${d.cycle_id}:${d.symbol}`
  if (verdictCache.has(key)) return
  const { data } = await supabase
    .from('strategy_verdicts')
    .select('*')
    .eq('cycle_id', d.cycle_id)
    .eq('symbol', d.symbol)
  if (data?.length) verdictCache.set(key, data as StrategyVerdict[])
}

// ─── Symbols & Grouping ────────────────────────────────
const symbols = computed(() => {
  const set = new Set<string>()
  for (const d of decisions.value) if (d.symbol) set.add(d.symbol)
  let list = [...set].sort((a, b) => (bot.latestPrices[b] ?? 0) - (bot.latestPrices[a] ?? 0))
  if (props.filterSymbol) list = list.filter(s => s === props.filterSymbol)
  return list
})

const spotActionOrder = ['BUY', 'HOLD', 'SELL'] as const
const futuresActionOrder = ['BUY', 'COVER', 'HOLD', 'SELL', 'SHORT'] as const
const spotLabelsMap: Record<string, string> = { BUY: '買入', HOLD: '觀望', SELL: '賣出' }
const futuresLabelsMap: Record<string, string> = { BUY: '做多', COVER: '平空', HOLD: '觀望', SELL: '平多', SHORT: '做空' }

const actionOrder = computed(() => props.marketType === 'spot' ? spotActionOrder : futuresActionOrder)
const labelsMap = computed(() => props.marketType === 'spot' ? spotLabelsMap : futuresLabelsMap)

type GroupedColumn = { action: string; label: string; cards: LLMDecision[] }

function getGrouped(symbol: string): GroupedColumn[] {
  return actionOrder.value.map(action => ({
    action,
    label: labelsMap.value[action] ?? action,
    cards: decisions.value
      .filter(d => d.symbol === symbol && d.action === action)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .slice(0, 1),
  }))
}

// ─── Strategies ─────────────────────────────────────────
const allStrategies = ['sma_crossover', 'rsi_oversold', 'bollinger_breakout', 'macd_momentum', 'vwap_reversion', 'ema_ribbon', 'tia_orderflow']
const strategyShort: Record<string, string> = {
  sma_crossover: 'SMA', rsi_oversold: 'RSI', bollinger_breakout: 'BOLL',
  macd_momentum: 'MACD', vwap_reversion: 'VWAP', ema_ribbon: 'EMA', tia_orderflow: 'OFlow',
}

type VerdictSlot = { strategy: string; verdict: StrategyVerdict | null }

function getVerdictSlots(decision: LLMDecision): VerdictSlot[] {
  const key = `${decision.cycle_id}:${decision.symbol}`
  const matched = verdictCache.get(key) ?? []
  const best = new Map<string, StrategyVerdict>()
  for (const v of matched) {
    const prev = best.get(v.strategy)
    if (!prev || v.confidence > prev.confidence) best.set(v.strategy, v)
  }
  return allStrategies.map(s => ({ strategy: s, verdict: best.get(s) ?? null }))
}

// ─── UI helpers ─────────────────────────────────────────
function timeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小時前`
  return `${Math.floor(diff / 86400)}天前`
}

function signalLabel(signal: string) {
  if (signal === 'BUY') return '買入'
  if (signal === 'SELL') return '賣出'
  if (signal === 'SHORT') return '做空'
  if (signal === 'COVER') return '平空'
  if (signal === 'HOLD') return '觀望'
  return signal
}

function actionBadgeClass(action: string) {
  if (action === 'BUY' || action === 'COVER') return 'badge-buy'
  if (action === 'SELL' || action === 'SHORT') return 'badge-sell'
  return 'badge-hold'
}

function actionDotColor(action: string) {
  if (action === 'BUY' || action === 'COVER') return 'var(--color-success)'
  if (action === 'SELL' || action === 'SHORT') return 'var(--color-danger)'
  return 'var(--color-text-secondary)'
}

function barColor(signal: string): string {
  if (signal === 'BUY' || signal === 'COVER') return 'var(--color-success)'
  if (signal === 'SELL' || signal === 'SHORT') return 'var(--color-danger)'
  return 'var(--color-text-secondary)'
}

function daysAgo(ts: string): number {
  const now = new Date()
  const d = new Date(ts)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dStart = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  return Math.round((todayStart.getTime() - dStart.getTime()) / 86400000)
}

function cardStyle(d: LLMDecision): Record<string, string> {
  const days = daysAgo(d.created_at)
  const opacityMap: Record<number, string> = { 0: '1', 1: '0.8', 2: '0.6', 3: '0.4' }
  const style: Record<string, string> = {}
  if (days === 0) style.borderColor = 'color-mix(in srgb, var(--color-warning) 60%, transparent)'
  if (days > 0) style.opacity = opacityMap[days] ?? '0.4'
  return style
}

const drawerDecision = ref<LLMDecision | null>(null)
</script>

<template>
  <section class="shrink-0">
    <h3 class="text-lg font-semibold text-(--color-text-primary) mb-3">策略決策</h3>

    <div v-if="dLoading" class="text-sm text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">載入中...</div>
    <div v-else-if="!symbols.length" class="text-sm text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">無策略記錄</div>

    <div v-else class="flex flex-col gap-2">
      <!-- Column headers (desktop) -->
      <div
        class="hidden md:grid gap-0 mb-1 px-1"
        :class="marketType === 'spot'
          ? 'grid-cols-[100px_2fr_1fr_2fr]'
          : 'grid-cols-[100px_1fr_1fr_1fr_1fr_1fr]'"
      >
        <div></div>
        <div v-for="action in actionOrder" :key="action" class="flex items-center gap-1 px-2">
          <div class="w-1.5 h-1.5 rounded-full" :style="{ backgroundColor: actionDotColor(action) }"></div>
          <span class="text-[10px] font-semibold uppercase tracking-wide" :class="actionBadgeClass(action)">{{ labelsMap[action] }}</span>
        </div>
      </div>

      <!-- Symbol rows -->
      <div
        v-for="sym in symbols"
        :key="sym"
        class="symbol-row bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden"
      >
        <!-- ===== Desktop ===== -->
        <div
          class="hidden md:grid gap-0 min-h-[80px]"
          :class="marketType === 'spot'
            ? 'grid-cols-[100px_2fr_1fr_2fr]'
            : 'grid-cols-[100px_1fr_1fr_1fr_1fr_1fr]'"
        >
          <!-- Symbol label -->
          <div class="flex flex-col justify-center px-3 py-2 border-r border-(--color-border)/50">
            <span class="font-bold text-sm text-(--color-text-primary)">{{ sym.replace('/USDT', '') }}</span>
            <span class="text-xs text-(--color-text-muted) tabular-nums">${{ bot.latestPrices[sym]?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '-' }}</span>
          </div>

          <!-- Action columns -->
          <template v-for="(group, gi) in getGrouped(sym)" :key="group.action">
            <div
              class="px-1.5 py-2"
              :class="{ 'border-r border-(--color-border)/30': gi < actionOrder.length - 1 }"
            >
              <div
                v-for="d in group.cards"
                :key="d.id"
                class="decision-card relative border border-(--color-border) rounded-lg cursor-pointer hover:border-(--color-accent)/50 transition-colors h-[115px] overflow-hidden"
                :style="cardStyle(d)"
                @click="drawerDecision = d"
              >
                <!-- Background bars -->
                <div class="absolute inset-x-0 bottom-0 flex items-end justify-center gap-[3px] h-full px-2 pb-1 pointer-events-none">
                  <div
                    v-for="slot in getVerdictSlots(d)"
                    :key="slot.strategy"
                    class="flex-1 rounded-t-sm transition-all"
                    :style="{
                      height: slot.verdict ? `${Math.max(slot.verdict.confidence * 100, 8)}%` : '8%',
                      backgroundColor: slot.verdict ? barColor(slot.verdict.signal) : 'var(--color-border)',
                      opacity: slot.verdict ? '0.10' : '0.04',
                    }"
                    :title="`${strategyShort[slot.strategy] ?? slot.strategy}: ${slot.verdict ? (slot.verdict.confidence * 100).toFixed(0) + '% ' + signalLabel(slot.verdict.signal) : '無資料'}`"
                  ></div>
                </div>
                <!-- Time badge -->
                <span class="absolute top-0 right-0 text-[10px] text-(--color-text-muted) bg-(--color-bg-card)/80 px-1 py-px rounded">{{ timeAgo(d.created_at) }}</span>
                <!-- Text overlay -->
                <div class="relative z-10 p-2 h-full flex flex-col overflow-hidden">
                  <span v-if="d.executed === false" class="text-[9px] px-1 py-px rounded bg-(--color-warning-subtle) text-(--color-warning) font-medium self-start mb-1 shrink-0">攔截</span>
                  <div class="text-xs text-(--color-text-secondary) leading-relaxed flex-1">{{ d.reasoning }}</div>
                </div>
              </div>

              <!-- Ghost -->
              <div
                v-if="!group.cards.length"
                class="rounded-lg p-2 border border-dashed flex items-center justify-center h-[115px]"
                :style="{ borderColor: `color-mix(in srgb, ${actionDotColor(group.action)} 20%, transparent)` }"
              >
                <span class="text-[11px] text-(--color-text-muted) opacity-30">無紀錄</span>
              </div>
            </div>
          </template>
        </div>

        <!-- ===== Mobile ===== -->
        <div class="md:hidden">
          <div class="flex items-center justify-between px-3 py-2 border-b border-(--color-border)/50">
            <span class="font-bold text-sm text-(--color-text-primary)">{{ sym.replace('/USDT', '') }}</span>
            <span class="text-xs text-(--color-text-muted) tabular-nums">${{ bot.latestPrices[sym]?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '-' }}</span>
          </div>
          <div :class="marketType === 'futures' ? 'overflow-x-auto' : ''">
            <div
              class="grid gap-0"
              :class="marketType === 'spot' ? 'grid-cols-3' : 'grid-cols-5'"
              :style="marketType === 'futures' ? 'min-width: 500px' : ''"
            >
              <template v-for="(group, gi) in getGrouped(sym)" :key="group.action">
                <div class="px-1.5 py-2" :class="{ 'border-r border-(--color-border)/30': gi < actionOrder.length - 1 }">
                  <div class="flex items-center gap-1 mb-1 px-0.5">
                    <div class="w-1.5 h-1.5 rounded-full" :style="{ backgroundColor: actionDotColor(group.action) }"></div>
                    <span class="text-[9px] font-semibold" :class="actionBadgeClass(group.action)">{{ group.label }}</span>
                  </div>
                  <div
                    v-for="d in group.cards"
                    :key="d.id"
                    class="decision-card relative border border-(--color-border) rounded-lg cursor-pointer h-[100px] overflow-hidden"
                    :style="cardStyle(d)"
                    @click="drawerDecision = d"
                  >
                    <div class="absolute inset-x-0 bottom-0 flex items-end justify-center gap-[3px] h-full px-1.5 pb-0.5 pointer-events-none">
                      <div
                        v-for="slot in getVerdictSlots(d)"
                        :key="slot.strategy"
                        class="flex-1 rounded-t-sm"
                        :style="{
                          height: slot.verdict ? `${Math.max(slot.verdict.confidence * 100, 8)}%` : '8%',
                          backgroundColor: slot.verdict ? barColor(slot.verdict.signal) : 'var(--color-border)',
                          opacity: slot.verdict ? '0.10' : '0.04',
                        }"
                      ></div>
                    </div>
                    <span class="absolute top-0 right-0 text-[9px] text-(--color-text-muted) bg-(--color-bg-card)/80 px-1 py-px rounded">{{ timeAgo(d.created_at) }}</span>
                    <div class="relative z-10 p-1.5 h-full flex flex-col overflow-hidden">
                      <div class="text-[11px] text-(--color-text-secondary) leading-relaxed line-clamp-4 flex-1">{{ d.reasoning }}</div>
                    </div>
                  </div>
                  <div
                    v-if="!group.cards.length"
                    class="rounded-lg p-1.5 border border-dashed flex items-center justify-center h-[100px]"
                    :style="{ borderColor: `color-mix(in srgb, ${actionDotColor(group.action)} 20%, transparent)` }"
                  >
                    <span class="text-[10px] text-(--color-text-muted) opacity-30">-</span>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <DecisionDrawer :decision="drawerDecision" @close="drawerDecision = null" />
  </section>
</template>

<style scoped>
.symbol-row {
  box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.06);
}

.decision-card {
  box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.04);
  text-shadow: 0 0 1px #999;
}
.decision-card:hover {
  box-shadow: 0 2px 4px 0 rgb(0 0 0 / 0.06);
}

.badge-buy {
  color: var(--color-success);
}
.badge-sell {
  color: var(--color-danger);
}
.badge-hold {
  color: var(--color-text-secondary);
}
</style>
