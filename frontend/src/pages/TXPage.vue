<script setup lang="ts">
import { ref, computed, onUnmounted, watch } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import { useBotStore } from '@/stores/bot'
import type { StrategyVerdict } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()

// ─── Config ──────────────────────────────────────
const txStrategies = ['sma_crossover', 'rsi_oversold', 'bollinger_breakout', 'macd_momentum', 'vwap_reversion', 'ema_ribbon']
const txTimeframes = ['15m', '1h', '1d']
const strategyLabel: Record<string, string> = {
  sma_crossover: 'SMA 交叉',
  rsi_oversold: 'RSI 超買賣',
  bollinger_breakout: '布林突破',
  macd_momentum: 'MACD 動能',
  vwap_reversion: 'VWAP 回歸',
  ema_ribbon: 'EMA 絲帶',
}

// ─── Data ────────────────────────────────────────
const txVerdicts = ref<StrategyVerdict[]>([])
const txLoading = ref(true)
const txPrice = ref<number | null>(null)

async function fetchTXVerdicts() {
  txLoading.value = true
  const { data } = await supabase
    .from('strategy_verdicts')
    .select('*')
    .eq('market_type', 'tx')
    .eq('mode', bot.globalMode)
    .order('created_at', { ascending: false })
    .limit(200)
  if (data) txVerdicts.value = data as StrategyVerdict[]
  txLoading.value = false
}

async function fetchTXPrice() {
  const { data } = await supabase
    .from('market_snapshots')
    .select('price')
    .eq('symbol', 'TX')
    .eq('mode', bot.globalMode)
    .order('created_at', { ascending: false })
    .limit(1)
    .single()
  if (data) txPrice.value = data.price
}

fetchTXVerdicts()
fetchTXPrice()
watch(() => bot.globalMode, () => { fetchTXVerdicts(); fetchTXPrice() })

// ─── Realtime ────────────────────────────────────
const txChannel = supabase
  .channel('rt:strategy_verdicts:tx:page')
  .on(
    'postgres_changes',
    { event: 'INSERT', schema: 'public', table: 'strategy_verdicts', filter: 'market_type=eq.tx' },
    (payload) => {
      const v = payload.new as StrategyVerdict
      if ((v.mode ?? 'live') !== bot.globalMode) return
      txVerdicts.value = [v, ...txVerdicts.value].slice(0, 200)
    },
  )
  .subscribe()

const txPriceChannel = supabase
  .channel('rt:market_snapshots:tx:page')
  .on(
    'postgres_changes',
    { event: 'INSERT', schema: 'public', table: 'market_snapshots', filter: 'symbol=eq.TX' },
    (payload) => {
      const snap = payload.new as { price: number; mode: string }
      if ((snap.mode ?? 'live') !== bot.globalMode) return
      txPrice.value = snap.price
    },
  )
  .subscribe()

onUnmounted(() => {
  supabase.removeChannel(txChannel)
  supabase.removeChannel(txPriceChannel)
})

// ─── Session status ──────────────────────────────
const sessionStatus = computed(() => {
  const now = new Date()
  const utc8 = new Date(now.getTime() + 8 * 3600000)
  const h = utc8.getUTCHours()
  const m = utc8.getUTCMinutes()
  const t = h * 60 + m
  const wd = utc8.getUTCDay()

  if (wd === 0 || wd === 6) return { active: false, label: '休市', session: '' }

  // Day session: 08:45-13:45
  if (t >= 525 && t <= 825) return { active: true, label: '交易中', session: '日盤' }
  // Night session: 15:00-05:00 next day
  if (t >= 900 || t < 300) return { active: true, label: '交易中', session: '夜盤' }

  return { active: false, label: '休市', session: '' }
})

// ─── Matrix computation ──────────────────────────
type TXCell = { signal: string; confidence: number; reasoning: string; timeAgoStr: string } | null

