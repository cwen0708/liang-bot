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
    .limit(14)
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

// Trend: last 7 reviews' overall score
const trendData = computed(() =>
  [...reviews.value]
    .slice(0, 7)
    .reverse()
    .map(r => ({
      date: r.review_date,
      score: r.scores?.overall ?? 0,
    })),
)

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
  <div class="p-4 md:p-6 flex flex-col gap-4 md:gap-5 md:h-[calc(100vh)] md:overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between shrink-0">
      <h2 class="text-2xl md:text-3xl font-bold">復盤</h2>
      <div class="text-sm text-(--color-text-secondary)">
        透過 <code class="px-1.5 py-0.5 bg-(--color-bg-secondary) rounded text-xs">/review</code> 產生報告
      </div>
    </div>

    <!-- Scrollable -->
    <div class="flex flex-col gap-4 md:gap-5 min-h-0 flex-1 overflow-auto">

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

          <!-- Score Trend (last 7) -->
          <div v-if="trendData.length > 1" class="bg-(--color-bg-card) border border-(--color-border) rounded-xl p-4 shadow-sm dark:shadow-none">
            <h3 class="font-semibold text-base mb-3">總分趨勢</h3>
            <div class="flex items-end gap-1 h-20">
              <div
                v-for="(pt, i) in trendData"
                :key="i"
                class="flex-1 rounded-t transition-all relative group"
                :class="scoreColor(pt.score)"
                :style="{ height: Math.max(pt.score * 100, 4) + '%' }"
              >
                <div class="absolute -top-5 left-1/2 -translate-x-1/2 text-[10px] font-mono text-(--color-text-secondary) opacity-0 group-hover:opacity-100 transition-opacity whitespace-nowrap">
                  {{ (pt.score * 100).toFixed(0) }}
                </div>
              </div>
            </div>
            <div class="flex justify-between text-[10px] text-(--color-text-muted) mt-1.5">
              <span v-if="trendData.length">{{ trendData[0]?.date }}</span>
              <span v-if="trendData.length > 1">{{ trendData[trendData.length - 1]?.date }}</span>
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

    </div><!-- /scrollable -->
  </div>
</template>
