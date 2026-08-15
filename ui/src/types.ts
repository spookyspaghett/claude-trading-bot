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

/** Which asset classes each strategy can trade.
 *
 *  Mirrors STRATEGY_ASSETS in config_loader.py, which is the authority — the
 *  server rejects a mismatched pair whatever the UI offers. This copy exists so
 *  the pickers never present a choice that would be refused on save. */
export const STRATEGY_ASSETS: Record<string, AssetClass[]> = {
  // ORB is built around the 09:30 ET opening range, so it is stock-only.
  orb:         ['stock'],
  ema:         ['stock', 'crypto'],
  donchian:    ['stock', 'crypto'],
  trend_sr:    ['stock', 'crypto'],
  vwap_revert: ['stock', 'crypto'],
}

export const STRATEGY_LABELS: Record<string, string> = {
  orb:         'ORB',
  ema:         'EMA',
  donchian:    'Donchian',
  trend_sr:    'Trend/SR',
  vwap_revert: 'VWAP',
}

export function strategiesFor(asset: AssetClass): string[] {
  return Object.keys(STRATEGY_ASSETS).filter(n => STRATEGY_ASSETS[n].includes(asset))
}

export function strategySupports(name: string, asset: AssetClass): boolean {
  return (STRATEGY_ASSETS[name] ?? []).includes(asset)
}

export interface ConfigRisk {
  max_position_usd: number
  stop_loss_pct: number
  daily_loss_limit_usd: number
  max_open_positions: number
  /** % of equity risked entry→stop. 0 = flat max_position_usd sizing. */
  risk_per_trade_pct: number
  /** Caps on total open risk (Σ |entry−stop| × qty) as % of equity. 0 = off. */
  max_portfolio_heat_pct: number
  max_group_heat_pct: number
  /** Group label → symbols that move together. */
  correlation_groups: Record<string, string[]>
  max_gross_exposure_pct: number
  daily_loss_limit_pct: number
  /** Drawdown throttle: taper from derisk_start, stop opening at halt. */
  derisk_start_dd_pct: number
  halt_dd_pct: number
  min_risk_scale: number
}

export interface ConfigOrb {
  opening_range_minutes: number
  entry_order_type: string
  eod_exit_time: string
  buffer_pct?: number
  stop_mode?: string
  max_range_pct?: number
}

export interface ConfigEma {
  fast_period: number
  slow_period: number
  entry_order_type: string
  eod_exit_time: string
  min_separation_pct?: number
}

export interface ConfigDonchian {
  lookback_days: number
  trend_ma: number
  trailing_activation_pct: number
  trailing_pct: number
  long_only: boolean
  exit_lookback?: number
}

export interface ConfigVwap {
  band_mult: number
  stop_mult: number
  dev_window: number
  min_bars: number
  max_trades_per_day: number
  long_only: boolean
  eod_exit_time: string
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
    vwap_revert?: ConfigVwap
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
