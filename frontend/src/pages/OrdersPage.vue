<script setup lang="ts">
import { ref, computed } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { Order, LLMDecision } from '@/types'

const bot = useBotStore()
const { rows: orders, loading } = useRealtimeTable<Order>('orders', { limit: 100 })
const { rows: decisions } = useRealtimeTable<LLMDecision>('llm_decisions', { limit: 200 })

const filterMode = computed(() => bot.globalMode)
const filterSymbol = ref('')
const filterStatus = ref('')
const expandedOrderId = ref<number | null>(null)

const availableSymbols = computed(() => {
  const set = new Set<string>()
  for (const p of bot.spotPositions) {
    if ((p.mode ?? 'live') === filterMode.value) set.add(p.symbol)
  }
  for (const o of orders.value) {
    if ((o.mode ?? 'live') === filterMode.value && (o.market_type ?? 'spot') === 'spot') set.add(o.symbol)
  }
  return [...set].sort()
})

// --- Positions filtered by mode (spot only) ---
const filteredPositions = computed(() => {
  return bot.spotPositions.filter(p => (p.mode ?? 'live') === filterMode.value)
})

// --- Orders filtered by mode + spot only + symbol + status ---
const filteredOrders = computed(() => {
  let result = [...orders.value]
    .filter(o => (o.mode ?? 'live') === filterMode.value && (o.market_type ?? 'spot') === 'spot')
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  if (filterSymbol.value) {
    result = result.filter(o => o.symbol === filterSymbol.value)
  }
  if (filterStatus.value === 'filled') {
    result = result.filter(o => o.status === 'filled' || o.status === 'closed')
  } else if (filterStatus.value === 'cancelled') {
    result = result.filter(o => o.status === 'cancelled')
  }
  return result
})

// --- AI Decision lookup by cycle_id + symbol ---
const decisionMap = computed(() => {
  const map = new Map<string, LLMDecision>()
  for (const d of decisions.value) {
    if (d.cycle_id) map.set(`${d.cycle_id}:${d.symbol}`, d)
  }
  return map
})

function getDecision(order: Order): LLMDecision | undefined {
  if (!order.cycle_id) return undefined
  return decisionMap.value.get(`${order.cycle_id}:${order.symbol}`)
}

function toggleExpand(orderId: number) {
  expandedOrderId.value = expandedOrderId.value === orderId ? null : orderId
}

function selectPosition(symbol: string) {
  filterSymbol.value = filterSymbol.value === symbol ? '' : symbol
}

function statusLabel(status: string): string {
  if (status === 'filled' || status === 'closed') return '已成交'
  if (status === 'partial') return '部分成交'
  if (status === 'cancelled') return '已取消'
  return status
}

function actionLabel(action: string): string {
  if (action === 'BUY') return '買入'
  if (action === 'SELL') return '賣出'
  return '觀望'
}

function formatDate(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', { hour12: false })
}

