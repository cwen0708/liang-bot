<script setup lang="ts">
import { useRealtimeTable } from '@/composables/useRealtime'
import type { StrategyVerdict, LLMDecision } from '@/types'

const { rows: verdicts, loading: vLoading } = useRealtimeTable<StrategyVerdict>('strategy_verdicts', { limit: 50 })
const { rows: decisions, loading: dLoading } = useRealtimeTable<LLMDecision>('llm_decisions', { limit: 30 })

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function signalColor(signal: string) {
  if (signal === 'BUY') return 'text-(--color-success)'
  if (signal === 'SELL') return 'text-(--color-danger)'
  return 'text-(--color-text-secondary)'
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-xl md:text-2xl font-bold">策略</h2>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
      <!-- Strategy Verdicts -->
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">策略結論</h3>
        <div v-if="vLoading" class="text-sm text-(--color-text-secondary)">載入中...</div>
        <div class="space-y-2 max-h-[60vh] md:max-h-[600px] overflow-auto">
          <div v-for="v in verdicts" :key="v.id"
               class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm">
            <div class="flex justify-between items-start gap-2">
              <div class="min-w-0">
                <span :class="signalColor(v.signal)" class="font-bold">{{ v.signal === 'BUY' ? '買入' : v.signal === 'SELL' ? '賣出' : v.signal === 'HOLD' ? '持有' : v.signal }}</span>
                <span class="text-(--color-text-secondary) ml-2">{{ v.symbol }}</span>
              </div>
              <div class="text-xs text-(--color-text-secondary) shrink-0 text-right">
                <div>{{ v.strategy }}</div>
                <div>{{ (v.confidence * 100).toFixed(0) }}% &middot; {{ formatTime(v.created_at) }}</div>
              </div>
            </div>
            <div class="text-xs text-(--color-text-secondary) mt-1 line-clamp-2">{{ v.reasoning }}</div>
          </div>
        </div>
      </div>

      <!-- LLM Decisions -->
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">AI 決策</h3>
        <div v-if="dLoading" class="text-sm text-(--color-text-secondary)">載入中...</div>
        <div class="space-y-2 max-h-[60vh] md:max-h-[600px] overflow-auto">
          <div v-for="d in decisions" :key="d.id"
               class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm">
            <div class="flex justify-between items-start gap-2">
              <div class="min-w-0">
                <span :class="signalColor(d.action)" class="font-bold">{{ d.action === 'BUY' ? '買入' : d.action === 'SELL' ? '賣出' : d.action === 'HOLD' ? '持有' : d.action }}</span>
                <span class="text-(--color-text-secondary) ml-2">{{ d.symbol }}</span>
              </div>
              <div class="text-xs text-(--color-text-secondary) shrink-0">
                {{ (d.confidence * 100).toFixed(0) }}% &middot; {{ formatTime(d.created_at) }}
              </div>
            </div>
            <div class="text-xs text-(--color-text-secondary) mt-2">{{ d.reasoning }}</div>
            <div class="text-xs text-(--color-text-secondary)/60 mt-1">Model: {{ d.model }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
