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
  desired?: boolean
  stopped_unexpectedly?: boolean
}

export interface DonchianPosition {
  symbol: string
  direction: string
  entry_price: number
  entry_date: string
  stop_price: number
  channel_low: number
  channel_high: number
  trailing_active: boolean
  qty: number
  pending_exit: boolean
}

export interface DonchianState {
  positions: DonchianPosition[]
  queued_entries: Record<string, string>
  queued_exits: string[]
  queued_date: string
  pending_reanchor: string[]
  ran_eod_date: string
  ran_open_date: string
}

export interface BotStatusMap {
  bots: Record<string, BotStatus>
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

export interface ConfigDonchian {
  lookback_days: number
  trend_ma: number
  trailing_activation_pct: number
  trailing_pct: number
  long_only: boolean
}

export interface ConfigTrendSR {
  bar_minutes: number
  ma_fast: number
  ma_slow: number
  regime_ma: number
  pivot_lookback: number
  pivot_strength: number
  atr_period: number
  atr_mult: number
  breakout_buffer_atr: number
  cooldown_bars: number
  trailing_activation_pct: number
  trailing_pct: number
  long_only: boolean
  min_adx: number
  adx_period: number
  volume_mult: number
  volume_ma: number
}

export type AssetClass = 'stock' | 'crypto'

export interface Config {
  live: boolean
  asset_class: AssetClass
  symbols: string[]
  risk: ConfigRisk
  strategy: {
    name: string
    orb: ConfigOrb
    ema: ConfigEma
    donchian: ConfigDonchian
    trend_sr: ConfigTrendSR
  }
}

// ── Profiles ───────────────────────────────────────────────────────────────────

export interface ProfileSummary {
  slug: string
  name: string
  asset_class: AssetClass
  live: boolean
  symbols: string[]
  strategy: string
  has_keys: boolean
  active: boolean
}
