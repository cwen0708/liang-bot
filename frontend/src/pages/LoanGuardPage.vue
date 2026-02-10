<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { LoanHealth } from '@/types'

const { rows: loanHistory, loading } = useRealtimeTable<LoanHealth>('loan_health', { limit: 100 })

const selectedPair = ref('')

const latestPerPair = computed(() => {
  const map = new Map<string, LoanHealth>()
  for (const l of loanHistory.value) {
    const key = `${l.collateral_coin}/${l.loan_coin}`
    if (!map.has(key)) map.set(key, l)
  }
  return [...map.values()]
})

const filteredHistory = computed(() => {
  if (!selectedPair.value) return loanHistory.value
  return loanHistory.value.filter(
    (l) => `${l.collateral_coin}/${l.loan_coin}` === selectedPair.value,
  )
})

function selectPair(pair: string) {
  selectedPair.value = selectedPair.value === pair ? '' : pair
}

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

function formatTimeShort(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-xl md:text-2xl font-bold">借貸監控</h2>

    <!-- Current LTV Gauges (clickable to filter) -->
    <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <div v-for="l in latestPerPair" :key="`${l.collateral_coin}/${l.loan_coin}`"
           class="bg-(--color-bg-card) border-2 rounded-xl p-3 md:p-4 cursor-pointer transition-all"
           :class="selectedPair === `${l.collateral_coin}/${l.loan_coin}`
             ? 'border-(--color-accent) ring-1 ring-(--color-accent)/30'
             : 'border-(--color-border) hover:border-(--color-text-secondary)'"
           @click="selectPair(`${l.collateral_coin}/${l.loan_coin}`)">
        <div class="flex justify-between items-center">
          <div class="text-xs text-(--color-text-secondary) uppercase">
            {{ l.collateral_coin }} / {{ l.loan_coin }}
          </div>
          <svg v-if="selectedPair === `${l.collateral_coin}/${l.loan_coin}`"
               class="w-4 h-4 text-(--color-accent)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </div>
        <div class="text-2xl md:text-3xl font-bold mt-2" :class="ltvColor(l.ltv)">
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
        <div class="flex justify-between text-[10px] text-(--color-text-secondary) mt-1">
          <span>0%</span>
          <span>40%</span>
          <span>65%</span>
          <span>75%</span>
        </div>
        <div class="mt-2 text-xs text-(--color-text-secondary) space-y-0.5">
          <div>負債: {{ l.total_debt.toFixed(2) }} {{ l.loan_coin }}</div>
          <div>質押: {{ l.collateral_amount.toFixed(4) }} {{ l.collateral_coin }}</div>
          <div v-if="l.action_taken !== 'none'" class="font-medium"
               :class="l.action_taken === 'protect' ? 'text-(--color-warning)' : 'text-(--color-success)'">
            {{ l.action_taken === 'protect' ? '保護' : l.action_taken === 'take_profit' ? '獲利了結' : l.action_taken }}
          </div>
        </div>
      </div>
    </div>

    <!-- Filter indicator -->
    <div v-if="selectedPair" class="flex items-center gap-2 text-sm">
      <span class="text-(--color-text-secondary)">篩選:</span>
      <span class="px-2 py-0.5 bg-(--color-accent)/20 text-(--color-accent) rounded text-xs font-medium">
        {{ selectedPair }}
      </span>
      <button @click="selectedPair = ''" class="text-xs text-(--color-text-secondary) hover:text-(--color-text-primary)">
        清除
      </button>
    </div>

    <!-- History -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden">
      <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase px-4 py-3 bg-(--color-bg-secondary)">
        LTV 歷史
        <span v-if="selectedPair" class="text-(--color-accent) normal-case"> - {{ selectedPair }}</span>
      </h3>

      <!-- Desktop table -->
      <div class="hidden md:block">
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
            <tr v-else-if="!filteredHistory.length">
              <td colspan="6" class="px-4 py-8 text-center text-(--color-text-secondary)">無記錄</td>
            </tr>
            <tr v-for="l in filteredHistory" :key="l.id" class="border-t border-(--color-border) hover:bg-(--color-bg-secondary)/50">
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
                  {{ l.action_taken === 'protect' ? '保護' : l.action_taken === 'take_profit' ? '獲利了結' : l.action_taken }}
                </span>
                <span v-else class="text-(--color-text-secondary)">-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- Mobile cards -->
      <div class="md:hidden divide-y divide-(--color-border)">
        <div v-if="loading" class="p-8 text-center text-sm text-(--color-text-secondary)">載入中...</div>
        <div v-else-if="!filteredHistory.length" class="p-8 text-center text-sm text-(--color-text-secondary)">無記錄</div>
        <div v-for="l in filteredHistory" :key="l.id" class="p-3">
          <div class="flex justify-between items-center">
            <span class="text-sm font-medium">{{ l.collateral_coin }}/{{ l.loan_coin }}</span>
            <span class="font-bold" :class="ltvColor(l.ltv)">{{ (l.ltv * 100).toFixed(1) }}%</span>
          </div>
          <div class="flex justify-between items-center mt-1 text-xs text-(--color-text-secondary)">
            <span>{{ formatTimeShort(l.created_at) }}</span>
            <span v-if="l.action_taken !== 'none'"
                  class="px-2 py-0.5 rounded"
                  :class="{
                    'bg-(--color-warning)/20 text-(--color-warning)': l.action_taken === 'protect',
                    'bg-(--color-success)/20 text-(--color-success)': l.action_taken === 'take_profit',
                  }">
              {{ l.action_taken === 'protect' ? '保護' : '獲利了結' }}
            </span>
            <span v-else>-</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