function timeAgo(ts: string): string {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小時前`
  return `${Math.floor(diff / 86400)}天前`
}

function signalBadgeClass(signal: string) {
  if (signal === 'BUY' || signal === 'COVER') return 'text-(--color-success)'
  if (signal === 'SELL' || signal === 'SHORT') return 'text-(--color-danger)'
  return 'text-(--color-text-muted)'
}

function signalLabel(signal: string) {
  if (signal === 'BUY') return '買入'
  if (signal === 'SELL') return '賣出'
  if (signal === 'SHORT') return '做空'
  if (signal === 'COVER') return '平空'
  if (signal === 'HOLD') return '觀望'
  return signal
}

function verdictBgStyle(signal: string, confidence: number): Record<string, string> {
  const bg = 'var(--color-bg-secondary)'
  if (confidence <= 0 || signal === 'HOLD') return { background: bg }
  const pct = Math.round(confidence * 100)
  const color = (signal === 'BUY' || signal === 'COVER') ? 'var(--color-success)' : 'var(--color-danger)'
  return {
    background: `linear-gradient(to right, color-mix(in srgb, ${color} 25%, transparent) 0%, color-mix(in srgb, ${color} 12%, transparent) ${pct}%, transparent ${pct}%) ${bg}`,
  }
}

const txMatrix = computed(() => {
  const latest = new Map<string, StrategyVerdict>()
  for (const v of txVerdicts.value) {
    const key = `${v.strategy}:${v.timeframe}`
    const prev = latest.get(key)
    if (!prev || new Date(v.created_at) > new Date(prev.created_at)) {
      latest.set(key, v)
    }
  }

  return txStrategies.map(s => {
    const cells: Record<string, TXCell> = {}
    for (const tf of txTimeframes) {
      const v = latest.get(`${s}:${tf}`)
      cells[tf] = v ? {
        signal: v.signal,
        confidence: v.confidence,
        reasoning: v.reasoning,
        timeAgoStr: timeAgo(v.created_at),
      } : null
    }
    return { strategy: s, label: strategyLabel[s] ?? s, cells }
  })
})

// ─── Signal summary ──────────────────────────────
const signalSummary = computed(() => {
  let buy = 0, sell = 0, hold = 0
  for (const row of txMatrix.value) {
    for (const tf of txTimeframes) {
      const cell = row.cells[tf]
      if (!cell) continue
      if (cell.signal === 'BUY' || cell.signal === 'COVER') buy++
      else if (cell.signal === 'SELL' || cell.signal === 'SHORT') sell++
      else hold++
    }
  }
  return { buy, sell, hold, total: buy + sell + hold }
})

// ─── Recent verdicts timeline ────────────────────
const recentVerdicts = computed(() => {
  const seen = new Set<string>()
  const result: StrategyVerdict[] = []
  for (const v of txVerdicts.value) {
    if (v.signal === 'HOLD') continue
    const key = `${v.strategy}:${v.timeframe}:${v.signal}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push(v)
    if (result.length >= 10) break
  }
  return result
})

// ─── Expanded verdict ────────────────────────────
const expandedIdx = ref<number | null>(null)
</script>

