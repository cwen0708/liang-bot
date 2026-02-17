<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useBotStore } from '@/stores/bot'
import { useSupabase } from '@/composables/useSupabase'
import type { DailyReview, ReviewSuggestion } from '@/types'

const bot = useBotStore()
const supabase = useSupabase()

const reviews = ref<DailyReview[]>([])
const loading = ref(true)
const selectedIdx = ref(0)
const expandedReport = ref(false)

async function fetchReviews() {
  loading.value = true
  const { data } = await supabase
    .from('daily_reviews')
    .select('*')
    .eq('mode', bot.globalMode)
    .order('review_date', { ascending: false })
    .limit(30)
  if (data) reviews.value = data as DailyReview[]
  loading.value = false
}

onMounted(fetchReviews)
watch(() => bot.globalMode, fetchReviews)

const currentReview = computed(() => reviews.value[selectedIdx.value] ?? null)

// Score bar helpers
function scoreColor(val: number): string {
  if (val >= 0.7) return 'bg-(--color-success)'
  if (val >= 0.4) return 'bg-amber-500'
  return 'bg-(--color-danger)'
}

function scoreTextColor(val: number): string {
  if (val >= 0.7) return 'text-(--color-success)'
  if (val >= 0.4) return 'text-amber-500'
  return 'text-(--color-danger)'
}

const scoreDimensions = [
  { key: 'strategy_accuracy', label: '策略準確' },
  { key: 'risk_execution', label: '風控執行' },
  { key: 'pnl_performance', label: '損益表現' },
  { key: 'prompt_quality', label: 'Prompt' },
  { key: 'overall', label: '總分' },
] as const

// Trend: all reviews (up to 30 days), reversed to chronological
const trendData = computed(() =>
  [...reviews.value]
    .reverse()
    .map(r => ({
      date: r.review_date,
      overall: (r.scores as any)?.overall ?? 0,
      strategy: (r.scores as any)?.strategy_accuracy ?? 0,
      risk: (r.scores as any)?.risk_execution ?? 0,
      pnl: (r.scores as any)?.pnl_performance ?? 0,
      prompt: (r.scores as any)?.prompt_quality ?? 0,
    })),
)

// SVG line chart helpers
const chartW = 600
const chartH = 160
const chartPadX = 36
const chartPadTop = 8
const chartPadBottom = 24

const trendLines = computed(() => {
  const data = trendData.value
  if (data.length < 2) return []
  const n = data.length
  const xStep = (chartW - chartPadX * 2) / (n - 1)

  const lines: { key: string; label: string; color: string; points: string; dots: { x: number; y: number; val: number }[] }[] = []

  const configs = [
    { key: 'overall', label: '總分', color: 'var(--color-accent, #6366f1)' },
    { key: 'strategy', label: '策略', color: '#f59e0b' },
    { key: 'risk', label: '風控', color: '#10b981' },
    { key: 'pnl', label: '損益', color: '#ef4444' },
    { key: 'prompt', label: 'Prompt', color: '#8b5cf6' },
  ]

  for (const cfg of configs) {
    const dots: { x: number; y: number; val: number }[] = []
    for (let i = 0; i < n; i++) {
      const x = chartPadX + i * xStep
      const val = (data[i] as any)[cfg.key] as number
      const y = chartPadTop + (1 - val) * (chartH - chartPadTop - chartPadBottom)
      dots.push({ x, y, val })
    }
    lines.push({
      key: cfg.key,
      label: cfg.label,
      color: cfg.color,
      points: dots.map(d => `${d.x},${d.y}`).join(' '),
      dots,
    })
  }
  return lines
})

// X-axis labels for the trend chart
const trendXLabels = computed(() => {
  const data = trendData.value
  if (data.length < 2) return []
  const n = data.length
  const xStep = (chartW - chartPadX * 2) / (n - 1)
  // Show at most ~6 labels
  const step = Math.max(1, Math.floor(n / 6))
  const labels: { x: number; text: string }[] = []
  for (let i = 0; i < n; i += step) {
    labels.push({
      x: chartPadX + i * xStep,
      text: data[i]!.date.slice(5), // MM-DD
    })
  }
  // Always include last
  if (labels[labels.length - 1]?.x !== chartPadX + (n - 1) * xStep) {
    labels.push({
      x: chartPadX + (n - 1) * xStep,
      text: data[n - 1]!.date.slice(5),
    })
  }
  return labels
})

// Toggle which lines are visible
const visibleLines = ref(new Set(['overall']))
function toggleLine(key: string) {
  const s = new Set(visibleLines.value)
  if (s.has(key)) { if (s.size > 1) s.delete(key) } // keep at least 1
  else s.add(key)
  visibleLines.value = s
}

