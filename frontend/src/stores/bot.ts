import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import type { BotStatus, Position, MarketSnapshot } from '@/types'

export const useBotStore = defineStore('bot', () => {
  const supabase = useSupabase()

  const status = ref<BotStatus | null>(null)
  const positions = ref<Position[]>([])
  const latestPrices = ref<Record<string, number>>({})

  const isOnline = computed(() => {
    if (!status.value) return false
    const updatedAt = new Date(status.value.updated_at).getTime()
    const now = Date.now()
    // 5 分鐘沒更新視為離線（AI 審核借款可能需要較長時間）
    return now - updatedAt < 300_000
  })

  async function fetchStatus() {
    const { data } = await supabase
      .from('bot_status')
      .select('*')
      .order('updated_at', { ascending: false })
      .limit(1)
    if (data?.[0]) status.value = data[0] as BotStatus
  }

  async function fetchPositions() {
    const { data } = await supabase
      .from('positions')
      .select('*')
      .order('updated_at', { ascending: false })
    if (data) positions.value = data as Position[]
  }

  async function fetchLatestPrices() {
    // Get most recent snapshot per symbol
    const { data } = await supabase
      .from('market_snapshots')
      .select('symbol, price')
      .order('created_at', { ascending: false })
      .limit(10)
    if (data) {
      const seen = new Set<string>()
      for (const row of data as MarketSnapshot[]) {
        if (!seen.has(row.symbol)) {
          latestPrices.value[row.symbol] = row.price
          seen.add(row.symbol)
        }
      }
    }
  }

  // Subscribe to realtime updates
  function subscribeRealtime() {
    supabase
      .channel('store:bot_status')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'bot_status' }, (p) => {
        status.value = p.new as BotStatus
      })
      .subscribe()

    supabase
      .channel('store:positions')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'positions' }, (p) => {
        if (p.eventType === 'DELETE') {
          positions.value = positions.value.filter((pos) => pos.id !== (p.old as Position).id)
        } else {
          const idx = positions.value.findIndex((pos) => pos.id === (p.new as Position).id)
          if (idx >= 0) {
            positions.value[idx] = p.new as Position
          } else {
            positions.value.unshift(p.new as Position)
          }
        }
      })
      .subscribe()

    supabase
      .channel('store:market_snapshots')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'market_snapshots' }, (p) => {
        const snap = p.new as MarketSnapshot
        latestPrices.value[snap.symbol] = snap.price
      })
      .subscribe()
  }

  return {
    status,
    positions,
    latestPrices,
    isOnline,
    fetchStatus,
    fetchPositions,
    fetchLatestPrices,
    subscribeRealtime,
  }
})