<template>
  <div class="p-4 md:p-6 flex flex-col gap-4 md:gap-6 md:h-[calc(100vh)] md:overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between gap-2 shrink-0 flex-wrap">
      <div class="flex items-center gap-3">
        <h2 class="text-2xl font-bold md:hidden">台指</h2>
        <div class="flex items-center gap-1.5">
          <span
            class="w-2 h-2 rounded-full"
            :class="sessionStatus.active ? 'bg-(--color-success) animate-pulse' : 'bg-(--color-text-muted)'"
          />
          <span class="text-xs font-medium" :class="sessionStatus.active ? 'text-(--color-success)' : 'text-(--color-text-muted)'">
            {{ sessionStatus.label }}
            <template v-if="sessionStatus.session"> &middot; {{ sessionStatus.session }}</template>
          </span>
        </div>
      </div>
      <div v-if="txPrice" class="text-right">
        <div class="text-2xl font-bold tabular-nums">{{ txPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }}</div>
        <div class="text-xs text-(--color-text-muted)">加權指數</div>
      </div>
    </div>

    <!-- Scrollable content -->
    <div class="flex flex-col gap-4 md:gap-5 min-h-0 md:flex-1 md:overflow-auto">

      <!-- Signal summary cards -->
      <div class="grid grid-cols-3 gap-2 md:gap-3">
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3">
          <div class="text-xs text-(--color-text-secondary)">買入信號</div>
          <div class="text-xl md:text-2xl font-bold text-(--color-success) mt-0.5">{{ signalSummary.buy }}</div>
          <div class="text-[11px] text-(--color-text-muted) mt-0.5">/ {{ signalSummary.total }}</div>
        </div>
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3">
          <div class="text-xs text-(--color-text-secondary)">賣出信號</div>
          <div class="text-xl md:text-2xl font-bold text-(--color-danger) mt-0.5">{{ signalSummary.sell }}</div>
          <div class="text-[11px] text-(--color-text-muted) mt-0.5">/ {{ signalSummary.total }}</div>
        </div>
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-2.5 md:p-3">
          <div class="text-xs text-(--color-text-secondary)">觀望</div>
          <div class="text-xl md:text-2xl font-bold text-(--color-text-secondary) mt-0.5">{{ signalSummary.hold }}</div>
          <div class="text-[11px] text-(--color-text-muted) mt-0.5">/ {{ signalSummary.total }}</div>
        </div>
      </div>

      <!-- Signal matrix table -->
      <section>
        <h3 class="text-lg font-semibold text-(--color-text-primary) mb-2">策略信號矩陣</h3>
        <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden shadow-sm dark:shadow-none">
          <div v-if="txLoading" class="p-4 text-sm text-(--color-text-secondary)">載入中...</div>
          <div v-else-if="!txMatrix.length || !txVerdicts.length" class="p-4 text-sm text-(--color-text-secondary)">尚無分析數據，等待 Bot 執行策略分析...</div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-(--color-border)/30">
                  <th class="text-left px-3 py-2.5 text-xs font-semibold text-(--color-text-secondary) w-[120px]">策略</th>
                  <th v-for="tf in txTimeframes" :key="tf" class="text-center px-2 py-2.5 text-xs font-semibold text-(--color-text-secondary)">{{ tf }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in txMatrix" :key="row.strategy" class="border-b border-(--color-border)/20 last:border-0">
                  <td class="px-3 py-2.5 text-xs font-medium text-(--color-text-primary)">{{ row.label }}</td>
                  <td v-for="tf in txTimeframes" :key="tf" class="text-center px-2 py-2">
                    <template v-if="row.cells[tf]">
                      <div
                        class="inline-flex flex-col items-center gap-0.5 px-2.5 py-1.5 rounded-lg cursor-default transition-colors"
                        :style="verdictBgStyle(row.cells[tf]!.signal, row.cells[tf]!.confidence)"
                        :title="row.cells[tf]!.reasoning"
                      >
                        <span class="text-[11px] font-semibold" :class="signalBadgeClass(row.cells[tf]!.signal)">
                          {{ signalLabel(row.cells[tf]!.signal) }}
                        </span>
                        <span class="text-[9px] tabular-nums text-(--color-text-muted)">
                          {{ (row.cells[tf]!.confidence * 100).toFixed(0) }}%
                        </span>
                      </div>
                    </template>
                    <span v-else class="text-[10px] text-(--color-text-muted) opacity-30">-</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Last updated -->
          <div v-if="txVerdicts.length" class="px-4 py-2 border-t border-(--color-border)/30">
            <span class="text-[10px] text-(--color-text-muted)">最後更新: {{ timeAgo(txVerdicts[0]!.created_at) }}</span>
          </div>
        </div>
      </section>

      <!-- Recent non-HOLD signals -->
      <section v-if="recentVerdicts.length">
        <h3 class="text-lg font-semibold text-(--color-text-primary) mb-2">近期信號</h3>
        <div class="flex flex-col gap-1.5">
          <div
            v-for="(v, idx) in recentVerdicts"
            :key="v.id"
            class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-2 cursor-pointer hover:border-(--color-accent)/50 transition-colors"
            @click="expandedIdx = expandedIdx === idx ? null : idx"
          >
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span
                  class="px-1.5 py-0.5 rounded text-[11px] font-bold"
                  :class="(v.signal === 'BUY' || v.signal === 'COVER')
                    ? 'bg-(--color-success)/15 text-(--color-success)'
                    : 'bg-(--color-danger)/15 text-(--color-danger)'"
                >{{ signalLabel(v.signal) }}</span>
                <span class="text-xs text-(--color-text-primary) font-medium">{{ strategyLabel[v.strategy] ?? v.strategy }}</span>
                <span class="text-[11px] text-(--color-text-muted)">{{ v.timeframe }}</span>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-[11px] tabular-nums text-(--color-text-muted)">{{ (v.confidence * 100).toFixed(0) }}%</span>
                <span class="text-[10px] text-(--color-text-muted)">{{ timeAgo(v.created_at) }}</span>
              </div>
            </div>
            <div v-if="expandedIdx === idx" class="mt-2 pt-2 border-t border-(--color-border)/30 text-xs text-(--color-text-secondary) leading-relaxed">
              {{ v.reasoning }}
            </div>
          </div>
        </div>
      </section>

      <!-- Info note -->
      <div class="text-xs text-(--color-text-muted) pb-4">
        台灣加權指數 (^TWII) 策略分析，數據來源 Yahoo Finance。僅供參考，不執行交易。
        <br>日盤 08:45-13:45 / 夜盤 15:00-05:00 (UTC+8)
      </div>
    </div>
  </div>
</template>
