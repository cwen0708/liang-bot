<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useBotStore } from '@/stores/bot'

const bot = useBotStore()
const mobileMenuOpen = ref(false)

onMounted(() => {
  bot.fetchStatus()
  bot.fetchPositions()
  bot.fetchLatestPrices()
  bot.subscribeRealtime()
})

const navItems = [
  { to: '/', label: '總覽', icon: '📊' },
  { to: '/trading', label: '交易', icon: '📈' },
  { to: '/orders', label: '訂單', icon: '📋' },
  { to: '/strategy', label: '策略', icon: '🎯' },
  { to: '/loan-guard', label: '借貸', icon: '🛡️' },
  { to: '/config', label: '設定', icon: '⚙️' },
  { to: '/logs', label: '日誌', icon: '📝' },
]
</script>

<template>
  <div class="flex min-h-screen flex-col md:flex-row">
    <!-- Desktop Sidebar (hidden on mobile) -->
    <aside class="hidden md:flex w-56 bg-(--color-bg-secondary) border-r border-(--color-border) flex-col shrink-0">
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

    <!-- Mobile Header -->
    <header class="md:hidden flex items-center justify-between px-4 py-3 bg-(--color-bg-secondary) border-b border-(--color-border) sticky top-0 z-40">
      <div class="flex items-center gap-2">
        <h1 class="text-base font-bold text-(--color-accent)">Spot Bot</h1>
        <span
          class="w-2 h-2 rounded-full"
          :class="bot.isOnline ? 'bg-(--color-success)' : 'bg-(--color-danger)'"
        />
      </div>
      <div class="text-xs text-(--color-text-secondary)">
        <template v-if="bot.status?.cycle_num">
          第 {{ bot.status.cycle_num }} 輪
        </template>
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
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex flex-col items-center py-2 px-1 min-w-0 flex-1 text-(--color-text-secondary) transition-colors"
          active-class="!text-(--color-accent)"
          @click="mobileMenuOpen = false"
        >
          <span class="text-lg leading-none">{{ item.icon }}</span>
          <span class="text-[10px] mt-0.5 truncate w-full text-center">{{ item.label }}</span>
        </RouterLink>
      </div>
    </nav>
  </div>
</template>
