<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRealtimeTable } from '@/composables/useRealtime'
import { useBotStore } from '@/stores/bot'
import DecisionDrawer from '@/components/DecisionDrawer.vue'
import type { StrategyVerdict, LLMDecision } from '@/types'

const bot = useBotStore()

const { rows: verdicts, loading: vLoading } = useRealtimeTable<StrategyVerdict>('strategy_verdicts', { limit: 500 })
const { rows: decisions, loading: dLoading } = useRealtimeTable<LLMDecision>('llm_decisions', { limit: 200 })

const marketTab = ref<'spot' | 'futures'>('spot')

onMounted(() => {
  if (!bot.spotPairs.length) bot.fetchConfigPairs()
})

const filteredDecisions = computed(() => {
  return decisions.value.filter(d => (d.market_type ?? 'spot') === marketTab.value && daysAgo(d.created_at) <= 3)
})

const filteredVerdicts = computed(() => {
  return verdicts.value.filter(v => (v.market_type ?? 'spot') === marketTab.value)
})

const symbols = computed(() => {
  const set = new Set<string>()
  for (const d of filteredDecisions.value) if (d.symbol) set.add(d.symbol)
  return [...set].sort((a, b) => (bot.latestPrices[b] ?? 0) - (bot.latestPrices[a] ?? 0))
})

const actionOrder = ['BUY', 'HOLD', 'SELL'] as const

/** Decisions for a symbol grouped by action, each max 2, sorted newest first */
function getGroupedDecisions(symbol: string): { action: string; label: string; cards: LLMDecision[] }[] {
  const labels: Record<string, string> = { BUY: '買入', HOLD: '觀望', SELL: '賣出' }
  return actionOrder.map(action => ({
    action,
    label: labels[action] ?? action,
    cards: filteredDecisions.value
      .filter(d => d.symbol === symbol && d.action === action)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .slice(0, 1),
  }))
}

const allStrategies = ['sma_crossover', 'rsi_oversold', 'bollinger_breakout', 'macd_momentum', 'tia_orderflow']

type VerdictSlot = { strategy: string; verdict: StrategyVerdict | null }

/** Returns all 5 strategies with their verdict for a given decision, matched by cycle_id */
function getVerdictSlots(decision: LLMDecision): VerdictSlot[] {
  const matched = filteredVerdicts.value.filter(v => v.cycle_id === decision.cycle_id && v.symbol === decision.symbol)
  return allStrategies.map(s => ({
    strategy: s,
    verdict: matched.find(v => v.strategy === s) ?? null,
  }))
}

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function signalBadgeClass(signal: string) {
  if (signal === 'BUY') return 'text-(--color-success)'
  if (signal === 'SELL') return 'text-(--color-danger)'
  return 'text-(--color-text-muted)'
}

function verdictBgStyle(signal: string, confidence: number): Record<string, string> {
  const bg = 'var(--color-bg-secondary)'
  if (confidence <= 0 || signal === 'HOLD') return { background: bg }
  const pct = Math.round(confidence * 100)
  const color = signal === 'BUY' ? 'var(--color-success)' : 'var(--color-danger)'
  return {
    background: `linear-gradient(to right, color-mix(in srgb, ${color} 25%, transparent) 0%, color-mix(in srgb, ${color} 12%, transparent) ${pct}%, transparent ${pct}%) ${bg}`,
  }
}

function signalLabel(signal: string) {
  if (signal === 'BUY') return '買入'
  if (signal === 'SELL') return '賣出'
  if (signal === 'HOLD') return '觀望'
  return signal
}

function actionBadgeClass(action: string) {
  if (action === 'BUY') return 'badge-buy'
  if (action === 'SELL') return 'badge-sell'
  return 'badge-hold'
}

function actionDotColor(action: string) {
  if (action === 'BUY') return 'var(--color-success)'
  if (action === 'SELL') return 'var(--color-danger)'
  return 'var(--color-text-secondary)'
}

