<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import type { LLMDecision, StrategyVerdict, Order } from '@/types'

const props = defineProps<{
  decision: LLMDecision | null
}>()

const emit = defineEmits<{
  close: []
}>()

const supabase = useSupabase()
const verdicts = ref<StrategyVerdict[]>([])
const orders = ref<Order[]>([])
const loading = ref(false)
const selectedTimeframe = ref<string | null>(null)
const verdictCache = new Map<string, StrategyVerdict[]>()

const allStrategies = ['sma_crossover', 'rsi_oversold', 'bollinger_breakout', 'macd_momentum', 'vwap_reversion', 'ema_ribbon', 'tia_orderflow']
const strategyLabel: Record<string, string> = {
  sma_crossover: 'SMA 交叉',
  rsi_oversold: 'RSI 超買賣',
  bollinger_breakout: '布林突破',
  macd_momentum: 'MACD 動能',
  vwap_reversion: 'VWAP 回歸',
  ema_ribbon: 'EMA 絲帶',
  tia_orderflow: '訂單流',
}

type VerdictSlot = { strategy: string; verdict: StrategyVerdict | null }

/** When decision changes, fetch verdicts + orders by cycle_id */
watch(() => props.decision, async (d) => {
  verdicts.value = []
  orders.value = []
  selectedTimeframe.value = null
  if (!d?.cycle_id) return

  const cacheKey = `${d.cycle_id}:${d.symbol}`
  const cached = verdictCache.get(cacheKey)
  if (cached) {
    verdicts.value = cached
    // orders still need fresh query (may change)
    const { data } = await supabase
      .from('orders')
      .select('*')
      .eq('cycle_id', d.cycle_id)
      .eq('symbol', d.symbol)
      .order('created_at', { ascending: false })
    if (data) orders.value = data as Order[]
    return
  }

  loading.value = true
  const [verdictRes, orderRes] = await Promise.all([
    supabase
      .from('strategy_verdicts')
      .select('*')
      .eq('cycle_id', d.cycle_id)
      .eq('symbol', d.symbol),
    supabase
      .from('orders')
      .select('*')
      .eq('cycle_id', d.cycle_id)
      .eq('symbol', d.symbol)
      .order('created_at', { ascending: false }),
  ])
  if (verdictRes.data) {
    verdicts.value = verdictRes.data as StrategyVerdict[]
    verdictCache.set(cacheKey, verdicts.value)
  }
  if (orderRes.data) orders.value = orderRes.data as Order[]
  loading.value = false
})

const availableTimeframes = computed(() => {
  const tfs = new Set(verdicts.value.map(v => v.timeframe))
  return ['15m', '1h', '4h', '1d'].filter(tf => tfs.has(tf))
})

function getVerdictSlots(): VerdictSlot[] {
  if (selectedTimeframe.value) {
    const filtered = verdicts.value.filter(v => v.timeframe === selectedTimeframe.value)
    const map = new Map<string, StrategyVerdict>()
    for (const v of filtered) map.set(v.strategy, v)
    return allStrategies.map(s => ({ strategy: s, verdict: map.get(s) ?? null }))
  }
  // 預設：每策略取最高 confidence
  const best = new Map<string, StrategyVerdict>()
  for (const v of verdicts.value) {
    const prev = best.get(v.strategy)
    if (!prev || v.confidence > prev.confidence) best.set(v.strategy, v)
  }
  return allStrategies.map(s => ({ strategy: s, verdict: best.get(s) ?? null }))
}

