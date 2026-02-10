<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRealtimeTable } from '@/composables/useRealtime'
import type { BotLog } from '@/types'

const { rows: logs, loading } = useRealtimeTable<BotLog>('bot_logs', { limit: 200 })
const filterLevel = ref('')
const filterText = ref('')

const filteredLogs = computed(() => {
  let result = logs.value
  if (filterLevel.value) {
    result = result.filter((l) => l.level === filterLevel.value)
  }
  if (filterText.value) {
    const q = filterText.value.toLowerCase()
    result = result.filter(
      (l) => l.message.toLowerCase().includes(q) || l.module.toLowerCase().includes(q),
    )
  }
  // 按 created_at 降序（毫秒精度），避免同秒批次日誌順序錯亂
  return [...result].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
})

function levelColor(level: string) {
  switch (level) {
    case 'ERROR': return 'text-(--color-danger)'
    case 'WARNING': return 'text-(--color-warning)'
    case 'INFO': return 'text-(--color-accent)'
    case 'DEBUG': return 'text-(--color-text-secondary)'
    default: return 'text-(--color-text-primary)'
  }
}

function formatTime(ts: string) {
  const d = new Date(ts)
  const hms = d.toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${hms}.${ms}`
}
</script>

<template>
  <div class="p-4 md:p-6 space-y-4">
    <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
      <h2 class="text-xl md:text-2xl font-bold">日誌</h2>
      <div class="flex gap-2 w-full sm:w-auto">
        <select
          v-model="filterLevel"
          class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">全部</option>
          <option>ERROR</option>
          <option>WARNING</option>
          <option>INFO</option>
          <option>DEBUG</option>
        </select>
        <input
          v-model="filterText"
          placeholder="搜尋..."
          class="bg-(--color-bg-card) border border-(--color-border) rounded-lg px-3 py-1.5 text-sm flex-1 sm:w-48"
        />
      </div>
    </div>

    <div class="bg-(--color-bg-card) border border-(--color-border) rounded-xl overflow-hidden">
      <div v-if="loading" class="p-8 text-center text-(--color-text-secondary)">載入中...</div>

      <!-- Desktop: horizontal log lines -->
      <div v-else class="hidden md:block max-h-[calc(100vh-160px)] overflow-auto font-mono text-xs">
        <div
          v-for="log in filteredLogs"
          :key="log.id"
          class="flex gap-3 px-4 py-1 border-b border-(--color-border)/30 hover:bg-(--color-bg-secondary)/50"
        >
          <span class="text-(--color-text-secondary) shrink-0 w-24">{{ formatTime(log.created_at) }}</span>
          <span class="shrink-0 w-16 font-bold" :class="levelColor(log.level)">{{ log.level }}</span>
          <span class="text-(--color-text-secondary) shrink-0 w-24 truncate">{{ log.module }}</span>
          <span class="text-(--color-text-primary) break-all">{{ log.message }}</span>
        </div>
        <div v-if="!filteredLogs.length" class="p-8 text-center text-(--color-text-secondary)">
          無符合條件的日誌
        </div>
      </div>

      <!-- Mobile: stacked log entries -->
      <div v-if="!loading" class="md:hidden max-h-[calc(100vh-200px)] overflow-auto">
        <div
          v-for="log in filteredLogs"
          :key="log.id"
          class="px-3 py-2 border-b border-(--color-border)/30"
        >
          <div class="flex justify-between items-center">
            <span class="text-xs font-bold" :class="levelColor(log.level)">{{ log.level }}</span>
            <span class="text-[10px] text-(--color-text-secondary)">{{ formatTime(log.created_at) }} &middot; {{ log.module }}</span>
          </div>
          <div class="text-xs text-(--color-text-primary) mt-0.5 break-all">{{ log.message }}</div>
        </div>
        <div v-if="!filteredLogs.length" class="p-8 text-center text-(--color-text-secondary) text-sm">
          無符合條件的日誌
        </div>
      </div>
    </div>
  </div>
</template>
