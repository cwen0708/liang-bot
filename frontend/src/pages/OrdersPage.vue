<script setup lang="ts">
import { ref } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { Order } from '@/types'

const supabase = useSupabase()
const filterSymbol = ref('')

const { rows: orders, loading } = useRealtimeTable<Order>('orders', { limit: 100 })

const filteredOrders = ref<Order[]>([])

import { watchEffect } from 'vue'
watchEffect(() => {
  if (!filterSymbol.value) {
    filteredOrders.value = orders.value
  } else {
    filteredOrders.value = orders.value.filter((o) => o.symbol === filterSymbol.value)
  }
})

function formatDate(ts: string) {
  return new Date(ts).toLocaleString('zh-TW')
}

function formatDateShort(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
      <h2 class="text-xl md:text-2xl font-bold">訂單</h2>
      <input
        v-model="filterSymbol"
        placeholder="依交易對篩選..."
        class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm w-full sm:w-48"
      />
    </div>

    <!-- Desktop table -->
    <div class="hidden md:block bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden">
      <table class="w-full text-sm">
        <thead class="bg-(--color-bg-secondary)">
          <tr class="text-(--color-text-secondary) text-left">
            <th class="px-4 py-3">時間</th>
            <th class="px-4 py-3">交易對</th>
            <th class="px-4 py-3">方向</th>
            <th class="px-4 py-3">類型</th>
            <th class="px-4 py-3">數量</th>
            <th class="px-4 py-3">價格</th>
            <th class="px-4 py-3">成交量</th>
            <th class="px-4 py-3">狀態</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="8" class="px-4 py-8 text-center text-(--color-text-secondary)">載入中...</td>
          </tr>
          <tr v-else-if="!filteredOrders.length">
            <td colspan="8" class="px-4 py-8 text-center text-(--color-text-secondary)">尚無訂單</td>
          </tr>
          <tr v-for="o in filteredOrders" :key="o.id" class="border-t border-(--color-border) hover:bg-(--color-bg-secondary)/50">
            <td class="px-4 py-2 text-(--color-text-secondary)">{{ formatDate(o.created_at) }}</td>
            <td class="px-4 py-2 font-medium">{{ o.symbol }}</td>
            <td class="px-4 py-2">
              <span :class="o.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'" class="font-medium uppercase">
                {{ o.side }}
              </span>
            </td>
            <td class="px-4 py-2 text-(--color-text-secondary)">{{ o.order_type }}</td>
            <td class="px-4 py-2">{{ o.quantity.toFixed(6) }}</td>
            <td class="px-4 py-2">${{ o.price?.toFixed(2) ?? '-' }}</td>
            <td class="px-4 py-2">{{ o.filled?.toFixed(6) ?? '-' }}</td>
            <td class="px-4 py-2">
              <span class="px-2 py-0.5 rounded text-xs" :class="{
                'bg-(--color-success)/20 text-(--color-success)': o.status === 'filled',
                'bg-(--color-warning)/20 text-(--color-warning)': o.status === 'partial',
                'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': o.status === 'cancelled',
              }">
                {{ o.status }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Mobile cards -->
    <div class="md:hidden space-y-3">
      <div v-if="loading" class="text-center text-sm text-(--color-text-secondary) py-8">載入中...</div>
      <div v-else-if="!filteredOrders.length" class="text-center text-sm text-(--color-text-secondary) py-8">尚無訂單</div>
      <div v-for="o in filteredOrders" :key="o.id"
           class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3">
        <div class="flex justify-between items-center mb-2">
          <div class="flex items-center gap-2">
            <span :class="o.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'" class="font-bold uppercase text-sm">
              {{ o.side === 'buy' ? '買入' : '賣出' }}
            </span>
            <span class="font-medium text-sm">{{ o.symbol }}</span>
          </div>
          <span class="px-2 py-0.5 rounded text-xs" :class="{
            'bg-(--color-success)/20 text-(--color-success)': o.status === 'filled',
            'bg-(--color-warning)/20 text-(--color-warning)': o.status === 'partial',
            'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': o.status === 'cancelled',
          }">
            {{ o.status }}
          </span>
        </div>
        <div class="grid grid-cols-2 gap-1 text-xs text-(--color-text-secondary)">
          <div>數量: {{ o.quantity.toFixed(6) }}</div>
          <div>價格: ${{ o.price?.toFixed(2) ?? '-' }}</div>
          <div>成交: {{ o.filled?.toFixed(6) ?? '-' }}</div>
          <div>{{ formatDateShort(o.created_at) }}</div>
        </div>
      </div>
    </div>
  </div>
</template>
