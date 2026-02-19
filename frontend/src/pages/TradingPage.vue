<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, createSeriesMarkers, type IChartApi, type UTCTimestamp, ColorType, LineStyle, LineSeries } from 'lightweight-charts'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import { useChartColors, useTheme } from '@/composables/useTheme'
import type { Order } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()
const { getColors } = useChartColors()
const { isDark } = useTheme()

const chartContainer = ref<HTMLElement>()
const selectedSymbol = ref('BTC/USDT')
const filterMode = computed(() => bot.globalMode)
const marketTab = ref<'spot' | 'futures'>('spot')
const chartInterval = ref('5m')
const intervalOptions = [
  { value: '1m', label: '1分', ws: '1m' },
  { value: '5m', label: '5分', ws: '5m' },
  { value: '15m', label: '15分', ws: '15m' },
  { value: '1h', label: '1時', ws: '1h' },
  { value: '4h', label: '4時', ws: '4h' },
  { value: '1d', label: '1日', ws: '1d' },
]

let chart: IChartApi | null = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let lineSeries: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let markersPrimitive: any = null
let ws: WebSocket | null = null

onMounted(() => {
  if (!bot.spotPairs.length) bot.fetchConfigPairs()
})

// 合併現貨 + 合約交易對列表
interface PairItem {
  symbol: string
  market: 'spot' | 'futures'
  price: number | null
}
const allPairs = computed<PairItem[]>(() => {
  const items: PairItem[] = []
  for (const s of bot.spotPairs) {
    items.push({ symbol: s, market: 'spot', price: bot.latestPrices[s] ?? null })
  }
  for (const s of bot.futuresPairs) {
    items.push({ symbol: s, market: 'futures', price: bot.latestPrices[s] ?? null })
  }
  return items
})

function selectPair(item: PairItem) {
  selectedSymbol.value = item.symbol
  marketTab.value = item.market
}

// Binance API base URLs
const restBase = computed(() =>
  marketTab.value === 'futures'
    ? 'https://fapi.binance.com/fapi/v1/klines'
    : 'https://api.binance.com/api/v3/klines',
)
const wsBase = computed(() =>
  marketTab.value === 'futures'
    ? 'wss://fstream.binance.com/ws/'
    : 'wss://stream.binance.com:9443/ws/',
)

// Order markers state
const symbolOrders = ref<Order[]>([])
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let priceLines: any[] = []

function getCSSVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

// BTC/USDT → btcusdt
function toBinanceWs(pair: string): string {
  return pair.replace('/', '').toLowerCase()
}

// BTC/USDT → BTCUSDT
function toBinanceRest(pair: string): string {
  return pair.replace('/', '').toUpperCase()
}

// --- Binance REST: Load kline history ---
async function loadChartData() {
  if (!chart || !lineSeries) return

  const symbol = toBinanceRest(selectedSymbol.value)
  const url = `${restBase.value}?symbol=${symbol}&interval=${chartInterval.value}&limit=1000`

  try {
    const resp = await fetch(url)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()

    const tzOffsetSec = new Date().getTimezoneOffset() * -60
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const lineData = data.map((k: any[]) => ({
      time: (Math.floor(k[0] / 1000) + tzOffsetSec) as UTCTimestamp,
      value: parseFloat(k[4]), // close price
    }))

    lineSeries.setData(lineData)
    chart.timeScale().fitContent()
  } catch (e) {
    console.error('Failed to load Binance klines:', e)
  }

  loadOrders()
  updatePositionLines()
}

// --- Binance WebSocket: Real-time kline ---
function connectWebSocket() {
  if (ws) {
    ws.close()
    ws = null
  }

  const symbol = toBinanceWs(selectedSymbol.value)
  const wsInterval = intervalOptions.find(o => o.value === chartInterval.value)?.ws ?? '5m'
  ws = new WebSocket(`${wsBase.value}${symbol}@kline_${wsInterval}`)

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data)
      const k = msg.k
      if (k && lineSeries) {
        const tzOff = new Date().getTimezoneOffset() * -60
        lineSeries.update({
          time: (Math.floor(k.t / 1000) + tzOff) as UTCTimestamp,
          value: parseFloat(k.c),
        })
      }
    } catch { /* ignore parse errors */ }
  }

  ws.onerror = () => {
    console.error('Binance WebSocket error')
  }
}

