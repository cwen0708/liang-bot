// Supabase table types

export interface BotConfig {
  id: number
  version: number
  config_json: Record<string, unknown>
  changed_by: string
  change_note: string
  created_at: string
}

export interface StrategyVerdict {
  id: number
  symbol: string
  strategy: string
  signal: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  reasoning: string
  cycle_id: string
  created_at: string
}

export interface LLMDecision {
  id: number
  symbol: string
  action: 'BUY' | 'SELL' | 'HOLD'
  confidence: number
  reasoning: string
  model: string
  cycle_id: string
  created_at: string
}

export interface Order {
  id: number
  symbol: string
  side: 'buy' | 'sell'
  order_type: string
  quantity: number
  price: number
  filled: number
  status: string
  exchange_id: string
  source: string
  mode: 'paper' | 'live'
  cycle_id: string
  created_at: string
}

export interface Position {
  id: number
  symbol: string
  quantity: number
  entry_price: number
  current_price: number
  unrealized_pnl: number
  stop_loss: number | null
  take_profit: number | null
  mode: 'paper' | 'live'
  updated_at: string
}

export interface LoanHealth {
  id: number
  loan_coin: string
  collateral_coin: string
  ltv: number
  total_debt: number
  collateral_amount: number
  action_taken: string
  created_at: string
}

export interface BotLog {
  id: number
  level: string
  module: string
  message: string
  created_at: string
}

export interface MarketSnapshot {
  id: number
  symbol: string
  price: number
  created_at: string
}

export interface BotStatus {
  id: number
  cycle_num: number
  status: 'running' | 'paused' | 'error'
  config_ver: number
  pairs: string[]
  uptime_sec: number
  updated_at: string
}

export interface AccountBalance {
  id: number
  currency: string
  free: number
  usdt_value: number
  snapshot_id: string
  created_at: string
}
