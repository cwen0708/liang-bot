import { onUnmounted, ref, type Ref } from 'vue'
import { useSupabase } from './useSupabase'
import type { RealtimeChannel } from '@supabase/supabase-js'

/**
 * Subscribe to Supabase Realtime INSERT events on a table.
 * Returns a reactive array that accumulates new rows.
 */
export function useRealtimeInserts<T extends { id: number }>(
  table: string,
  opts: { limit?: number } = {},
): { rows: Ref<T[]>; channel: RealtimeChannel } {
  const supabase = useSupabase()
  const rows = ref<T[]>([]) as Ref<T[]>
  const limit = opts.limit ?? 200

  const channel = supabase
    .channel(`realtime:${table}`)
    .on(
      'postgres_changes',
      { event: 'INSERT', schema: 'public', table },
      (payload) => {
        rows.value = [payload.new as T, ...rows.value].slice(0, limit)
      },
    )
    .subscribe()

  onUnmounted(() => {
    supabase.removeChannel(channel)
  })

  return { rows, channel }
}

/**
 * Fetch initial data + subscribe to realtime updates.
 */
export function useRealtimeTable<T extends { id: number }>(
  table: string,
  opts: {
    limit?: number
    orderBy?: string
    ascending?: boolean
    filter?: { column: string; value: string }
  } = {},
) {
  const supabase = useSupabase()
  const rows = ref<T[]>([]) as Ref<T[]>
  const loading = ref(true)
  const limit = opts.limit ?? 50
  const orderBy = opts.orderBy ?? 'created_at'

  // Initial fetch
  async function fetch() {
    loading.value = true
    let query = supabase
      .from(table)
      .select('*')
      .order(orderBy, { ascending: opts.ascending ?? false })
      .limit(limit)

    if (opts.filter) {
      query = query.eq(opts.filter.column, opts.filter.value)
    }

    const { data } = await query
    if (data) rows.value = data as T[]
    loading.value = false
  }

  fetch()

  // Realtime subscription
  const channel = supabase
    .channel(`rt:${table}:${opts.filter?.value ?? 'all'}`)
    .on(
      'postgres_changes',
      { event: '*', schema: 'public', table },
      (payload) => {
        if (payload.eventType === 'INSERT') {
          rows.value = [payload.new as T, ...rows.value].slice(0, limit)
        } else if (payload.eventType === 'UPDATE') {
          const idx = rows.value.findIndex(
            (r) => (r as { id: number }).id === (payload.new as { id: number }).id,
          )
          if (idx >= 0) rows.value[idx] = payload.new as T
        } else if (payload.eventType === 'DELETE') {
          rows.value = rows.value.filter(
            (r) => (r as { id: number }).id !== (payload.old as { id: number }).id,
          )
        }
      },
    )
    .subscribe()

  onUnmounted(() => {
    supabase.removeChannel(channel)
  })

  return { rows, loading, refetch: fetch }
}
