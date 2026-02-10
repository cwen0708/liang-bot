<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, type IChartApi, type UTCTimestamp, ColorType, BaselineSeries } from 'lightweight-charts'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { LoanHealth } from '@/types'

const { rows: loanHistory, loading } = useRealtimeTable<LoanHealth>('loan_health', { limit: 200 })

// 閾值：與 config.yaml loan_guard 保持一致
const DANGER_LTV = 0.75
const LOW_LTV = 0.40
const WARN_RANGE = 0.05

// 每個幣種的圖表容器 refs
const chartRefs = ref<Record<string, HTMLElement | null>>({})
const charts = new Map<string, { chart: IChartApi; series: any }>()

// 按幣種分組的歷史記錄（時間升序）
const historyByPair = computed(() => {
  const map = new Map<string, LoanHealth[]>()
  for (const l of loanHistory.value) {
    const key = `${l.collateral_coin}/${l.loan_coin}`
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(l)
  }
  for (const [, arr] of map) {
    arr.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
  }
  return map
})

const pairKeys = computed(() => [...historyByPair.value.keys()].sort())

const latestPerPair = computed(() => {
  const map = new Map<string, LoanHealth>()
  for (const l of loanHistory.value) {
    const key = `${l.collateral_coin}/${l.loan_coin}`
    if (!map.has(key)) map.set(key, l)
  }
  return map
})

function ltvColor(ltv: number) {
  if (ltv >= DANGER_LTV) return 'text-(--color-danger)'
  if (ltv >= DANGER_LTV - WARN_RANGE) return 'text-(--color-warning)'
  if (ltv <= LOW_LTV) return 'text-(--color-success)'
  if (ltv <= LOW_LTV + WARN_RANGE) return 'text-(--color-success)'
  return 'text-(--color-text-primary)'
}

function ltvBgColor(ltv: number) {
  if (ltv >= DANGER_LTV) return 'bg-(--color-danger)'
  if (ltv >= DANGER_LTV - WARN_RANGE) return 'bg-(--color-warning)'
  if (ltv <= LOW_LTV) return 'bg-(--color-success)'
  if (ltv <= LOW_LTV + WARN_RANGE) return 'bg-(--color-success)'
  return 'bg-(--color-accent)'
}

function setChartRef(el: any, pair: string) {
  if (el) chartRefs.value[pair] = el as HTMLElement
}

function createMiniChart(pair: string) {
  const container = chartRefs.value[pair]
  if (!container) return

  if (charts.has(pair)) {
    charts.get(pair)!.chart.remove()
    charts.delete(pair)
  }

  const chart = createChart(container, {
    width: container.clientWidth,
    height: 80,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: '#64748b',
      fontSize: 9,
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { color: '#1e293b', style: 2 },
    },
    rightPriceScale: {
      borderVisible: false,
      scaleMargins: { top: 0.1, bottom: 0.1 },
    },
    timeScale: {
      borderVisible: false,
      timeVisible: true,
      fixLeftEdge: true,
      fixRightEdge: true,
    },
    crosshair: {
      vertLine: { visible: false },
      horzLine: { visible: false },
    },
    handleScroll: false,
    handleScale: false,
  })

  // Baseline series: 以 target_ltv (65%) 為基線
  // 高於 65% 偏紅（接近危險），低於 65% 偏綠（質押物升值）
  const series = chart.addSeries(BaselineSeries, {
    baseValue: { type: 'price', price: 65 },
    topLineColor: '#ef4444',
    topFillColor1: 'rgba(239,68,68,0.15)',
    topFillColor2: 'rgba(239,68,68,0.02)',
    bottomLineColor: '#22c55e',
    bottomFillColor1: 'rgba(34,197,94,0.02)',
    bottomFillColor2: 'rgba(34,197,94,0.15)',
    lineWidth: 2,
    priceFormat: { type: 'custom', formatter: (p: number) => `${p.toFixed(1)}%` },
  })

  charts.set(pair, { chart, series })
}