// Interval → minutes mapping (1000 bars)
const intervalMinutes: Record<string, number> = {
  '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440,
}

// --- Order Markers (v5 createSeriesMarkers) ---
async function loadOrders() {
  // Only load orders within the chart's visible time range (1000 bars)
  const mins = intervalMinutes[chartInterval.value] ?? 60
  const chartStartDate = new Date(Date.now() - mins * 1000 * 60000).toISOString()

  const { data } = await supabase
    .from('orders')
    .select('*')
    .eq('symbol', selectedSymbol.value)
    .eq('mode', filterMode.value)
    .eq('market_type', marketTab.value)
    .gte('created_at', chartStartDate)
    .order('created_at', { ascending: true })
    .limit(200)

  if (data) {
    symbolOrders.value = data as Order[]
    applyMarkers()
  }
}

function applyMarkers() {
  if (!lineSeries) return

  const tzOffsetSec = new Date().getTimezoneOffset() * -60
  const successColor = getCSSVar('--color-success')
  const dangerColor = getCSSVar('--color-danger')

  const markers = symbolOrders.value.map((o) => {
    const isBuy = o.side === 'buy'
    return {
      time: (Math.floor(new Date(o.created_at).getTime() / 1000) + tzOffsetSec) as UTCTimestamp,
      position: isBuy ? 'belowBar' as const : 'aboveBar' as const,
      color: isBuy ? successColor : dangerColor,
      shape: isBuy ? 'arrowUp' as const : 'arrowDown' as const,
      text: isBuy ? `買 $${o.price}` : `賣 $${o.price}`,
    }
  })

  markers.sort((a, b) => (a.time as number) - (b.time as number))

  if (markersPrimitive) {
    markersPrimitive.setMarkers(markers)
  } else {
    markersPrimitive = createSeriesMarkers(lineSeries, markers)
  }
}

// --- Position Price Lines ---
function updatePositionLines() {
  if (!lineSeries) return

  for (const pl of priceLines) {
    lineSeries.removePriceLine(pl)
  }
  priceLines = []

  const pos = bot.positions.find((p) =>
    p.symbol === selectedSymbol.value
    && (p.mode ?? 'live') === filterMode.value
    && (p.market_type ?? 'spot') === marketTab.value,
  )
  if (!pos) return

  const accentColor = getCSSVar('--color-accent')
  const dangerColor = getCSSVar('--color-danger')
  const successColor = getCSSVar('--color-success')

  priceLines.push(
    lineSeries.createPriceLine({
      price: pos.entry_price,
      color: accentColor,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: `入場 $${pos.entry_price}`,
    }),
  )

  if (pos.stop_loss) {
    priceLines.push(
      lineSeries.createPriceLine({
        price: pos.stop_loss,
        color: dangerColor,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `SL $${pos.stop_loss}`,
      }),
    )
  }

  if (pos.take_profit) {
    priceLines.push(
      lineSeries.createPriceLine({
        price: pos.take_profit,
        color: successColor,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `TP $${pos.take_profit}`,
      }),
    )
  }
}

function applyChartTheme() {
  if (!chart || !lineSeries) return
  const c = getColors()
  chart.applyOptions({
    layout: {
      background: { type: ColorType.Solid, color: c.bg },
      textColor: c.text,
    },
    grid: {
      vertLines: { color: c.grid },
      horzLines: { color: c.grid },
    },
  })
  lineSeries.applyOptions({ color: c.line })
  updatePositionLines()
}

