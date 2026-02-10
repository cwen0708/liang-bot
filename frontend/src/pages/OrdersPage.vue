<script setup lang="ts">
import { ref } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { Order } from '@/types'

const supabase = useSupabase()
const filterSymbol = ref('')

const { rows: orders, loading } = useRealtimeTable<Order>('orders', { limit: 100 })

const filteredOrders = ref<Order[]>([])

// Simple filter watcher
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
</script>

<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-2xl font-bold">訂單</h2>
      <input
        v-model="filterSymbol"
        placeholder="依交易對篩選..."
        class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm w-48"
      />
    </div>

    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden">
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
  </div>
</template>