function formatDateTime(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', { hour12: false })
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

function signalBadgeClass(signal: string) {
  if (signal === 'BUY') return 'text-(--color-success)'
  if (signal === 'SELL') return 'text-(--color-danger)'
  return 'text-(--color-text-muted)'
}

function computeRR(d: LLMDecision): string {
  const entry = d.entry_price ?? 0
  const sl = d.stop_loss ?? 0
  const tp = d.take_profit ?? 0
  if (!entry || !sl || !tp) return '—'
  const isShort = d.action === 'SHORT' || d.action === 'SELL'
  const slDist = isShort ? sl - entry : entry - sl
  const tpDist = isShort ? entry - tp : tp - entry
  if (slDist <= 0) return '—'
  return (tpDist / slDist).toFixed(2)
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
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="decision" class="fixed inset-0 z-50 flex justify-end" @click.self="emit('close')">
        <div class="absolute inset-0 bg-black/40" @click="emit('close')"></div>

        <div class="relative w-full max-w-lg bg-(--color-bg-primary) shadow-xl overflow-y-auto">
          <div class="p-5 flex flex-col gap-4">
            <!-- Header -->
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2.5">
                <div class="flex items-center gap-1.5 px-3 py-1 rounded-md text-sm font-bold" :class="actionBadgeClass(decision.action)">
                  <div class="w-2 h-2 rounded-full bg-current"></div>
                  {{ signalLabel(decision.action) }}
                </div>
                <span class="font-semibold text-lg text-(--color-text-primary)">{{ decision.symbol }}</span>
              </div>
              <button
                class="p-1.5 rounded-lg hover:bg-(--color-bg-secondary) text-(--color-text-secondary) transition-colors"
                @click="emit('close')"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>

            <!-- Time + market type -->
            <div class="flex items-center gap-2 text-sm text-(--color-text-muted)">
              <span>{{ formatDateTime(decision.created_at) }}</span>
              <span class="px-1.5 py-0.5 rounded text-[11px] font-medium bg-(--color-bg-secondary)">{{ (decision.market_type ?? 'spot') === 'futures' ? '合約' : '現貨' }}</span>
            </div>

            <!-- Rejected banner -->
            <div v-if="decision.executed === false" class="flex items-start gap-2 rounded-lg px-3 py-2.5 bg-(--color-warning-subtle) border border-(--color-warning)/20">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-warning)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="shrink-0 mt-0.5"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
              <div>
                <div class="text-xs font-bold text-(--color-warning)">風控攔截 — 未執行</div>
                <div v-if="decision.reject_reason" class="text-xs text-(--color-text-secondary) mt-0.5">{{ decision.reject_reason }}</div>
              </div>
            </div>

            <!-- Full reasoning -->
            <div class="text-sm text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ decision.reasoning.replace(/。/g, '。\n\n') }}</div>

            <!-- LLM 建議價位 -->
            <div v-if="decision.stop_loss || decision.take_profit" class="rounded-lg border border-(--color-border) overflow-hidden">
              <div class="px-3 py-1.5 text-xs font-medium text-(--color-text-secondary) bg-(--color-bg-secondary)">LLM 建議價位</div>
              <div class="grid grid-cols-3 divide-x divide-(--color-border)">
                <div class="px-3 py-2.5 text-center">
                  <div class="text-[11px] text-(--color-text-muted) mb-0.5">停損</div>
                  <div class="text-sm font-bold tabular-nums text-(--color-danger)">{{ decision.stop_loss ? decision.stop_loss.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—' }}</div>
                </div>
                <div class="px-3 py-2.5 text-center">
                  <div class="text-[11px] text-(--color-text-muted) mb-0.5">進場</div>
                  <div class="text-sm font-bold tabular-nums text-(--color-text-primary)">{{ decision.entry_price ? decision.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—' }}</div>
                </div>
                <div class="px-3 py-2.5 text-center">
                  <div class="text-[11px] text-(--color-text-muted) mb-0.5">停利</div>
                  <div class="text-sm font-bold tabular-nums text-(--color-success)">{{ decision.take_profit ? decision.take_profit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—' }}</div>
                </div>
              </div>
              <div v-if="decision.stop_loss && decision.take_profit && decision.entry_price" class="px-3 py-1.5 text-center text-xs text-(--color-text-muted) bg-(--color-bg-secondary) border-t border-(--color-border)">
                R:R {{ computeRR(decision) }}
              </div>
            </div>

            <!-- Strategy verdicts -->
            <div v-if="loading" class="text-sm text-(--color-text-muted)">載入策略...</div>
            <template v-else>
            <!-- Timeframe selector -->
            <div v-if="availableTimeframes.length" class="flex items-center gap-1.5">
              <button
                class="px-2.5 py-1 text-xs rounded-full transition-colors"
                :class="selectedTimeframe === null
                  ? 'bg-(--color-accent) text-white font-medium'
                  : 'bg-(--color-bg-secondary) text-(--color-text-muted) hover:text-(--color-text-secondary)'"
                @click="selectedTimeframe = null"
              >最佳</button>
              <button
                v-for="tf in availableTimeframes"
                :key="tf"
                class="px-2.5 py-1 text-xs rounded-full transition-colors"
                :class="selectedTimeframe === tf
                  ? 'bg-(--color-accent) text-white font-medium'
                  : 'bg-(--color-bg-secondary) text-(--color-text-muted) hover:text-(--color-text-secondary)'"
                @click="selectedTimeframe = tf"
              >{{ tf }}</button>
            </div>
            <div class="flex flex-col gap-2">
              <div
                v-for="slot in getVerdictSlots()"
                :key="slot.strategy"
                class="rounded-lg p-3"
                :style="slot.verdict ? verdictBgStyle(slot.verdict.signal, slot.verdict.confidence) : { background: 'var(--color-bg-secondary)' }"
              >
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-1.5">
                    <span class="text-sm font-medium text-(--color-text-primary)">{{ strategyLabel[slot.strategy] || slot.strategy }}</span>
                    <span v-if="slot.verdict?.timeframe" class="text-xs text-(--color-text-muted) opacity-60">{{ slot.verdict.timeframe }}</span>
                  </div>
                  <div v-if="slot.verdict" class="flex items-center gap-2">
                    <span class="text-sm font-bold" :class="signalBadgeClass(slot.verdict.signal)">{{ signalLabel(slot.verdict.signal) }}</span>
                    <span class="text-sm text-(--color-text-muted)">{{ (slot.verdict.confidence * 100).toFixed(0) }}%</span>
                  </div>
                  <span v-else class="text-sm text-(--color-text-muted) opacity-30">-</span>
                </div>
                <div v-if="slot.verdict?.reasoning" class="text-xs text-(--color-text-secondary) leading-relaxed">{{ slot.verdict.reasoning }}</div>
              </div>
            </div>
            </template>

            <!-- Related orders -->
            <div v-if="orders.length" class="flex flex-col gap-2">
              <div class="text-sm font-medium text-(--color-text-secondary)">關聯訂單</div>
              <div
                v-for="order in orders"
                :key="order.id"
                class="rounded-lg p-3 bg-(--color-bg-secondary)"
              >
                <div class="flex items-center justify-between mb-1">
                  <div class="flex items-center gap-1.5">
                    <span class="text-sm font-bold" :class="order.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'">
                      {{ order.side === 'buy' ? '買入' : '賣出' }}
                    </span>
                    <span v-if="order.market_type === 'futures' && order.position_side" class="text-[11px] px-1.5 py-0.5 rounded bg-(--color-bg-tertiary) text-(--color-text-muted)">
                      {{ order.position_side === 'long' ? '多' : '空' }}
                    </span>
                    <span v-if="order.market_type === 'futures' && order.leverage" class="text-[11px] px-1.5 py-0.5 rounded bg-(--color-bg-tertiary) text-(--color-text-muted)">
                      {{ order.leverage }}x
                    </span>
                    <span v-if="order.reduce_only" class="text-[11px] px-1.5 py-0.5 rounded bg-(--color-warning-subtle) text-(--color-warning)">
                      減倉
                    </span>
                  </div>
                  <span class="text-xs text-(--color-text-muted)">{{ order.status }}</span>
                </div>
                <div class="flex items-center gap-3 text-xs text-(--color-text-secondary)">
                  <span>數量 {{ order.filled || order.quantity }}</span>
                  <span>@ {{ order.price }}</span>
                  <span class="ml-auto">{{ formatDateTime(order.created_at) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.badge-buy { color: var(--color-success); }
.badge-sell { color: var(--color-danger); }
.badge-hold { color: var(--color-text-secondary); }

.drawer-enter-active,
.drawer-leave-active {
  transition: all 0.25s ease;
}
.drawer-enter-active > :last-child,
.drawer-leave-active > :last-child {
  transition: transform 0.25s ease;
}
.drawer-enter-from,
.drawer-leave-to {
  opacity: 0;
}
.drawer-enter-from > :last-child {
  transform: translateX(100%);
}
.drawer-leave-to > :last-child {
  transform: translateX(100%);
}
</style>
