<script setup lang="ts">
import { computed } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { Order, LoanHealth, LLMDecision } from '@/types'

const bot = useBotStore()
const { rows: recentOrders } = useRealtimeTable<Order>('orders', { limit: 5 })
const { rows: recentLoans } = useRealtimeTable<LoanHealth>('loan_health', { limit: 5 })
const { rows: recentDecisions } = useRealtimeTable<LLMDecision>('llm_decisions', { limit: 5 })

const totalPnl = computed(() =>
  bot.positions.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0),
)

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-xl md:text-2xl font-bold">總覽</h2>

    <!-- Status Cards -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 md:gap-4">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <div class="text-xs text-(--color-text-secondary) uppercase">狀態</div>
        <div class="text-lg md:text-xl font-bold mt-1" :class="bot.isOnline ? 'text-(--color-success)' : 'text-(--color-danger)'">
          {{ bot.isOnline ? '運行中' : '離線' }}
        </div>
        <div class="text-xs text-(--color-text-secondary) mt-1">
          第 {{ bot.status?.cycle_num ?? '-' }} 輪
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <div class="text-xs text-(--color-text-secondary) uppercase">持倉</div>
        <div class="text-lg md:text-xl font-bold mt-1">{{ bot.positions.length }}</div>
        <div class="text-xs mt-1" :class="totalPnl >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'">
          {{ totalPnl >= 0 ? '+' : '' }}{{ totalPnl.toFixed(2) }} USDT
        </div>
      </div>

      <div v-for="(price, symbol) in bot.latestPrices" :key="symbol"
           class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <div class="text-xs text-(--color-text-secondary) uppercase">{{ symbol }}</div>
        <div class="text-lg md:text-xl font-bold mt-1">${{ price.toLocaleString() }}</div>
      </div>
    </div>

    <!-- Orders & AI -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">最近訂單</h3>
        <div v-if="!recentOrders.length" class="text-sm text-(--color-text-secondary)">尚無訂單</div>
        <div v-for="o in recentOrders" :key="o.id" class="flex justify-between items-center py-2 border-b border-(--color-border) last:border-0 text-sm">
          <div>
            <span :class="o.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'" class="font-medium uppercase">
              {{ o.side === 'buy' ? '買入' : '賣出' }}
            </span>
            <span class="ml-2 text-(--color-text-secondary)">{{ o.symbol }}</span>
          </div>
          <div class="text-right">
            <div>{{ o.quantity.toFixed(6) }} @ ${{ o.price?.toFixed(2) }}</div>
            <div class="text-xs text-(--color-text-secondary)">{{ formatTime(o.created_at) }}</div>
          </div>
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
        <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">AI 決策</h3>
        <div v-if="!recentDecisions.length" class="text-sm text-(--color-text-secondary)">尚無決策</div>
        <div v-for="d in recentDecisions" :key="d.id" class="py-2 border-b border-(--color-border) last:border-0 text-sm">
          <div class="flex justify-between">
            <span :class="{
              'text-(--color-success)': d.action === 'BUY',
              'text-(--color-danger)': d.action === 'SELL',
              'text-(--color-text-secondary)': d.action === 'HOLD',
            }" class="font-medium">
              {{ d.action === 'BUY' ? '買入' : d.action === 'SELL' ? '賣出' : '持有' }}
            </span>
            <span class="text-(--color-text-secondary) text-xs">{{ d.symbol }} &middot; {{ (d.confidence * 100).toFixed(0) }}%</span>
          </div>
          <div class="text-xs text-(--color-text-secondary) mt-1 line-clamp-2">{{ d.reasoning }}</div>
        </div>
      </div>
    </div>

    <!-- Loan Health -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4">
      <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">借貸健康度</h3>
      <div v-if="!recentLoans.length" class="text-sm text-(--color-text-secondary)">尚無借貸資料</div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="l in recentLoans" :key="l.id"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="text-xs text-(--color-text-secondary)">{{ l.collateral_coin }} / {{ l.loan_coin }}</div>
          <div class="text-lg font-bold mt-1" :class="{
            'text-(--color-danger)': l.ltv >= 0.75,
            'text-(--color-warning)': l.ltv >= 0.65,
            'text-(--color-success)': l.ltv < 0.4,
          }">
            {{ (l.ltv * 100).toFixed(1) }}%
          </div>
          <div class="text-xs text-(--color-text-secondary) mt-1">
            {{ l.action_taken === 'protect' ? '保護' : l.action_taken === 'take_profit' ? '獲利了結' : '安全' }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
