<script setup lang="ts">
import { ref, watch } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import type { LLMDecision, StrategyVerdict } from '@/types'

const props = defineProps<{
  decision: LLMDecision | null
}>()

const emit = defineEmits<{
  close: []
}>()

const supabase = useSupabase()
const verdicts = ref<StrategyVerdict[]>([])
const loading = ref(false)

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

/** When decision changes, fetch verdicts by cycle_id */
watch(() => props.decision, async (d) => {
  verdicts.value = []
  if (!d?.cycle_id) return
  loading.value = true
  const { data } = await supabase
    .from('strategy_verdicts')
    .select('*')
    .eq('cycle_id', d.cycle_id)
    .eq('symbol', d.symbol)
  if (data) verdicts.value = data as StrategyVerdict[]
  loading.value = false
})

/** Per strategy, pick the verdict with the highest confidence */
function getVerdictSlots(): VerdictSlot[] {
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

            <!-- Strategy verdicts -->
            <div v-if="loading" class="text-sm text-(--color-text-muted)">載入策略...</div>
            <div v-else class="flex flex-col gap-2">
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