onMounted(() => {
  if (!chartContainer.value) return

  const c = getColors()
  chart = createChart(chartContainer.value, {
    width: chartContainer.value.clientWidth,
    height: window.innerWidth < 768 ? 250 : 370,
    layout: {
      background: { type: ColorType.Solid, color: c.bg },
      textColor: c.text,
    },
    grid: {
      vertLines: { color: c.grid },
      horzLines: { color: c.grid },
    },
    crosshair: { mode: 0 },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
    },
  })

  lineSeries = chart.addSeries(LineSeries, {
    color: c.line,
    lineWidth: 2,
  })

  loadChartData()
  connectWebSocket()

  // Realtime: new orders → add marker
  const orderChannel = supabase
    .channel('chart:orders')
    .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'orders' }, (p) => {
      const newOrder = p.new as Order
      if (newOrder.symbol === selectedSymbol.value && (newOrder.mode ?? 'live') === filterMode.value && (newOrder.market_type ?? 'spot') === marketTab.value) {
        symbolOrders.value.push(newOrder)
        applyMarkers()
      }
    })
    .subscribe()

  const resizeObserver = new ResizeObserver(() => {
    if (chart && chartContainer.value) {
      chart.applyOptions({
        width: chartContainer.value.clientWidth,
        height: window.innerWidth < 768 ? 250 : 370,
      })
    }
  })
  resizeObserver.observe(chartContainer.value)

  onUnmounted(() => {
    if (ws) { ws.close(); ws = null }
    supabase.removeChannel(orderChannel)
    resizeObserver.disconnect()
    chart?.remove()
  })
})

// Symbol, interval or market tab change → reload chart + reconnect WS
watch([selectedSymbol, chartInterval, marketTab], () => {
  if (markersPrimitive) {
    markersPrimitive.detach()
    markersPrimitive = null
  }
  loadChartData()
  connectWebSocket()
})

// Mode change → reload orders + position lines
watch(filterMode, () => {
  if (markersPrimitive) {
    markersPrimitive.detach()
    markersPrimitive = null
  }
  loadOrders()
  updatePositionLines()
})

watch(() => bot.positions, () => updatePositionLines(), { deep: true })


watch(isDark, async () => {
  await nextTick()
  applyChartTheme()
})
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-2xl font-bold md:hidden">行情</h2>

    <!-- Chart: Binance live kline -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2 md:p-4 shadow-sm dark:shadow-none">
      <div class="flex gap-1 mb-2">
        <button
          v-for="opt in intervalOptions"
          :key="opt.value"
          class="px-2.5 py-1 rounded text-xs font-medium transition-colors"
          :class="chartInterval === opt.value
            ? 'bg-(--color-accent) text-white'
            : 'text-(--color-text-secondary) hover:text-(--color-text-primary) hover:bg-(--color-bg-secondary)'"
          @click="chartInterval = opt.value"
        >{{ opt.label }}</button>
      </div>
      <div ref="chartContainer" class="w-full" />
    </div>

    <!-- Price Cards: Spot + Futures combined -->
    <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-1.5">
      <div
        v-for="item in allPairs"
        :key="`${item.market}:${item.symbol}`"
        class="flex items-center justify-between px-3 py-2 rounded-lg cursor-pointer transition-colors border"
        :class="[
          selectedSymbol === item.symbol && marketTab === item.market
            ? 'border-(--color-accent) ring-1 ring-(--color-accent)/30'
            : 'border-transparent hover:border-(--color-accent)/30',
          item.market === 'futures'
            ? 'bg-(--color-accent)/6'
            : 'bg-(--color-bg-card)',
        ]"
        :title="item.market === 'futures' ? '合約' : '現貨'"
        @click="selectPair(item)"
      >
        <span class="text-sm font-medium truncate">{{ item.symbol }}</span>
        <span class="font-mono text-sm font-semibold tabular-nums">{{ item.price ?? '—' }}</span>
      </div>
    </div>
  </div>
</template>
