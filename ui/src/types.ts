export interface Position {
  symbol: string
  qty: string
  side: string
  avg_entry_price: string
  current_price: string
  unrealized_pl: string
  unrealized_plpc: string
  market_value: string
}

export interface Account {
  equity: string
  portfolio_value: string
  buying_power: string
  cash: string
  daily_pnl: string
}

export interface BotStatus {
  running: boolean
  pid: number | null
}

export interface LogEvent {
  event: string
  timestamp: string
  level?: string
  symbol?: string
  direction?: string
  price?: string
  order_id?: string
  side?: string
  qty?: string
  filled_qty?: string
  filled_avg_price?: string
  reason?: string
  error?: string
  [key: string]: unknown
}

export interface EquityPoint {
  timestamp: number
  equity: number
  profit_loss: number
}

export interface PnLPoint {
  timestamp: number
  profit_loss: number
}

export interface ConfigRisk {
  max_position_usd: number
  stop_loss_pct: number
  daily_loss_limit_usd: number
  max_open_positions: number
}

export interface ConfigOrb {
  opening_range_minutes: number
  entry_order_type: string
  eod_exit_time: string
}

export interface ConfigEma {
  fast_period: number
  slow_period: number
  entry_order_type: string
  eod_exit_time: string
}

export interface Config {
  live: boolean
  symbols: string[]
  risk: ConfigRisk
  strategy: {
    name: string
    orb: ConfigOrb
    ema: ConfigEma
  }
}
