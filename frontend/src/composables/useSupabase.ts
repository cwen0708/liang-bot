import { createClient, type SupabaseClient } from '@supabase/supabase-js'

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string
const supabaseKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string

let client: SupabaseClient | null = null

export function useSupabase(): SupabaseClient {
  if (!client) {
    client = createClient(supabaseUrl, supabaseKey)
  }
  return client
}