function formatDateShort(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function livePrice(symbol: string, fallback: number): number {
  return bot.latestPrices[symbol] ?? fallback
}

function calcPnl(pos: { symbol: string; entry_price: number; quantity: number; current_price: number; side?: string }) {
  const price = livePrice(pos.symbol, pos.current_price)
  const direction = pos.side === 'short' ? -1 : 1
  const pnl = (price - pos.entry_price) * pos.quantity * direction
  const pnlPct = pos.entry_price > 0 ? ((price - pos.entry_price) / pos.entry_price) * 100 * direction : 0
  return { pnl, pnlPct }
}
</script>

<template>
  <div class="p-4 md:p-6 flex flex-col gap-4 md:gap-6 md:h-[calc(100vh)] md:overflow-hidden">
    <!-- Header + Status filters -->
    <div class="flex items-center justify-between gap-2 shrink-0">
      <h2 class="text-2xl md:text-3xl font-bold">現貨</h2>
      <div class="inline-flex rounded-lg bg-(--color-bg-secondary) p-0.5">
        <button
          class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
          :class="!filterStatus
            ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
            : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
          @click="filterStatus = ''"
        >全部</button>
        <button
          class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
          :class="filterStatus === 'filled'
            ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
            : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
          @click="filterStatus = 'filled'"
        >已成交</button>
        <button
          class="px-3 py-1 rounded-md text-sm font-medium transition-colors"
          :class="filterStatus === 'cancelled'
            ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
            : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
          @click="filterStatus = 'cancelled'"
        >已取消</button>
      </div>
    </div>

    <!-- Scrollable content -->
    <div class="flex flex-col gap-4 md:gap-5 min-h-0 md:flex-1 md:overflow-hidden">
      <!-- ===== Positions: horizontal scroll, 4 cols ===== -->
      <section class="shrink-0">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-lg font-semibold text-(--color-text-primary)">持倉</h3>
          <button
            v-if="filterSymbol"
            class="text-xs text-(--color-accent) hover:underline"
            @click="filterSymbol = ''"
          >清除篩選</button>
        </div>
        <div v-if="!filteredPositions.length" class="text-sm text-(--color-text-secondary) bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">
          目前無持倉
        </div>
        <div v-else class="overflow-x-auto">
          <div class="inline-grid grid-cols-4 gap-2" style="min-width: max-content">
            <div
              v-for="pos in filteredPositions"
              :key="pos.id"
              class="w-[220px] bg-(--color-bg-card) border rounded-lg p-3 cursor-pointer transition-all"
              :class="filterSymbol === pos.symbol
                ? 'border-(--color-accent) ring-1 ring-(--color-accent)/30'
                : 'border-(--color-border) hover:border-(--color-accent)/50'"
              @click="selectPosition(pos.symbol)"
            >
              <!-- Header: symbol + PnL -->
              <div class="flex justify-between items-center mb-2">
                <span class="font-bold text-sm">{{ pos.symbol.replace('/USDT', '') }}</span>
                <div class="text-right">
                  <span
                    class="font-bold text-sm block"
                    :class="calcPnl(pos).pnl >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'"
                  >{{ calcPnl(pos).pnl >= 0 ? '+' : '' }}{{ calcPnl(pos).pnl.toFixed(2) }}</span>
                  <span
                    class="text-xs"
                    :class="calcPnl(pos).pnlPct >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'"
                  >{{ calcPnl(pos).pnlPct >= 0 ? '+' : '' }}{{ calcPnl(pos).pnlPct.toFixed(2) }}%</span>
                </div>
              </div>
              <!-- Compact info -->
              <div class="text-xs text-(--color-text-secondary) space-y-0.5">
                <div class="flex justify-between">
                  <span>數量</span>
                  <span class="text-(--color-text-primary) font-medium">{{ pos.quantity.toFixed(6) }}</span>
                </div>
                <div class="flex justify-between">
                  <span>入場</span>
                  <span class="text-(--color-text-primary) font-medium">${{ pos.entry_price.toFixed(2) }}</span>
                </div>
                <div class="flex justify-between">
                  <span>現價</span>
                  <span class="text-(--color-text-primary) font-medium">${{ livePrice(pos.symbol, pos.current_price).toFixed(2) }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- ===== Orders Section ===== -->
      <section class="flex flex-col min-h-0 md:flex-1">
        <h3 class="text-lg font-semibold mb-2 text-(--color-text-primary)">
          訂單紀錄
          <span v-if="filterSymbol" class="text-sm font-normal text-(--color-accent) ml-2">{{ filterSymbol }}</span>
        </h3>
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 flex flex-col min-h-0 md:flex-1 shadow-sm dark:shadow-none">
          <div v-if="loading" class="text-base text-(--color-text-secondary)">載入中...</div>
          <div v-else-if="!filteredOrders.length" class="text-base text-(--color-text-secondary)">無訂單紀錄</div>

          <!-- Desktop table -->
          <div v-if="!loading && filteredOrders.length" class="hidden md:block overflow-auto flex-1 min-h-0 table-responsive">
            <table class="w-full text-base">
              <thead>
                <tr class="text-(--color-text-secondary) text-left text-sm">
                  <th class="pb-2">狀態</th>
                  <th class="pb-2">交易對</th>
                  <th class="pb-2">方向</th>
                  <th class="pb-2">數量</th>
                  <th class="pb-2">價格</th>
                  <th class="pb-2">成交量</th>
                  <th class="pb-2">AI</th>
                  <th class="pb-2">時間</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="order in filteredOrders" :key="order.id">
                  <tr
                    class="border-t border-(--color-border) cursor-pointer transition-colors"
                    :class="expandedOrderId === order.id ? 'bg-(--color-bg-secondary)/70' : 'hover:bg-(--color-bg-secondary)/50'"
                    @click="toggleExpand(order.id)"
                  >
                    <td class="py-2">
                      <span class="px-2 py-0.5 rounded text-xs" :class="{
                        'bg-(--color-success)/20 text-(--color-success)': order.status === 'filled' || order.status === 'closed',
                        'bg-(--color-warning)/20 text-(--color-warning)': order.status === 'partial',
                        'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': order.status === 'cancelled',
                      }">{{ statusLabel(order.status) }}</span>
                    </td>
                    <td class="py-2 font-medium">{{ order.symbol }}</td>
                    <td class="py-2">
                      <span
                        :class="order.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'"
                        class="font-medium"
                      >{{ order.side === 'buy' ? '買入' : '賣出' }}</span>
                    </td>
                    <td class="py-2">{{ order.quantity.toFixed(6) }}</td>
                    <td class="py-2">${{ order.price?.toFixed(2) ?? '-' }}</td>
                    <td class="py-2 text-(--color-text-secondary)">{{ order.filled?.toFixed(6) ?? '-' }}</td>
                    <td class="py-2">
                      <span v-if="getDecision(order)" class="px-2 py-0.5 rounded text-xs font-medium" :class="{
                        'bg-(--color-success)/20 text-(--color-success)': getDecision(order)!.action === 'BUY',
                        'bg-(--color-danger)/20 text-(--color-danger)': getDecision(order)!.action === 'SELL',
                        'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': getDecision(order)!.action === 'HOLD',
                      }">{{ actionLabel(getDecision(order)!.action) }} {{ (getDecision(order)!.confidence * 100).toFixed(0) }}%</span>
                      <span v-else class="text-xs text-(--color-text-secondary)">-</span>
                    </td>
                    <td class="py-2 text-(--color-text-secondary) text-sm">{{ formatDate(order.created_at) }}</td>
                  </tr>
                  <!-- Expanded: AI Decision detail -->
                  <tr v-if="expandedOrderId === order.id && getDecision(order)">
                    <td colspan="8" class="pb-3 pt-0 px-2">
                      <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm">
                        <div class="flex items-center gap-2 mb-1.5">
                          <span class="font-medium text-(--color-text-primary)">AI 決策</span>
                          <span class="px-2 py-0.5 rounded text-xs font-medium" :class="{
                            'bg-(--color-success)/20 text-(--color-success)': getDecision(order)!.action === 'BUY',
                            'bg-(--color-danger)/20 text-(--color-danger)': getDecision(order)!.action === 'SELL',
                            'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': getDecision(order)!.action === 'HOLD',
                          }">{{ actionLabel(getDecision(order)!.action) }}</span>
                          <span class="text-(--color-text-secondary)">信心 {{ (getDecision(order)!.confidence * 100).toFixed(0) }}%</span>
                          <span class="text-(--color-text-secondary) ml-auto text-xs">{{ getDecision(order)!.model }}</span>
                        </div>
                        <p class="text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ getDecision(order)!.reasoning }}</p>
                        <div class="mt-1.5 text-xs text-(--color-text-secondary)">cycle: {{ order.cycle_id }}</div>
                      </div>
                    </td>
                  </tr>
                  <tr v-else-if="expandedOrderId === order.id && !getDecision(order)">
                    <td colspan="8" class="pb-3 pt-0 px-2">
                      <div class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm text-(--color-text-secondary)">
                        {{ order.cycle_id ? '找不到對應的 AI 決策記錄' : '此訂單無 cycle_id（停損/停利觸發）' }}
                      </div>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>

          <!-- Mobile cards -->
          <div v-if="!loading && filteredOrders.length" class="md:hidden space-y-3 overflow-auto flex-1 min-h-0">
            <div
              v-for="order in filteredOrders"
              :key="order.id"
              class="bg-(--color-bg-secondary) rounded-lg p-3"
              @click="toggleExpand(order.id)"
            >
              <div class="flex justify-between items-center mb-2">
                <div class="flex items-center gap-2">
                  <span
                    :class="order.side === 'buy' ? 'text-(--color-success)' : 'text-(--color-danger)'"
                    class="font-bold text-base"
                  >{{ order.side === 'buy' ? '買入' : '賣出' }}</span>
                  <span class="font-medium text-base">{{ order.symbol }}</span>
                </div>
                <div class="flex items-center gap-1.5">
                  <span v-if="getDecision(order)" class="px-1.5 py-0.5 rounded text-xs font-medium" :class="{
                    'bg-(--color-success)/20 text-(--color-success)': getDecision(order)!.action === 'BUY',
                    'bg-(--color-danger)/20 text-(--color-danger)': getDecision(order)!.action === 'SELL',
                  }">AI {{ (getDecision(order)!.confidence * 100).toFixed(0) }}%</span>
                  <span class="px-2 py-0.5 rounded text-xs" :class="{
                    'bg-(--color-success)/20 text-(--color-success)': order.status === 'filled' || order.status === 'closed',
                    'bg-(--color-warning)/20 text-(--color-warning)': order.status === 'partial',
                    'bg-(--color-text-secondary)/20 text-(--color-text-secondary)': order.status === 'cancelled',
                  }">{{ statusLabel(order.status) }}</span>
                </div>
              </div>
              <div class="grid grid-cols-2 gap-1 text-sm text-(--color-text-secondary)">
                <div>數量: {{ order.quantity.toFixed(6) }}</div>
                <div>價格: ${{ order.price?.toFixed(2) ?? '-' }}</div>
                <div>成交: {{ order.filled?.toFixed(6) ?? '-' }}</div>
                <div>{{ formatDateShort(order.created_at) }}</div>
              </div>
              <!-- Mobile expanded AI detail -->
              <div v-if="expandedOrderId === order.id && getDecision(order)" class="mt-2 pt-2 border-t border-(--color-border)/50">
                <div class="flex items-center gap-2 mb-1">
                  <span class="text-xs font-medium text-(--color-text-primary)">AI 決策</span>
                  <span class="text-xs text-(--color-text-secondary)">信心 {{ (getDecision(order)!.confidence * 100).toFixed(0) }}%</span>
                </div>
                <p class="text-sm text-(--color-text-primary) leading-relaxed whitespace-pre-wrap">{{ getDecision(order)!.reasoning }}</p>
              </div>
              <div v-else-if="expandedOrderId === order.id && !getDecision(order)" class="mt-2 pt-2 border-t border-(--color-border)/50 text-xs text-(--color-text-secondary)">
                {{ order.cycle_id ? '找不到對應的 AI 決策記錄' : '此訂單無 cycle_id' }}
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
