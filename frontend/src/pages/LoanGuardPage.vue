<script setup lang="ts">
import { computed } from 'vue'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { LoanHealth } from '@/types'

const { rows: loanHistory, loading } = useRealtimeTable<LoanHealth>('loan_health', { limit: 100 })

// Group by collateral_coin to show latest per pair
const latestPerPair = computed(() => {
  const map = new Map<string, LoanHealth>()
  for (const l of loanHistory.value) {
    const key = `${l.collateral_coin}/${l.loan_coin}`
    if (!map.has(key)) map.set(key, l)
  }
  return [...map.values()]
})

function ltvColor(ltv: number) {
  if (ltv >= 0.75) return 'text-(--color-danger)'
  if (ltv >= 0.65) return 'text-(--color-warning)'
  if (ltv <= 0.4) return 'text-(--color-success)'
  return 'text-(--color-text-primary)'
}

function ltvBgColor(ltv: number) {
  if (ltv >= 0.75) return 'bg-(--color-danger)'
  if (ltv >= 0.65) return 'bg-(--color-warning)'
  if (ltv <= 0.4) return 'bg-(--color-success)'
  return 'bg-(--color-accent)'
}

function formatTime(ts: string) {
  return new Date(ts).toLocaleString('zh-TW')
}
</script>

<template>
  <div class="p-6 space-y-6">
    <h2 class="text-2xl font-bold">借貸監控</h2>

    <!-- Current LTV Gauges -->
    <div class="grid grid-cols-4 gap-4">
      <div v-for="l in latestPerPair" :key="`${l.collateral_coin}/${l.loan_coin}`"
           class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">
        <div class="text-xs text-(--color-text-secondary) uppercase">
          {{ l.collateral_coin }} / {{ l.loan_coin }}
        </div>
        <div class="text-3xl font-bold mt-2" :class="ltvColor(l.ltv)">
          {{ (l.ltv * 100).toFixed(1) }}%
        </div>
        <!-- LTV bar -->
        <div class="mt-3 h-2 bg-(--color-bg-secondary) rounded-full overflow-hidden">
          <div
            class="h-full rounded-full transition-all duration-500"
            :class="ltvBgColor(l.ltv)"
            :style="{ width: `${Math.min(l.ltv * 100, 100)}%` }"
          />
        </div>
        <div class="flex justify-between text-xs text-(--color-text-secondary) mt-1">
          <span>0%</span>
          <span>40% low</span>
          <span>65% target</span>
          <span>75% danger</span>
        </div>
        <div class="mt-3 text-xs text-(--color-text-secondary) space-y-1">
          <div>負債: {{ l.total_debt.toFixed(2) }} {{ l.loan_coin }}</div>
          <div>質押物: {{ l.collateral_amount.toFixed(8) }} {{ l.collateral_coin }}</div>
          <div v-if="l.action_taken !== 'none'" class="font-medium"
               :class="l.action_taken === 'protect' ? 'text-(--color-warning)' : 'text-(--color-success)'">
            操作: {{ l.action_taken === 'safe' ? '安全' : l.action_taken === 'protect' ? '保護' : l.action_taken === 'take_profit' ? '獲利了結' : l.action_taken }}
          </div>
        </div>
      </div>
    </div>

    <!-- History Table -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden">
      <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase px-4 py-3 bg-(--color-bg-secondary)">
        LTV 歷史
      </h3>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-(--color-text-secondary) text-left border-b border-(--color-border)">
            <th class="px-4 py-2">時間</th>
            <th class="px-4 py-2">交易對</th>
            <th class="px-4 py-2">LTV</th>
            <th class="px-4 py-2">負債</th>
            <th class="px-4 py-2">質押物</th>
            <th class="px-4 py-2">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="6" class="px-4 py-8 text-center text-(--color-text-secondary)">載入中...</td>
          </tr>
          <tr v-for="l in loanHistory" :key="l.id" class="border-t border-(--color-border) hover:bg-(--color-bg-secondary)/50">
            <td class="px-4 py-2 text-(--color-text-secondary)">{{ formatTime(l.created_at) }}</td>
            <td class="px-4 py-2">{{ l.collateral_coin }}/{{ l.loan_coin }}</td>
            <td class="px-4 py-2 font-medium" :class="ltvColor(l.ltv)">{{ (l.ltv * 100).toFixed(1) }}%</td>
            <td class="px-4 py-2">{{ l.total_debt.toFixed(2) }}</td>
            <td class="px-4 py-2">{{ l.collateral_amount.toFixed(8) }}</td>
            <td class="px-4 py-2">
              <span v-if="l.action_taken !== 'none'"
                    class="px-2 py-0.5 rounded text-xs"
                    :class="{
                      'bg-(--color-warning)/20 text-(--color-warning)': l.action_taken === 'protect',
                      'bg-(--color-success)/20 text-(--color-success)': l.action_taken === 'take_profit',
                    }">
                {{ l.action_taken === 'safe' ? '安全' : l.action_taken === 'protect' ? '保護' : l.action_taken === 'take_profit' ? '獲利了結' : l.action_taken }}
              </span>
              <span v-else class="text-(--color-text-secondary)">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
