<script setup lang="ts">
import { RouterLink } from 'vue-router'

const items = [
  { to: '/trading', label: '行情', icon: 'trading', desc: 'K 線圖與持倉詳情' },
  { to: '/futures', label: '合約', icon: 'futures', desc: '永續合約持倉與保證金' },
  { to: '/logs', label: '日誌', icon: 'logs', desc: '即時 Bot 日誌串流' },
  { to: '/config', label: '設定', icon: 'config', desc: 'Bot 配置管理' },
]
</script>

<template>
  <div class="p-4 md:p-6 space-y-4">
    <h2 class="text-2xl md:text-3xl font-bold">更多</h2>
    <div class="space-y-2">
      <RouterLink
        v-for="item in items"
        :key="item.to"
        :to="item.to"
        class="flex items-center gap-4 p-4 rounded-xl bg-(--color-bg-card) border border-(--color-border) hover:border-(--color-accent)/50 transition-colors"
      >
        <div class="w-10 h-10 rounded-lg bg-(--color-accent)/10 flex items-center justify-center shrink-0">
          <svg class="w-5 h-5 text-(--color-accent)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <template v-if="item.icon === 'trading'">
              <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" /><polyline points="16 7 22 7 22 13" />
            </template>
            <template v-else-if="item.icon === 'futures'">
              <path d="M2 17l6-6 4 4 10-10" /><path d="M22 7h-6" /><path d="M2 7l6 6 4-4 10 10" /><path d="M22 17h-6" />
            </template>
            <template v-else-if="item.icon === 'logs'">
              <line x1="4" y1="6" x2="20" y2="6" /><line x1="4" y1="10" x2="20" y2="10" /><line x1="4" y1="14" x2="16" y2="14" /><line x1="4" y1="18" x2="12" y2="18" />
            </template>
            <template v-else-if="item.icon === 'config'">
              <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </template>
          </svg>
        </div>
        <div>
          <div class="font-semibold text-(--color-text-primary)">{{ item.label }}</div>
          <div class="text-sm text-(--color-text-secondary)">{{ item.desc }}</div>
        </div>
        <svg class="w-5 h-5 ml-auto text-(--color-text-secondary)" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
      </RouterLink>
    </div>
  </div>
</template>
