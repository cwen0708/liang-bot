import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useSupabase } from '@/composables/useSupabase'
import type { AccountBalance, BotStatus, Position, MarketSnapshot, LoanHealth } from '@/types'

export const useBotStore = defineStore('bot', () => {
  const supabase = useSupabase()

  const status = ref<BotStatus | null>(null)
  const positions = ref<Position[]>([])
  const latestPrices = ref<Record<string, number>>({})
  const balances = ref<AccountBalance[]>([])
  const totalUsdt = ref(0)
  const loans = ref<LoanHealth[]>([])
  const pricesReady = ref(false)

  const isOnline = computed(() => {
    if (!status.value) return false
    const updatedAt = new Date(status.value.updated_at).getTime()
    const now = Date.now()
    // 5 分鐘沒更新視為離線（AI 審核借款可能需要較長時間）
    return now - updatedAt < 300_000
  })

  const netLoanValue = computed<number | null>(() => {
    if (!pricesReady.value || !loans.value.length) return null
    return loans.value.reduce((sum, loan) => {
      const priceKey = loan.collateral_coin + '/USDT'
      const price = latestPrices.value[priceKey] ?? 0
      if (price === 0) return sum // skip if price not available
      const collateralUsdt = loan.collateral_amount * price
      return sum + (collateralUsdt - loan.total_debt)
    }, 0)
  })

  const totalAssets = computed<number | null>(() => {
    if (netLoanValue.value === null) return null
    return totalUsdt.value + netLoanValue.value
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
    pricesReady.value = true
  }

  async function fetchBalances() {
    // Get latest snapshot_id
    const { data: latest } = await supabase
      .from('account_balances')
      .select('snapshot_id')
      .order('created_at', { ascending: false })
      .limit(1)
    if (!latest?.length) return

    const snapId = (latest[0] as AccountBalance).snapshot_id
    const { data } = await supabase
      .from('account_balances')
      .select('*')
      .eq('snapshot_id', snapId)
      .order('usdt_value', { ascending: false })
    if (data) {
      balances.value = data as AccountBalance[]
      totalUsdt.value = balances.value.reduce((s, b) => s + b.usdt_value, 0)
    }
  }

  async function fetchLoans() {
    const { data } = await supabase
      .from('loan_health')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(20)
    if (data) {
      const seen = new Set<string>()
      const unique: LoanHealth[] = []
      for (const row of data as LoanHealth[]) {
        const key = `${row.collateral_coin}/${row.loan_coin}`
        if (!seen.has(key)) {
          unique.push(row)
          seen.add(key)
        }
      }
      loans.value = unique
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

    supabase
      .channel('store:account_balances')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'account_balances' }, () => {
        fetchBalances()
      })
      .subscribe()

    supabase
      .channel('store:loan_health')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'loan_health' }, () => {
        fetchLoans()
      })
      .subscribe()
  }

  return {
    status,
    positions,
    latestPrices,
    pricesReady,
    balances,
    totalUsdt,
    loans,
    netLoanValue,
    totalAssets,
    isOnline,
    fetchStatus,
    fetchPositions,
    fetchLatestPrices,
    fetchBalances,
    fetchLoans,
    subscribeRealtime,
  }
})
