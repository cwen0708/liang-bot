<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useBotStore } from '@/stores/bot'
import { useTheme } from '@/composables/useTheme'

const bot = useBotStore()
const { isDark, toggleTheme } = useTheme()
const mobileMenuOpen = ref(false)

onMounted(() => {
  // Prices cached in localStorage, so all fetches can run in parallel
  bot.fetchStatus()
  bot.fetchPositions()
  bot.fetchLatestPrices()
  bot.fetchBalances()
  bot.fetchLoans()
  bot.fetchFuturesMargin()
  bot.fetchFuturesFunding()
  bot.subscribeRealtime()
})

const navItems = [
  { to: '/', label: '總覽', icon: 'dashboard' },
  { to: '/trading', label: '行情', icon: 'trading' },
  { to: '/orders', label: '現貨', icon: 'orders' },
  { to: '/futures', label: '合約', icon: 'futures' },
  { to: '/strategy', label: '策略', icon: 'strategy' },
  { to: '/loan-guard', label: '借貸', icon: 'shield' },
  { to: '/logs', label: '日誌', icon: 'logs' },
  { to: '/config', label: '設定', icon: 'config' },
]

// 手機底部 tab
const mobileTabItems = [
  { to: '/', label: '總覽', icon: 'dashboard' },
  { to: '/orders', label: '現貨', icon: 'orders' },
  { to: '/futures', label: '合約', icon: 'futures' },
  { to: '/strategy', label: '策略', icon: 'strategy' },
  { to: '/more', label: '更多', icon: 'more' },
]
</script>