function updateChartData(pair: string) {
  const entry = charts.get(pair)
  if (!entry) return
  const history = historyByPair.value.get(pair)
  if (!history || !history.length) return

  const data = history.map((h) => ({
    time: Math.floor(new Date(h.created_at).getTime() / 1000) as UTCTimestamp,
    value: h.ltv * 100,
  }))
  entry.series.setData(data)
  entry.chart.timeScale().fitContent()
}

function handleResize() {
  for (const [pair, entry] of charts) {
    const container = chartRefs.value[pair]
    if (container) {
      entry.chart.applyOptions({ width: container.clientWidth })
    }
  }
}

watch(
  () => pairKeys.value,
  async (keys) => {
    await nextTick()
    for (const pair of keys) {
      if (!charts.has(pair)) createMiniChart(pair)
      updateChartData(pair)
    }
  },
  { immediate: true },
)

watch(
  () => loanHistory.value.length,
  () => {
    for (const pair of pairKeys.value) updateChartData(pair)
  },
)

onMounted(() => {
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  for (const entry of charts.values()) entry.chart.remove()
  charts.clear()
})
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-xl md:text-2xl font-bold">借貸監控</h2>

    <div v-if="loading" class="text-sm text-(--color-text-secondary)">載入中...</div>
    <div v-else-if="!pairKeys.length" class="text-sm text-(--color-text-secondary)">無借貸記錄</div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div
        v-for="pair in pairKeys"
        :key="pair"
        class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4"
      >
        <!-- 頂部：幣種 + 當前 LTV + 細節 -->
        <div class="flex justify-between items-start">
          <div>
            <div class="text-xs text-(--color-text-secondary) uppercase font-medium">{{ pair }}</div>
            <div
              v-if="latestPerPair.get(pair)"
              class="text-2xl font-bold mt-1"
              :class="ltvColor(latestPerPair.get(pair)!.ltv)"
            >
              {{ (latestPerPair.get(pair)!.ltv * 100).toFixed(1) }}%
            </div>
          </div>
          <div v-if="latestPerPair.get(pair)" class="text-right text-xs text-(--color-text-secondary) space-y-0.5">
            <div>負債: {{ latestPerPair.get(pair)!.total_debt.toFixed(2) }} {{ latestPerPair.get(pair)!.loan_coin }}</div>
            <div>質押: {{ latestPerPair.get(pair)!.collateral_amount.toFixed(4) }} {{ latestPerPair.get(pair)!.collateral_coin }}</div>
            <div
              v-if="latestPerPair.get(pair)!.action_taken !== 'none'"
              class="font-medium"
              :class="latestPerPair.get(pair)!.action_taken === 'protect' ? 'text-(--color-warning)' : 'text-(--color-success)'"
            >
              {{ latestPerPair.get(pair)!.action_taken === 'protect' ? '保護' : '獲利了結' }}
            </div>
          </div>
        </div>

        <!-- LTV 進度條 -->
        <div class="mt-3 h-1.5 bg-(--color-bg-secondary) rounded-full overflow-hidden">
          <div
            v-if="latestPerPair.get(pair)"
            class="h-full rounded-full transition-all duration-500"
            :class="ltvBgColor(latestPerPair.get(pair)!.ltv)"
            :style="{ width: `${Math.min(latestPerPair.get(pair)!.ltv * 100, 100)}%` }"
          />
        </div>
        <div class="flex justify-between text-[9px] text-(--color-text-secondary) mt-0.5">
          <span>0%</span>
          <span class="text-(--color-success)">40%</span>
          <span>65%</span>
          <span class="text-(--color-danger)">75%</span>
        </div>

        <!-- 迷你 LTV 曲線圖 -->
        <div
          :ref="(el) => setChartRef(el, pair)"
          class="mt-2 w-full"
          style="height: 80px"
        />
      </div>
    </div>
  </div>
</template>
