<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useBotStore } from '@/stores/bot'

const bot = useBotStore()

onMounted(() => {
  bot.fetchStatus()
  bot.fetchPositions()
  bot.fetchLatestPrices()
  bot.subscribeRealtime()
})

const navItems = [
  { to: '/', label: '總覽' },
  { to: '/trading', label: '交易' },
  { to: '/orders', label: '訂單' },
  { to: '/strategy', label: '策略' },
  { to: '/loan-guard', label: '借貸監控' },
  { to: '/config', label: '設定' },
  { to: '/logs', label: '日誌' },
]
</script>

<template>
  <div class="flex min-h-screen">
    <!-- Sidebar -->
    <aside class="w-56 bg-(--color-bg-secondary) border-r border-(--color-border) flex flex-col">
      <div class="p-4 border-b border-(--color-border)">
        <h1 class="text-lg font-bold text-(--color-accent)">Spot Bot</h1>
        <div class="flex items-center gap-2 mt-1 text-xs">
          <span
            class="w-2 h-2 rounded-full"
            :class="bot.isOnline ? 'bg-(--color-success)' : 'bg-(--color-danger)'"
          />
          <span class="text-(--color-text-secondary)">
            {{ bot.isOnline ? '運行中' : '離線' }}
            <template v-if="bot.status?.cycle_num">
              &middot; 第 {{ bot.status.cycle_num }} 輪
            </template>
          </span>
        </div>
      </div>

      <nav class="flex-1 p-2 space-y-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-(--color-text-secondary) hover:bg-(--color-bg-card) hover:text-(--color-text-primary) transition-colors"
          active-class="!bg-(--color-bg-card) !text-(--color-accent)"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="p-4 border-t border-(--color-border) text-xs text-(--color-text-secondary)">
        <div v-if="bot.status">
          運行時間: {{ Math.floor((bot.status.uptime_sec ?? 0) / 60) }} 分鐘
        </div>
      </div>
    </aside>

    <!-- Main Content -->
    <main class="flex-1 overflow-auto">
      <RouterView />
    </main>
  </div>
</template>