// Hover state for trend chart
const hoverIdx = ref<number | null>(null)

// Suggestion helpers
function priorityBorder(p: string): string {
  if (p === 'high') return 'border-l-(--color-danger)'
  if (p === 'medium') return 'border-l-amber-500'
  return 'border-l-(--color-text-muted)'
}

function priorityBadge(p: string): string {
  if (p === 'high') return 'bg-(--color-danger)/15 text-(--color-danger)'
  if (p === 'medium') return 'bg-amber-500/15 text-amber-600'
  return 'bg-(--color-text-muted)/15 text-(--color-text-secondary)'
}

function categoryLabel(c: string): string {
  const map: Record<string, string> = {
    strategy: '策略',
    risk: '風控',
    config: '配置',
    prompt: 'Prompt',
  }
  return map[c] ?? c
}

// Simple markdown → html (headings, bold, lists, code blocks)
function renderMarkdown(md: string): string {
  if (!md) return ''
  let html = md
    // Code blocks
    .replace(/```[\s\S]*?```/g, m => {
      const inner = m.slice(3, -3).replace(/^\w*\n/, '')
      return `<pre class="bg-(--color-bg-secondary) rounded-lg p-3 text-sm overflow-x-auto my-2"><code>${escHtml(inner)}</code></pre>`
    })
    // Headings
    .replace(/^### (.+)$/gm, '<h4 class="font-semibold text-base mt-4 mb-1">$1</h4>')
    .replace(/^## (.+)$/gm, '<h3 class="font-bold text-lg mt-5 mb-2 text-(--color-text-primary)">$1</h3>')
    .replace(/^# (.+)$/gm, '<h2 class="font-bold text-xl mt-6 mb-3 text-(--color-accent)">$1</h2>')
    // Bold
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // Unordered list
    .replace(/^- (.+)$/gm, '<li class="ml-4 list-disc">$1</li>')
    // Paragraphs (double newline)
    .replace(/\n{2,}/g, '</p><p class="my-2">')
    // Single newline within paragraph
    .replace(/\n/g, '<br>')
  return `<p class="my-2">${html}</p>`
}

function escHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function formatDate(d: string): string {
  const date = new Date(d + 'T00:00:00+08:00')
  return date.toLocaleDateString('zh-TW', { month: '2-digit', day: '2-digit', weekday: 'short' })
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4 md:space-y-6">
    <!-- Header -->
    <div class="flex items-center justify-between md:hidden">
      <h2 class="text-2xl font-bold">復盤</h2>
      <div class="text-sm text-(--color-text-secondary)">
        透過 <code class="px-1.5 py-0.5 bg-(--color-bg-secondary) rounded text-xs">/review</code> 產生報告
      </div>
    </div>

    <!-- Content -->
    <div class="flex flex-col gap-4 md:gap-5">

      <!-- Loading / Empty -->
      <div v-if="loading" class="text-center py-12 text-(--color-text-secondary)">載入中...</div>
      <div v-else-if="!reviews.length" class="text-center py-16">
        <div class="text-4xl mb-3 opacity-30">&#128202;</div>
        <div class="text-lg text-(--color-text-secondary) mb-2">尚無復盤記錄</div>
        <div class="text-sm text-(--color-text-muted)">
          在 Claude Code 中輸入 <code class="px-1.5 py-0.5 bg-(--color-bg-secondary) rounded text-xs">/review</code> 執行第一次復盤
        </div>
      </div>

      <template v-else>
        <!-- Date selector (horizontal scroll chips) -->
        <div class="flex gap-1.5 overflow-x-auto shrink-0 pb-1">
          <button
            v-for="(r, idx) in reviews"
            :key="r.id"
            class="shrink-0 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap"
            :class="selectedIdx === idx
              ? 'bg-(--color-accent) text-white'
              : 'bg-(--color-bg-card) border border-(--color-border) text-(--color-text-secondary) hover:border-(--color-accent)/50'"
            @click="selectedIdx = idx"
          >
            {{ formatDate(r.review_date) }}
          </button>
        </div>

        <template v-if="currentReview">
          <!-- Score Overview -->
          <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4 shadow-sm dark:shadow-none">
            <div class="flex items-center justify-between mb-3">
              <h3 class="font-semibold text-lg">評分</h3>
              <div class="text-sm text-(--color-text-secondary)">{{ currentReview.model }}</div>
            </div>
            <div class="grid grid-cols-5 gap-3">
              <div
                v-for="dim in scoreDimensions"
                :key="dim.key"
                class="text-center"
              >
                <div
                  class="text-2xl md:text-3xl font-bold font-mono"
                  :class="scoreTextColor((currentReview.scores as any)?.[dim.key] ?? 0)"
                >
                  {{ (((currentReview.scores as any)?.[dim.key] ?? 0) * 100).toFixed(0) }}
                </div>
                <div class="text-[11px] text-(--color-text-secondary) mt-0.5">{{ dim.label }}</div>
                <!-- Mini bar -->
                <div class="mt-1.5 h-1.5 bg-(--color-bg-secondary) rounded-full overflow-hidden">
                  <div
                    class="h-full rounded-full transition-all"
                    :class="scoreColor((currentReview.scores as any)?.[dim.key] ?? 0)"
                    :style="{ width: (((currentReview.scores as any)?.[dim.key] ?? 0) * 100) + '%' }"
                  />
                </div>
              </div>
            </div>
          </div>

          <!-- Score Trend (line chart, up to 30 days) -->
          <div v-if="trendData.length > 1" class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4 shadow-sm dark:shadow-none">
            <div class="flex items-center justify-between mb-3">
              <h3 class="font-semibold text-base">評分趨勢</h3>
              <div class="flex gap-1 flex-wrap">
                <button
                  v-for="line in trendLines"
                  :key="line.key"
                  class="px-2 py-0.5 rounded text-[11px] font-medium transition-all border"
                  :class="visibleLines.has(line.key)
                    ? 'border-transparent text-white'
                    : 'border-(--color-border) text-(--color-text-muted) opacity-50'"
                  :style="visibleLines.has(line.key) ? { backgroundColor: line.color } : {}"
                  @click="toggleLine(line.key)"
                >{{ line.label }}</button>
              </div>
            </div>

            <!-- SVG Chart -->
            <div class="w-full overflow-x-auto">
              <svg
                :viewBox="`0 0 ${chartW} ${chartH}`"
                class="w-full min-w-[320px]"
                preserveAspectRatio="xMidYMid meet"
                @mouseleave="hoverIdx = null"
              >
                <!-- Y-axis grid lines -->
                <line v-for="y in [0, 0.25, 0.5, 0.75, 1.0]" :key="y"
                  :x1="chartPadX" :x2="chartW - chartPadX"
                  :y1="chartPadTop + (1 - y) * (chartH - chartPadTop - chartPadBottom)"
                  :y2="chartPadTop + (1 - y) * (chartH - chartPadTop - chartPadBottom)"
                  stroke="var(--color-border, #e5e7eb)" stroke-width="0.5" stroke-dasharray="3,3"
                />
                <!-- Y-axis labels -->
                <text v-for="y in [0, 0.25, 0.5, 0.75, 1.0]" :key="'yl'+y"
                  :x="chartPadX - 4"
                  :y="chartPadTop + (1 - y) * (chartH - chartPadTop - chartPadBottom) + 3"
                  text-anchor="end" fill="var(--color-text-muted, #9ca3af)" font-size="9" font-family="monospace"
                >{{ (y * 100).toFixed(0) }}</text>

                <!-- Lines -->
                <template v-for="line in trendLines" :key="line.key">
                  <polyline
                    v-if="visibleLines.has(line.key)"
                    :points="line.points"
                    fill="none"
                    :stroke="line.color"
                    :stroke-width="line.key === 'overall' ? 2.5 : 1.5"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    :opacity="line.key === 'overall' ? 1 : 0.7"
                  />
                </template>

                <!-- Hover overlay (invisible rects for each data point) -->
                <rect
                  v-for="(pt, i) in trendData"
                  :key="'hover'+i"
                  :x="chartPadX + i * ((chartW - chartPadX * 2) / (trendData.length - 1)) - (chartW - chartPadX * 2) / (trendData.length - 1) / 2"
                  :y="chartPadTop"
                  :width="(chartW - chartPadX * 2) / (trendData.length - 1)"
                  :height="chartH - chartPadTop - chartPadBottom"
                  fill="transparent"
                  @mouseenter="hoverIdx = i"
                />

                <!-- Hover vertical line -->
                <line
                  v-if="hoverIdx !== null"
                  :x1="chartPadX + hoverIdx * ((chartW - chartPadX * 2) / (trendData.length - 1))"
                  :x2="chartPadX + hoverIdx * ((chartW - chartPadX * 2) / (trendData.length - 1))"
                  :y1="chartPadTop" :y2="chartH - chartPadBottom"
                  stroke="var(--color-text-muted, #9ca3af)" stroke-width="0.5" stroke-dasharray="2,2"
                />

                <!-- Dots on hover -->
                <template v-if="hoverIdx !== null">
                  <template v-for="line in trendLines" :key="'dot'+line.key">
                    <circle
                      v-if="visibleLines.has(line.key) && line.dots[hoverIdx!]"
                      :cx="line.dots[hoverIdx!]!.x"
                      :cy="line.dots[hoverIdx!]!.y"
                      r="3.5"
                      :fill="line.color"
                      stroke="var(--color-bg-card, #fff)" stroke-width="1.5"
                    />
                  </template>
                </template>

                <!-- X-axis labels -->
                <text
                  v-for="lbl in trendXLabels"
                  :key="lbl.x"
                  :x="lbl.x" :y="chartH - 4"
                  text-anchor="middle" fill="var(--color-text-muted, #9ca3af)" font-size="9" font-family="monospace"
                >{{ lbl.text }}</text>
              </svg>
            </div>

            <!-- Hover tooltip -->
            <div v-if="hoverIdx !== null && trendData[hoverIdx!]" class="flex items-center gap-3 mt-2 text-xs text-(--color-text-secondary) flex-wrap">
              <span class="font-medium text-(--color-text-primary)">{{ trendData[hoverIdx!]!.date }}</span>
              <template v-for="line in trendLines" :key="'tip'+line.key">
                <span v-if="visibleLines.has(line.key)" class="font-mono" :style="{ color: line.color }">
                  {{ line.label }} {{ ((trendData[hoverIdx!] as any)[line.key] * 100).toFixed(0) }}
                </span>
              </template>
            </div>
          </div>

          <!-- Suggestions -->
          <div v-if="currentReview.suggestions?.length" class="bg-(--color-bg-card) border border-(--color-border) rounded-xl shadow-sm dark:shadow-none">
            <div class="p-4 border-b border-(--color-border)">
              <h3 class="font-semibold text-lg">改進建議 <span class="text-sm font-normal text-(--color-text-muted)">{{ currentReview.suggestions.length }}</span></h3>
            </div>
            <div class="divide-y divide-(--color-border)">
              <div
                v-for="(s, i) in (currentReview.suggestions as ReviewSuggestion[])"
                :key="i"
                class="p-4 border-l-3"
                :class="priorityBorder(s.priority)"
              >
                <div class="flex items-center gap-2 mb-1 flex-wrap">
                  <span class="px-1.5 py-0.5 rounded text-[11px] font-medium" :class="priorityBadge(s.priority)">
                    {{ s.priority === 'high' ? '高' : s.priority === 'medium' ? '中' : '低' }}
                  </span>
                  <span class="px-1.5 py-0.5 rounded text-[11px] bg-(--color-bg-secondary) text-(--color-text-secondary)">
                    {{ categoryLabel(s.category) }}
                  </span>
                  <span class="font-medium text-sm text-(--color-text-primary)">{{ s.title }}</span>
                </div>
                <p class="text-sm text-(--color-text-secondary) leading-relaxed">{{ s.detail }}</p>
                <p v-if="s.action" class="text-sm text-(--color-accent) mt-1">{{ s.action }}</p>
              </div>
            </div>
          </div>

          <!-- Full Report -->
          <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl shadow-sm dark:shadow-none">
            <div
              class="p-4 border-b border-(--color-border) flex items-center justify-between cursor-pointer hover:bg-(--color-bg-secondary)/50 transition-colors rounded-t-xl"
              @click="expandedReport = !expandedReport"
            >
              <h3 class="font-semibold text-lg">完整報告</h3>
              <svg
                xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24"
                fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
                class="text-(--color-text-muted) transition-transform"
                :class="expandedReport ? 'rotate-180' : ''"
              ><polyline points="6 9 12 15 18 9"/></svg>
            </div>
            <div v-if="expandedReport" class="p-4 md:p-6 prose-sm max-w-none text-(--color-text-primary) leading-relaxed" v-html="renderMarkdown(currentReview.summary)" />
          </div>

          <!-- Input Stats (collapsible debug) -->
          <div v-if="currentReview.input_stats && Object.keys(currentReview.input_stats).length" class="text-xs text-(--color-text-muted) flex flex-wrap gap-3 px-1">
            <span v-for="(val, key) in currentReview.input_stats" :key="key">
              {{ key }}: <span class="text-(--color-text-secondary) font-mono">{{ val }}</span>
            </span>
          </div>
        </template>
      </template>

    </div><!-- /content -->
  </div>
</template>
