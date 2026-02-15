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
  signal: 'BUY' | 'SELL' | 'HOLD' | 'SHORT' | 'COVER'
  confidence: number
  reasoning: string
  cycle_id: string
  market_type: 'spot' | 'futures' | 'tx'
  timeframe: string
  mode: 'paper' | 'live'
  created_at: string
}

export interface LLMDecision {
  id: number
  symbol: string
  action: 'BUY' | 'SELL' | 'HOLD' | 'SHORT' | 'COVER'
  confidence: number
  reasoning: string
  model: string
  cycle_id: string
  market_type: 'spot' | 'futures' | 'tx'
  executed: boolean
  reject_reason?: string
  entry_price?: number | null
  stop_loss?: number | null
  take_profit?: number | null
  mode: 'paper' | 'live'
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
  market_type: 'spot' | 'futures' | 'tx'
  position_side: 'long' | 'short' | null
  leverage: number
  reduce_only: boolean
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
  side: 'long' | 'short'
  leverage: number
  liquidation_price: number | null
  market_type: 'spot' | 'futures' | 'tx'
  margin_type: 'cross' | 'isolated'
  entry_horizon: string
  entry_reasoning: string
}

export interface FuturesFunding {
  id: number
  symbol: string
  funding_rate: number
  funding_fee: number
  position_size: number
  mode: 'paper' | 'live'
  created_at: string
}

export interface FuturesMargin {
  id: number
  total_wallet_balance: number
  available_balance: number
  total_unrealized_pnl: number
  total_margin_balance: number
  margin_ratio: number
  mode: 'paper' | 'live'
  created_at: string
}

export interface LoanHealth {
  id: number
  loan_coin: string
  collateral_coin: string
  ltv: number
  total_debt: number
  collateral_amount: number
  action_taken: string
  mode: 'paper' | 'live'
  created_at: string
}

export interface LoanAdjustHistory {
  id: number
  loan_coin: string
  collateral_coin: string
  direction: 'ADDITIONAL' | 'REDUCED'
  amount: number
  pre_ltv: number
  after_ltv: number
  adjust_time: string
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
  mode: 'paper' | 'live'
  created_at: string
}

export interface BotStatus {
  id: number
  cycle_num: number
  status: 'running' | 'running_futures' | 'paused' | 'error'
  config_ver: number
  pairs: string[]
  uptime_sec: number
  mode: 'paper' | 'live'
  updated_at: string
}

export interface AccountBalance {
  id: number
  currency: string
  free: number
  usdt_value: number
  snapshot_id: string
  mode: 'paper' | 'live'
  created_at: string
}

export interface ReviewScores {
  strategy_accuracy: number
  risk_execution: number
  pnl_performance: number
  prompt_quality: number
  overall: number
}

export interface ReviewSuggestion {
  category: 'strategy' | 'risk' | 'config' | 'prompt'
  priority: 'high' | 'medium' | 'low'
  title: string
  detail: string
  action: string
}

export interface DailyReview {
  id: number
  review_date: string
  market_type: string
  mode: 'paper' | 'live'
  model: string
  summary: string
  scores: ReviewScores
  suggestions: ReviewSuggestion[]
  input_stats: Record<string, number | string>
  created_at: string
}