/** Count decisions by action for column header stats */
function actionCounts(symbol: string): { buy: number; sell: number; hold: number } {
  const decs = filteredDecisions.value.filter(d => d.symbol === symbol)
  return {
    buy: decs.filter(d => d.action === 'BUY').length,
    sell: decs.filter(d => d.action === 'SELL').length,
    hold: decs.filter(d => d.action === 'HOLD').length,
  }
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

function isRecent(ts: string): boolean {
  return Date.now() - new Date(ts).getTime() < 4 * 3600000
}

const drawerDecision = ref<LLMDecision | null>(null)
</script>

<template>
  <div class="p-4 md:p-6 md:pb-0 flex flex-col gap-4 md:gap-6 md:h-[calc(100vh)] md:overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between shrink-0">
      <h2 class="text-2xl md:text-3xl font-bold">策略</h2>
      <div class="flex items-center gap-3">
        <span class="text-sm text-(--color-text-muted)">{{ filteredDecisions.length }} 筆決策</span>
        <div class="inline-flex rounded-lg bg-(--color-bg-secondary) p-0.5">
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="marketTab === 'spot'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="marketTab = 'spot'"
          >現貨</button>
          <button
            class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
            :class="marketTab === 'futures'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="marketTab = 'futures'"
          >合約</button>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="dLoading || vLoading" class="text-base text-(--color-text-secondary)">載入中...</div>
    <div v-else-if="!symbols.length" class="text-base text-(--color-text-secondary)">無記錄</div>

    <!-- Trello-style board -->
    <div v-else class="min-h-0 md:flex-1 overflow-x-auto overflow-y-hidden">
      <div class="flex gap-3 h-full items-start">
        <!-- Column per symbol -->
        <div
          v-for="sym in symbols"
          :key="sym"
          class="kanban-col flex flex-col shrink-0 w-[280px] max-h-full rounded-xl bg-(--color-bg-card) border border-(--color-border)"
        >
          <!-- Column header -->
          <div class="px-3 pt-3 pb-2 shrink-0">
            <div class="flex items-center justify-between mb-1">
              <span class="font-bold text-sm text-(--color-text-primary)">{{ sym.replace('/USDT', '') }}</span>
              <span class="text-xs text-(--color-text-muted) tabular-nums">{{ filteredDecisions.filter(d => d.symbol === sym).length }}</span>
            </div>
            <div class="text-xs text-(--color-text-muted) tabular-nums">
              ${{ bot.latestPrices[sym]?.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) ?? '-' }}
            </div>
          </div>

          <!-- Cards grouped by action: BUY → HOLD → SELL -->
          <div class="flex flex-col gap-1.5 px-2 pb-2 overflow-y-auto min-h-0 flex-1">
            <template v-for="group in getGroupedDecisions(sym)" :key="group.action">
              <!-- Real cards -->
              <div
                v-for="d in group.cards"
                :key="d.id"
                class="kanban-card bg-(--color-bg-card) border border-(--color-border) rounded-lg p-2.5 min-h-[240px] cursor-pointer hover:border-(--color-accent)/50 transition-colors"
                :style="cardStyle(d)"
                @click="drawerDecision = d"
              >
                <!-- Top: action dot + badge + time -->
                <div class="flex items-center justify-between mb-1.5">
                  <div class="flex items-center gap-1.5">
                    <div class="w-2 h-2 rounded-full shrink-0" :style="{ backgroundColor: isRecent(d.created_at) ? 'var(--color-warning)' : actionDotColor(d.action) }"></div>
                    <span class="text-xs font-bold" :class="isRecent(d.created_at) ? 'text-(--color-warning)' : actionBadgeClass(d.action)">{{ signalLabel(d.action) }}</span>
                  </div>
                  <span class="text-[11px] text-(--color-text-muted) tabular-nums">{{ formatTime(d.created_at) }}</span>
                </div>

                <!-- Reasoning preview -->
                <div class="text-xs text-(--color-text-secondary) leading-relaxed line-clamp-3 mb-2">{{ d.reasoning }}</div>

                <!-- Strategy verdict chips (always show all 5) -->
                <div class="flex flex-col gap-1">
                  <div
                    v-for="slot in getVerdictSlots(d)"
                    :key="slot.strategy"
                    class="flex items-center justify-between gap-1 rounded px-1.5 py-0.5"
                    :style="slot.verdict ? verdictBgStyle(slot.verdict.signal, slot.verdict.confidence) : { background: 'var(--color-bg-secondary)' }"
                  >
                    <div class="flex items-center gap-1 min-w-0">
                      <span class="text-[11px] text-(--color-text-muted) truncate">{{ slot.strategy }}</span>
                      <span v-if="slot.verdict?.timeframe" class="text-[10px] text-(--color-text-muted) opacity-60 shrink-0">{{ slot.verdict.timeframe }}</span>
                    </div>
                    <span v-if="slot.verdict" class="text-[11px] font-bold shrink-0" :class="signalBadgeClass(slot.verdict.signal)">{{ signalLabel(slot.verdict.signal) }}</span>
                    <span v-else class="text-[11px] text-(--color-text-muted) opacity-30 shrink-0">-</span>
                  </div>
                </div>
              </div>

              <!-- Ghost placeholder when this action has no card -->
              <div
                v-if="!group.cards.length"
                class="border border-dashed rounded-lg p-2.5 min-h-[240px] flex items-center"
                :style="{ borderColor: `color-mix(in srgb, ${actionDotColor(group.action)} 30%, transparent)` }"
              >
                <div class="flex items-center gap-1.5">
                  <div class="w-2 h-2 rounded-full shrink-0" :style="{ backgroundColor: actionDotColor(group.action), opacity: 0.4 }"></div>
                  <span class="text-xs opacity-40" :class="actionBadgeClass(group.action)">{{ group.label }}</span>
                  <span class="text-[11px] text-(--color-text-muted) opacity-30 ml-auto">無紀錄</span>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>

    <DecisionDrawer :decision="drawerDecision" :verdicts="filteredVerdicts" @close="drawerDecision = null" />
  </div>
</template>

<style scoped>
.kanban-col {
  box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.08);
}

.kanban-card {
  box-shadow: 0 1px 2px 0 rgb(0 0 0 / 0.06);
}
.kanban-card:hover {
  box-shadow: 0 2px 6px 0 rgb(0 0 0 / 0.08);
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