<template>
  <div class="flex min-h-screen flex-col md:flex-row">
    <!-- Desktop Sidebar -->
    <aside class="hidden md:flex w-56 bg-(--color-bg-secondary) border-r border-(--color-border) flex-col shrink-0 h-screen sticky top-0">
      <div class="p-4 border-b border-(--color-border)">
        <h1 class="text-xl font-bold text-(--color-accent)">Liang Bot</h1>
        <!-- Global Live / Paper toggle -->
        <div class="inline-flex rounded-lg bg-(--color-bg-primary) p-0.5 mt-2 w-full">
          <button
            class="flex-1 px-3 py-1 rounded-md text-xs font-medium transition-colors"
            :class="bot.globalMode === 'live'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="bot.setGlobalMode('live')"
          >Live</button>
          <button
            class="flex-1 px-3 py-1 rounded-md text-xs font-medium transition-colors"
            :class="bot.globalMode === 'paper'
              ? 'bg-(--color-bg-card) text-(--color-accent) shadow-sm'
              : 'text-(--color-text-secondary) hover:text-(--color-text-primary)'"
            @click="bot.setGlobalMode('paper')"
          >Paper</button>
        </div>
      </div>

      <nav class="flex-1 p-2 space-y-1">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 px-3 py-2 rounded-lg text-base text-(--color-text-secondary) hover:bg-(--color-bg-card) hover:text-(--color-text-primary) transition-colors"
          active-class="!bg-(--color-bg-card) !text-(--color-accent)"
        >
          <svg class="w-5 h-5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <!-- Dashboard -->
            <template v-if="item.icon === 'dashboard'">
              <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
            </template>
            <!-- Trading -->
            <template v-else-if="item.icon === 'trading'">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" />
            </template>
            <!-- Futures -->
            <template v-else-if="item.icon === 'futures'">
              <path d="M2 17l6-6 4 4 10-10" /><path d="M22 7h-6" /><path d="M2 7l6 6 4-4 10 10" /><path d="M22 17h-6" />
            </template>
            <!-- Orders -->
            <template v-else-if="item.icon === 'orders'">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="16" y2="17" />
            </template>
            <!-- Strategy -->
            <template v-else-if="item.icon === 'strategy'">
              <circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="3" /><line x1="12" y1="2" x2="12" y2="5" /><line x1="12" y1="19" x2="12" y2="22" />
            </template>
            <!-- Shield -->
            <template v-else-if="item.icon === 'shield'">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </template>
            <!-- Config -->
            <template v-else-if="item.icon === 'config'">
              <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </template>
            <!-- Logs -->
            <template v-else-if="item.icon === 'logs'">
              <line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="10" x2="20" y2="10" /><line x1="4" y1="14" x2="16" y2="14" /><line x1="4" y1="18" x2="12" y2="18" />
            </template>
          </svg>
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="p-4 border-t border-(--color-border) text-sm text-(--color-text-secondary)">
        <div class="flex items-center justify-between">
          <div class="flex flex-col gap-0.5">
            <div v-if="bot.status">
              運行 {{ Math.floor((bot.status.uptime_sec ?? 0) / 60) }} 分
            </div>
            <div class="flex items-center gap-1.5">
              <span
                class="w-2 h-2 rounded-full"
                :class="bot.isOnline ? 'bg-(--color-success)' : 'bg-(--color-danger)'"
              />
              <span class="text-xs">
                {{ bot.isOnline ? '運行中' : '離線' }}
                <template v-if="bot.status?.cycle_num">
                  &middot; 第 {{ bot.status.cycle_num }} 輪
                </template>
              </span>
            </div>
          </div>
          <button
            @click="toggleTheme"
            class="p-1.5 rounded-lg hover:bg-(--color-bg-card) transition-colors text-(--color-text-secondary) hover:text-(--color-text-primary)"
            :title="isDark ? '切換到淺色模式' : '切換到深色模式'"
          >
            <svg v-if="isDark" class="w-4.5 h-4.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
            </svg>
            <svg v-else class="w-4.5 h-4.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <!-- Mobile Header -->
    <header class="md:hidden flex items-center justify-between px-4 py-2.5 bg-(--color-bg-secondary) border-b border-(--color-border) sticky top-0 z-40">
      <div class="flex items-center gap-2">
        <h1 class="text-base font-bold text-(--color-accent)">Liang Bot</h1>
        <span
          class="w-2 h-2 rounded-full"
          :class="bot.isOnline ? 'bg-(--color-success)' : 'bg-(--color-danger)'"
        />
      </div>
      <div class="flex items-center gap-2">
        <!-- Mobile Live/Paper toggle -->
        <div class="inline-flex rounded-lg bg-(--color-bg-primary) p-0.5">
          <button
            class="px-2 py-1 rounded-md text-xs font-medium transition-colors"
            :class="bot.globalMode === 'live'
              ? 'bg-(--color-bg-card) text-(--color-text-primary) shadow-sm'
              : 'text-(--color-text-secondary)'"
            @click="bot.setGlobalMode('live')"
          >Live</button>
          <button
            class="px-2 py-1 rounded-md text-xs font-medium transition-colors"
            :class="bot.globalMode === 'paper'
              ? 'bg-(--color-bg-card) text-(--color-accent) shadow-sm'
              : 'text-(--color-text-secondary)'"
            @click="bot.setGlobalMode('paper')"
          >Paper</button>
        </div>
        <button
          @click="toggleTheme"
          class="p-1.5 rounded-lg hover:bg-(--color-bg-card) transition-colors text-(--color-text-secondary)"
          :title="isDark ? '切換到淺色模式' : '切換到深色模式'"
        >
          <svg v-if="isDark" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </svg>
          <svg v-else class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
        </button>
      </div>
    </header>

    <!-- Main Content -->
    <main class="flex-1 overflow-auto pb-20 md:pb-0">
      <RouterView />
    </main>

    <!-- Mobile Bottom Tab Bar -->
    <nav class="md:hidden fixed bottom-0 left-0 right-0 z-40 bg-(--color-bg-secondary) border-t border-(--color-border) safe-bottom">
      <div class="flex justify-around items-center">
        <RouterLink
          v-for="item in mobileTabItems"
          :key="item.to"
          :to="item.to"
          class="flex flex-col items-center py-2 px-1 min-w-0 flex-1 text-(--color-text-secondary) transition-colors"
          active-class="!text-(--color-accent)"
          @click="mobileMenuOpen = false"
        >
          <svg class="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <template v-if="item.icon === 'dashboard'">
              <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" />
            </template>
            <template v-else-if="item.icon === 'orders'">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /><line x1="8" y1="13" x2="16" y2="13" /><line x1="8" y1="17" x2="16" y2="17" />
            </template>
            <template v-else-if="item.icon === 'futures'">
              <path d="M2 17l6-6 4 4 10-10" /><path d="M22 7h-6" /><path d="M2 7l6 6 4-4 10 10" /><path d="M22 17h-6" />
            </template>
            <template v-else-if="item.icon === 'strategy'">
              <circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="3" /><line x1="12" y1="2" x2="12" y2="5" /><line x1="12" y1="19" x2="12" y2="22" />
            </template>
            <template v-else-if="item.icon === 'shield'">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </template>
            <template v-else-if="item.icon === 'more'">
              <circle cx="12" cy="5" r="1.5" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" /><circle cx="12" cy="19" r="1.5" fill="currentColor" stroke="none" />
            </template>
          </svg>
          <span class="text-xs mt-0.5 truncate w-full text-center">{{ item.label }}</span>
        </RouterLink>
      </div>
    </nav>
  </div>
</template>
