<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { createChart, type IChartApi, type UTCTimestamp, ColorType, BaselineSeries } from 'lightweight-charts'
import { useRealtimeTable } from '@/composables/useRealtime'
import { useChartColors, useTheme } from '@/composables/useTheme'
import type { LoanHealth, LoanAdjustHistory } from '@/types'

const { rows: loanHistory, loading } = useRealtimeTable<LoanHealth>('loan_health', { limit: 200 })
const { rows: adjustHistory, loading: adjustLoading } = useRealtimeTable<LoanAdjustHistory>('loan_adjust_history', { limit: 20, orderBy: 'adjust_time' })
const { getColors } = useChartColors()
const { isDark } = useTheme()

// 閾值：與 config.yaml loan_guard 保持一致
const DANGER_LTV = 0.75
const LOW_LTV = 0.40
const WARN_RANGE = 0.05

// 每個幣種的圖表容器 refs
const chartRefs = ref<Record<string, HTMLElement | null>>({})
const charts = new Map<string, { chart: IChartApi; series: ReturnType<IChartApi['addSeries']> }>()

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

// 調整歷史（從幣安 API 同步），按時間降序
const sortedAdjustHistory = computed(() =>
  [...adjustHistory.value].sort((a, b) =>
    new Date(b.adjust_time).getTime() - new Date(a.adjust_time).getTime()
  )
)

const latestPerPair = computed(() => {
  const map = new Map<string, LoanHealth>()
  for (const l of loanHistory.value) {
    const key = `${l.collateral_coin}/${l.loan_coin}`
    if (!map.has(key)) map.set(key, l)
  }
  return map
})

function formatTime(ts: string) {
  return new Date(ts).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
    hour12: false,
  })
}

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

function getChartData(pair: string) {
  const history = historyByPair.value.get(pair)
  if (!history || !history.length) return []

  const tzOffset = new Date().getTimezoneOffset() * -60
  const seen = new Set<number>()
  const result: { time: UTCTimestamp; value: number }[] = []

  for (const h of history) {
    let t = Math.floor(new Date(h.created_at).getTime() / 1000) + tzOffset
    // Deduplicate: skip if timestamp already used
    while (seen.has(t)) t++
    seen.add(t)
    result.push({ time: t as UTCTimestamp, value: h.ltv * 100 })
  }
  return result
}

function createMiniChart(pair: string) {
  const container = chartRefs.value[pair]
  if (!container) return

  if (charts.has(pair)) {
    charts.get(pair)!.chart.remove()
    charts.delete(pair)
  }

  const c = getColors()

  const chart = createChart(container, {
    width: container.clientWidth,
    height: 90,
    layout: {
      background: { type: ColorType.Solid, color: 'transparent' },
      textColor: c.text,
      fontSize: 11,
    },
    grid: {
      vertLines: { visible: false },
      horzLines: { color: c.grid, style: 2 },
    },
    crosshair: { mode: 0 },
    timeScale: {
      timeVisible: true,
      secondsVisible: false,
      borderVisible: false,
    },
    rightPriceScale: {
      borderVisible: false,
    },
    handleScroll: false,
    handleScale: false,
  })

  const series = chart.addSeries(BaselineSeries, {
    baseValue: { type: 'price' as const, price: DANGER_LTV * 100 },
    topLineColor: c.baselineTop,
    topFillColor1: c.baselineTopFill1,
    topFillColor2: c.baselineTopFill2,
    bottomLineColor: c.baselineBottom,
    bottomFillColor1: c.baselineBottomFill1,
    bottomFillColor2: c.baselineBottomFill2,
    lineWidth: 2,
  })

  const data = getChartData(pair)
  if (data.length) {
    series.setData(data)
    chart.timeScale().fitContent()
  }

  // 加上 40% 和 75% 的價格線
  series.createPriceLine({
    price: LOW_LTV * 100,
    color: c.pricelineLow,
    lineWidth: 1,
    lineStyle: 2, // Dashed
    axisLabelVisible: true,
    title: '40%',
  })
  series.createPriceLine({
    price: DANGER_LTV * 100,
    color: c.pricelineHigh,
    lineWidth: 1,
    lineStyle: 2,
    axisLabelVisible: true,
    title: '75%',
  })

  charts.set(pair, { chart, series })
}

