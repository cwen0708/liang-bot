<script setup lang="ts">
import { useBotStore } from '@/stores/bot'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { Order, LoanHealth, LLMDecision } from '@/types'

const bot = useBotStore()
const { rows: recentOrders } = useRealtimeTable<Order>('orders', { limit: 5 })
const { rows: recentDecisions } = useRealtimeTable<LLMDecision>('llm_decisions', { limit: 5 })

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function loanNetValue(l: LoanHealth) {
  const price = bot.latestPrices[l.collateral_coin + '/USDT'] ?? 0
  return l.collateral_amount * price - l.total_debt
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-2xl md:text-3xl font-bold">總覽</h2>

    <!-- Summary Cards -->
    <div class="grid grid-cols-3 gap-3 md:gap-4">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary) uppercase">總資產</div>
        <div class="text-xl md:text-2xl font-bold mt-1">
          <template v-if="bot.totalAssets !== null">
            ${{ bot.totalAssets.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
          </template>
          <span v-else class="text-(--color-text-muted)">--</span>
        </div>
        <div class="text-sm text-(--color-text-secondary) mt-1">
          {{ bot.isOnline ? '運行中' : '離線' }} · 第 {{ bot.status?.cycle_num ?? '-' }} 輪
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary) uppercase">現貨</div>
        <div class="text-xl md:text-2xl font-bold mt-1">
          ${{ bot.totalUsdt.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
        </div>
        <div class="text-sm text-(--color-text-secondary) mt-1">
          {{ bot.balances.length }} 幣種
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <div class="text-sm text-(--color-text-secondary) uppercase">借貸</div>
        <div class="text-xl md:text-2xl font-bold mt-1" :class="{
          'text-(--color-success)': bot.netLoanValue !== null && bot.netLoanValue > 0,
          'text-(--color-danger)': bot.netLoanValue !== null && bot.netLoanValue < 0,
        }">
          <template v-if="bot.netLoanValue !== null">
            ${{ bot.netLoanValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}
          </template>
          <span v-else class="text-(--color-text-muted)">--</span>
        </div>
        <div class="text-sm text-(--color-text-secondary) mt-1">
          {{ bot.loans.length }} 筆借貸
        </div>
      </div>
    </div>

    <!-- 帳戶餘額 (現貨) -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">帳戶餘額</h3>
      <div v-if="!bot.balances.length" class="text-base text-(--color-text-secondary)">尚無餘額資料</div>
      <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="b in bot.balances" :key="b.currency"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="flex justify-between items-center">
            <span class="text-base font-bold">{{ b.currency }}</span>
            <span class="text-sm text-(--color-text-secondary)">${{ b.usdt_value.toLocaleString(undefined, { maximumFractionDigits: 2 }) }}</span>
          </div>
          <div class="text-sm text-(--color-text-secondary) mt-1 font-mono">{{ b.free.toFixed(8) }}</div>
        </div>
      </div>
    </div>

    <!-- 借貸餘額 -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">借貸餘額</h3>
      <div v-if="!bot.loans.length" class="text-base text-(--color-text-secondary)">尚無借貸資料</div>
      <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div v-for="l in bot.loans" :key="`${l.collateral_coin}/${l.loan_coin}`"
             class="bg-(--color-bg-secondary) rounded-lg p-3">
          <div class="flex justify-between items-center">
            <span class="text-base font-bold">{{ l.collateral_coin }}/{{ l.loan_coin }}</span>
            <span class="text-sm font-medium" :class="{
              'text-(--color-danger)': l.ltv >= 0.75,
              'text-(--color-warning)': l.ltv >= 0.70 && l.ltv < 0.75,
              'text-(--color-success)': l.ltv < 0.4,
              'text-(--color-text-secondary)': l.ltv >= 0.4 && l.ltv < 0.70,
            }">LTV {{ (l.ltv * 100).toFixed(1) }}%</span>
          </div>
          <div class="text-sm text-(--color-text-secondary) mt-1">
            質押 {{ l.collateral_amount.toFixed(4) }} · 負債 {{ l.total_debt.toFixed(2) }}
          </div>
          <div class="flex justify-between items-center mt-1">
            <span class="text-sm text-(--color-text-secondary)">
              淨值 ${{ loanNetValue(l).toFixed(2) }}
            </span>
            <span v-if="l.action_taken !== 'none'" class="text-sm font-medium"
                  :class="l.action_taken === 'protect' ? 'text-(--color-warning)' : 'text-(--color-success)'">
              {{ l.action_taken === 'protect' ? '保護' : '獲利了結' }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- Orders & AI -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 md:gap-6">
      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">最近訂單</h3>
        <div v-if="!recentOrders.length" class="text-base text-(--color-text-secondary)">尚無訂單</div>
        <div v-for="o in recentOrders" :key="o.id" class="flex justify-between items-center py-2 border-b border-(--color-border) last:border-0 text-base">
          <div>
            <span :class="o.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'" class="font-medium uppercase">
              {{ o.side === 'buy' ? '買入' : '賣出' }}
            </span>
            <span class="ml-2 text-(--color-text-secondary)">{{ o.symbol }}</span>
          </div>
          <div class="text-right">
            <div>{{ o.quantity.toFixed(6) }} @ ${{ o.price?.toFixed(2) }}</div>
            <div class="text-sm text-(--color-text-secondary)">{{ formatTime(o.created_at) }}</div>
          </div>
        </div>
      </div>

      <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
        <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">AI 決策</h3>
        <div v-if="!recentDecisions.length" class="text-base text-(--color-text-secondary)">尚無決策</div>
        <div v-for="d in recentDecisions" :key="d.id" class="py-2 border-b border-(--color-border) last:border-0 text-base">
          <div class="flex justify-between">
            <span :class="{
              'text-(--color-success)': d.action === 'BUY',
              'text-(--color-danger)': d.action === 'SELL',
              'text-(--color-text-secondary)': d.action === 'HOLD',
            }" class="font-medium">
              {{ d.action === 'BUY' ? '買入' : d.action === 'SELL' ? '賣出' : '觀望' }}
            </span>
            <span class="text-(--color-text-secondary) text-sm">{{ d.symbol }} &middot; {{ (d.confidence * 100).toFixed(0) }}%</span>
          </div>
          <div class="text-sm text-(--color-text-secondary) mt-1 line-clamp-2">{{ d.reasoning }}</div>
        </div>
      </div>
    </div>

  </div>
</template>
