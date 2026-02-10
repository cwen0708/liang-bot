<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { createChart, type IChartApi, type UTCTimestamp, ColorType, LineSeries } from 'lightweight-charts'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import type { MarketSnapshot } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()

const chartContainer = ref<HTMLElement>()
const selectedSymbol = ref('BTC/USDT')

let chart: IChartApi | null = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let lineSeries: any = null

async function loadChartData() {
  const { data } = await supabase
    .from('market_snapshots')
    .select('price, created_at')
    .eq('symbol', selectedSymbol.value)
    .order('created_at', { ascending: true })
    .limit(500)

  if (!data || !chart) return

  const lineData = (data as Array<{ price: number; created_at: string }>).map((d) => ({
    time: Math.floor(new Date(d.created_at).getTime() / 1000) as UTCTimestamp,
    value: d.price,
  }))

  if (lineSeries) {
    lineSeries.setData(lineData)
  }
}

onMounted(() => {
  if (!chartContainer.value) return

  chart = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: 400,
    layout: {
      background: { type: ColorType.Solid, color: '#1a2332' },
      textColor: '#94a3b8',
    },
    grid: {
      vertLines: { color: '#2d3748' },
      horzLines: { color: '#2d3748' },
    },
    crosshair: {
      mode: 0,
    },
  })

  lineSeries = chart.addSeries(LineSeries, {
    color: '#3b82f6',
    lineWidth: 2,
  })

  loadChartData()

  // Realtime price updates
  const channel = supabase
    .channel('chart:snapshots')
    .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'market_snapshots' }, (p) => {
      const snap = p.new as MarketSnapshot
      if (snap.symbol === selectedSymbol.value && lineSeries) {
        lineSeries.update({
          time: Math.floor(new Date(snap.created_at).getTime() / 1000) as UTCTimestamp,
          value: snap.price,
        })
      }
    })
    .subscribe()

  const resizeObserver = new ResizeObserver(() => {
    if (chart && chartContainer.value) {
      chart.applyOptions({ width: chartContainer.value.clientWidth })
    }
  })
  resizeObserver.observe(chartContainer.value)

  onUnmounted(() => {
    supabase.removeChannel(channel)
    resizeObserver.disconnect()
    chart?.remove()
  })
})

watch(selectedSymbol, () => loadChartData())
</script>

<template>
  <div class="p-6 space-y-6">
    <div class="flex items-center justify-between">
      <h2 class="text-2xl font-bold">交易</h2>
      <select
        v-model="selectedSymbol"
        class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm"
      >
        <option v-for="pair in bot.status?.pairs ?? ['BTC/USDT', 'ETH/USDT']" :key="pair">
          {{ pair }}
        </option>
      </select>
    </div>

    <!-- Chart -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">
      <div ref="chartContainer" class="w-full" />
    </div>

    <!-- Positions -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4">
      <h3 class="text-sm font-semibold text-(--color-text-secondary) uppercase mb-3">持倉中</h3>
      <table class="w-full text-sm">
        <thead>
          <tr class="text-(--color-text-secondary) text-left">
            <th class="pb-2">交易對</th>
            <th class="pb-2">數量</th>
            <th class="pb-2">進場價</th>
            <th class="pb-2">現價</th>
            <th class="pb-2">損益</th>
            <th class="pb-2">停損 / 停利</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="pos in bot.positions" :key="pos.symbol" class="border-t border-(--color-border)">
            <td class="py-2 font-medium">{{ pos.symbol }}</td>
            <td>{{ pos.quantity.toFixed(6) }}</td>
            <td>${{ pos.entry_price.toFixed(2) }}</td>
            <td>${{ (bot.latestPrices[pos.symbol] ?? pos.current_price).toFixed(2) }}</td>
            <td :class="pos.unrealized_pnl >= 0 ? 'text-(--color-success)' : 'text-(--color-danger)'">
              {{ pos.unrealized_pnl >= 0 ? '+' : '' }}{{ pos.unrealized_pnl.toFixed(2) }}
            </td>
            <td class="text-(--color-text-secondary)">
              {{ pos.stop_loss?.toFixed(2) ?? '-' }} / {{ pos.take_profit?.toFixed(2) ?? '-' }}
            </td>
          </tr>
          <tr v-if="!bot.positions.length">
            <td colspan="6" class="py-4 text-center text-(--color-text-secondary)">尚無持倉</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