function updateChartData(pair: string) {
  const entry = charts.get(pair)
  if (!entry) return
  const data = getChartData(pair)
  if (!data.length) return

  entry.series.setData(data)
  entry.chart.timeScale().fitContent()
}

function rebuildAllCharts() {
  for (const pair of pairKeys.value) {
    createMiniChart(pair)
  }
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
      else updateChartData(pair)
    }
  },
  { immediate: true },
)

watch(
  () => loanHistory.value.length,
  () => {
    for (const pair of pairKeys.value) {
      if (charts.has(pair)) updateChartData(pair)
      else createMiniChart(pair)
    }
  },
)

// Rebuild charts on theme change (BaselineSeries fill colors require recreate)
watch(isDark, async () => {
  await nextTick()
  rebuildAllCharts()
})

onMounted(() => {
  window.addEventListener('resize', handleResize)
  // Ensure charts render after layout settles
  setTimeout(() => rebuildAllCharts(), 300)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  for (const entry of charts.values()) entry.chart.remove()
  charts.clear()
})
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <h2 class="text-2xl md:text-3xl font-bold">借貸監控</h2>

    <div v-if="loading" class="text-base text-(--color-text-secondary)">載入中...</div>
    <div v-else-if="!pairKeys.length" class="text-base text-(--color-text-secondary)">無借貸記錄</div>

    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div
        v-for="pair in pairKeys"
        :key="pair"
        class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none"
      >
        <!-- 頂部：幣種 + 當前 LTV + 細節 -->
        <div class="flex justify-between items-start">
          <div>
            <div class="text-base text-(--color-text-secondary) uppercase font-medium">{{ pair }}</div>
            <div
              v-if="latestPerPair.get(pair)"
              class="text-3xl font-bold mt-1"
              :class="ltvColor(latestPerPair.get(pair)!.ltv)"
            >
              {{ (latestPerPair.get(pair)!.ltv * 100).toFixed(1) }}%
            </div>
          </div>
          <div v-if="latestPerPair.get(pair)" class="text-right text-sm text-(--color-text-secondary) space-y-0.5">
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
        <div class="flex justify-between text-sm text-(--color-text-secondary) mt-0.5">
          <span>0%</span>
          <span class="text-(--color-success)">40%</span>
          <span>65%</span>
          <span class="text-(--color-danger)">75%</span>
        </div>

        <!-- 迷你 LTV 曲線圖 (lightweight-charts) -->
        <div
          :ref="(el) => setChartRef(el, pair)"
          class="mt-2 w-full"
          style="height: 90px"
        />
      </div>
    </div>

    <!-- LTV 調整歷史（幣安 API 同步） -->
    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-3 md:p-4 shadow-sm dark:shadow-none">
      <h3 class="text-base font-semibold text-(--color-text-secondary) uppercase mb-3">LTV 調整歷史</h3>
      <div v-if="adjustLoading" class="text-base text-(--color-text-secondary)">載入中...</div>
      <div v-else-if="!sortedAdjustHistory.length" class="text-base text-(--color-text-secondary)">尚無調整記錄</div>
      <div v-else class="space-y-2">
        <div
          v-for="a in sortedAdjustHistory"
          :key="a.id"
          class="flex items-center justify-between py-2 px-3 rounded-lg bg-(--color-bg-secondary)"
        >
          <div class="flex items-center gap-3">
            <span
              class="inline-flex items-center px-2 py-0.5 rounded text-sm font-bold"
              :class="a.direction === 'ADDITIONAL'
                ? 'bg-(--color-success)/15 text-(--color-success)'
                : 'bg-(--color-warning)/15 text-(--color-warning)'"
            >
              {{ a.direction === 'ADDITIONAL' ? '增加質押' : '減少質押' }}
            </span>
            <span class="text-base font-medium">{{ a.collateral_coin }}/{{ a.loan_coin }}</span>
          </div>
          <div class="text-right">
            <div class="text-sm">
              <span class="font-mono font-bold">{{ a.amount }}</span> {{ a.collateral_coin }}
              <span class="text-(--color-text-secondary) ml-2">
                LTV {{ (a.pre_ltv * 100).toFixed(1) }}% → {{ (a.after_ltv * 100).toFixed(1) }}%
              </span>
            </div>
            <div class="text-sm text-(--color-text-secondary)">{{ formatTime(a.adjust_time) }}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
