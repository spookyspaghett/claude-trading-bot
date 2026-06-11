import { useRef, useState } from 'react'
import { Play, Upload, ExternalLink, TrendingDown, Download, ChevronDown, ChevronUp } from 'lucide-react'
import {
  ResponsiveContainer,
  AreaChart, Area,
  BarChart, Bar, Cell,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'

interface BacktestStats {
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number       // already ×100 from the API (e.g. 52.3)
  avg_win: string
  avg_loss: string
  profit_factor: number
  total_pnl: string
  max_drawdown: string
  sharpe_ratio: number
  avg_hold_days: number
}

interface BacktestTrade {
  symbol: string
  direction: string
  entry_time: string
  entry_price: string
  exit_time: string | null
  exit_price: string | null
  exit_reason: string
  qty: string
  pnl: string
}

interface BacktestResult {
  symbol: string
  start_date: string
  end_date: string
  strategy_used: string
  stats: BacktestStats
  equity_curve: { timestamp: number; equity: number }[]
  trades: BacktestTrade[]
  report_file?: string
}

type DataSource = 'alpaca' | 'upload'

const SYMBOLS = ['SPY', 'AAPL', 'MSFT', 'NVDA', 'QQQ', 'TSLA', 'AMZN', 'META']

// ── Formatters ────────────────────────────────────────────────────────────────

function fmtUsd(val: string | number, signed = false) {
  const n = typeof val === 'string' ? parseFloat(val) : val
  const abs = Math.abs(n).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  if (!signed) return (n < 0 ? '-' : '') + abs
  return (n >= 0 ? '+' : '-') + abs
}

function fmtPrice(val: string | null | undefined) {
  if (!val) return '—'
  return `$${parseFloat(val).toFixed(2)}`
}

function fmtPct(n: number) {
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}

function formatDate(ts: number) {
  const d = new Date(ts * 1000)
  return `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} '${String(d.getFullYear()).slice(2)}`
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/New_York',
  })
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({
  label, value, sub, positive, large,
}: { label: string; value: string; sub?: string; positive?: boolean; large?: boolean }) {
  const color = positive === undefined ? 'text-slate-100'
    : positive ? 'text-green-400' : 'text-red-400'
  return (
    <div className="bg-slate-800 rounded-xl p-4 border border-slate-700/80">
      <p className="text-xs text-slate-500 mb-1.5 uppercase tracking-wide">{label}</p>
      <p className={`font-bold ${large ? 'text-2xl' : 'text-xl'} ${color} tabular-nums`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function BacktestPanel() {
  const today = new Date()
  const thirtyDaysAgo = new Date(today)
  thirtyDaysAgo.setDate(today.getDate() - 30)

  const [source, setSource] = useState<DataSource>('upload')

  const [symbol, setSymbol] = useState('SPY')
  const [startDate, setStartDate] = useState(thirtyDaysAgo.toISOString().slice(0, 10))
  const [endDate, setEndDate] = useState(today.toISOString().slice(0, 10))

  const [uploadSymbol, setUploadSymbol] = useState('SPY')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [lookbackDays, setLookbackDays] = useState(40)
  const [longOnly, setLongOnly] = useState(false)
  const [trendMa, setTrendMa] = useState(0)
  const [fastMa, setFastMa] = useState(50)
  const [useAtrStop, setUseAtrStop] = useState(true)
  const [atrPeriod, setAtrPeriod] = useState(14)
  const [atrMultiplier, setAtrMultiplier] = useState(1.5)
  const [volumeFilterDays, setVolumeFilterDays] = useState(20)
  const [trailingActivationPct, setTrailingActivationPct] = useState(2.0)
  const [trailingPct, setTrailingPct] = useState(8.0)
  const [startingEquity, setStartingEquity] = useState(500000)
  const [strategy, setStrategy] = useState<'auto' | 'trend_sr' | 'ema' | 'vwap_revert'>('auto')
  const [slippageBps, setSlippageBps] = useState(0)
  const [commission, setCommission] = useState(0)
  const [exitLookback, setExitLookback] = useState(0)
  const [maFast, setMaFast] = useState(21)
  const [maSlow, setMaSlow] = useState(55)
  const [pivotLookback, setPivotLookback] = useState(20)
  const [pivotStrength, setPivotStrength] = useState(3)
  const [minAdx, setMinAdx] = useState(0)
  const [volumeMult, setVolumeMult] = useState(0)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BacktestResult | null>(null)

  async function runAlpaca() {
    setLoading(true); setError(null); setResult(null)
    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, start_date: startDate, end_date: endDate, starting_equity: startingEquity }),
      })
      if (!res.ok) {
        const data = await res.json() as { detail?: string }
        throw new Error(data.detail ?? `HTTP ${res.status}`)
      }
      setResult(await res.json() as BacktestResult)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  async function runUpload() {
    if (!uploadFile) { setError('Please select a CSV or Excel file first.'); return }
    setLoading(true); setError(null); setResult(null)
    try {
      const form = new FormData()
      form.append('file', uploadFile)
      form.append('symbol', uploadSymbol.trim().toUpperCase())
      form.append('lookback_days', String(lookbackDays))
      form.append('long_only', String(longOnly))
      form.append('trend_ma', String(trendMa))
      form.append('fast_ma', String(fastMa))
      form.append('use_atr_stop', String(useAtrStop))
      form.append('atr_period', String(atrPeriod))
      form.append('atr_multiplier', String(atrMultiplier))
      form.append('volume_filter_days', String(volumeFilterDays))
      form.append('trailing_activation_pct', String(trailingActivationPct))
      form.append('trailing_pct', String(trailingPct))
      form.append('starting_equity', String(startingEquity))
      form.append('strategy', strategy)
      form.append('slippage_bps', String(slippageBps))
      form.append('commission', String(commission))
      form.append('ma_fast', String(maFast))
      form.append('ma_slow', String(maSlow))
      form.append('pivot_lookback', String(pivotLookback))
      form.append('pivot_strength', String(pivotStrength))
      form.append('min_adx', String(minAdx))
      form.append('volume_mult', String(volumeMult))
      form.append('exit_lookback', String(exitLookback))
      const res = await fetch('/api/backtest/upload', { method: 'POST', body: form })
      if (!res.ok) {
        const data = await res.json() as { detail?: string }
        throw new Error(data.detail ?? `HTTP ${res.status}`)
      }
      setResult(await res.json() as BacktestResult)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  // ── Derived chart data ─────────────────────────────────────────────────────

  const equityStart = result?.equity_curve[0]?.equity ?? startingEquity
  const equityEnd   = result?.equity_curve[result.equity_curve.length - 1]?.equity ?? equityStart
  const returnPct   = equityStart > 0 ? ((equityEnd - equityStart) / equityStart * 100) : 0
  const pnlPositive = returnPct >= 0
  const equityColor = pnlPositive ? '#4ade80' : '#f87171'
  const isDaily     = result?.strategy_used.includes('daily') ?? false

  // Equity + drawdown overlay data
  const equityWithDd = result ? (() => {
    let peak = result.equity_curve[0]?.equity ?? equityStart
    return result.equity_curve.map(p => {
      peak = Math.max(peak, p.equity)
      const dd = peak > 0 ? ((p.equity - peak) / peak * 100) : 0
      return { ...p, drawdown: dd }
    })
  })() : []

  // Per-trade P&L bars
  const tradeBarData = result?.trades.map((t, i) => ({
    trade: i + 1,
    pnl: parseFloat(t.pnl),
    label: `#${i + 1} ${t.symbol} ${t.direction} @ ${fmtPrice(t.entry_price)}`,
  })) ?? []

  function downloadReport() {
    if (!result) return
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href     = url
    a.download = result.report_file ?? `backtest_${result.symbol}_${result.start_date}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-4">
      {/* ── Controls ──────────────────────────────────────────────────────── */}
      <div className="card p-4 space-y-4">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <h2 className="text-sm font-semibold text-slate-200">Backtest</h2>
          <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5">
            <button
              onClick={() => setSource('upload')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                source === 'upload' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Upload size={12} />
              Upload CSV / Excel
            </button>
            <button
              onClick={() => setSource('alpaca')}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                source === 'alpaca' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Alpaca SIP
            </button>
          </div>
        </div>

        {source === 'upload' && (
          <div className="space-y-3">
            <div className="rounded-lg bg-slate-800 border border-slate-600 p-3 text-xs text-slate-400 space-y-2">
              <p className="text-slate-300 font-semibold">Free historical data — no account needed</p>
              <div className="space-y-1">
                <p><span className="text-slate-300 font-medium">Daily bars</span> — any stock, years of history:</p>
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {[['SPY','spy'],['AAPL','aapl'],['MSFT','msft'],['NVDA','nvda'],['QQQ','qqq'],['TSLA','tsla']].map(([label, s]) => (
                    <a key={label}
                      href={`https://stooq.com/q/d/l/?s=${s}.us`}
                      target="_blank" rel="noopener noreferrer"
                      className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 border border-slate-600 text-blue-400 hover:text-blue-300 flex items-center gap-1 transition-colors"
                    >
                      {label} <ExternalLink size={9} />
                    </a>
                  ))}
                </div>
              </div>
              <div className="space-y-1 pt-1 border-t border-slate-700">
                <p><span className="text-slate-300 font-medium">1-minute bars</span> — last ~5–10 trading days (ORB strategy):</p>
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {[['SPY','spy'],['AAPL','aapl'],['MSFT','msft'],['NVDA','nvda']].map(([label, s]) => (
                    <a key={label}
                      href={`https://stooq.com/q/d/l/?s=${s}.us&i=1`}
                      target="_blank" rel="noopener noreferrer"
                      className="px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 border border-slate-600 text-blue-400 hover:text-blue-300 flex items-center gap-1 transition-colors"
                    >
                      {label} 1-min <ExternalLink size={9} />
                    </a>
                  ))}
                </div>
              </div>
              <p className="text-slate-500 pt-1 border-t border-slate-700">
                Auto: daily files → Donchian Breakout · 1-minute files → ORB. Or force Trend/SR below.
              </p>
            </div>

            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="text-xs text-slate-500 block mb-1">Strategy</label>
                <select value={strategy} onChange={e => setStrategy(e.target.value as 'auto' | 'trend_sr' | 'ema' | 'vwap_revert')}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500">
                  <option value="auto">Auto (Donchian / ORB)</option>
                  <option value="trend_sr">Trend/SR (crypto)</option>
                  <option value="ema">EMA crossover</option>
                  <option value="vwap_revert">VWAP mean-reversion</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Slippage (bps)</label>
                <input type="number" min={0} step={1} value={slippageBps}
                  onChange={e => setSlippageBps(Math.max(0, parseFloat(e.target.value) || 0))}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Commission ($/fill)</label>
                <input type="number" min={0} step={0.1} value={commission}
                  onChange={e => setCommission(Math.max(0, parseFloat(e.target.value) || 0))}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-28 focus:outline-none focus:border-blue-500" />
              </div>
              {/* Fast/Slow MA — used by Trend/SR (trend filter) and EMA (the crossover itself) */}
              {(strategy === 'trend_sr' || strategy === 'ema') && (
                <>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Fast MA {strategy === 'ema' && <span className="text-slate-600">(fast EMA)</span>}
                    </label>
                    <input type="number" min={2} step={1} value={maFast}
                      onChange={e => setMaFast(Math.max(2, parseInt(e.target.value) || 21))}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Slow MA {strategy === 'ema' && <span className="text-slate-600">(slow EMA)</span>}
                    </label>
                    <input type="number" min={3} step={1} value={maSlow}
                      onChange={e => setMaSlow(Math.max(3, parseInt(e.target.value) || 55))}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
                  </div>
                </>
              )}
              {/* Pivot / ADX / volume — Trend/SR only */}
              {strategy === 'trend_sr' && (
                <>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Pivot lookback</label>
                    <input type="number" min={2} step={1} value={pivotLookback}
                      onChange={e => setPivotLookback(Math.max(2, parseInt(e.target.value) || 20))}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Pivot strength</label>
                    <input type="number" min={1} step={1} value={pivotStrength}
                      onChange={e => setPivotStrength(Math.max(1, parseInt(e.target.value) || 3))}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Min ADX <span className="text-slate-600">(0 = off)</span>
                    </label>
                    <input type="number" min={0} max={100} step={1} value={minAdx}
                      onChange={e => setMinAdx(Math.max(0, parseInt(e.target.value) || 0))}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
                    <p className="text-[10px] text-slate-600 mt-0.5">skip weak trends (20-25)</p>
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Volume × <span className="text-slate-600">(0 = off)</span>
                    </label>
                    <input type="number" min={0} max={10} step={0.1} value={volumeMult}
                      onChange={e => setVolumeMult(Math.max(0, parseFloat(e.target.value) || 0))}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500" />
                    <p className="text-[10px] text-slate-600 mt-0.5">vol ≥ N× avg (1.2-1.5)</p>
                  </div>
                </>
              )}
              {/* VWAP — no per-run knobs beyond Long only; bands come from the profile config */}
              {strategy === 'vwap_revert' && (
                <div className="flex items-end">
                  <p className="text-[11px] text-slate-500 max-w-xs leading-relaxed">
                    Fades 2σ moves off the session VWAP, exits back at VWAP with a 3.5σ stop.
                    Only <span className="text-slate-300">Long only</span> is tunable here; band/stop widths use defaults.
                  </p>
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="text-xs text-slate-500 block mb-1">Symbol</label>
                <input
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-28 focus:outline-none focus:border-blue-500 uppercase"
                  value={uploadSymbol}
                  onChange={e => setUploadSymbol(e.target.value.toUpperCase())}
                  placeholder="SPY"
                />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">
                  Lookback days <span className="text-slate-600">(daily)</span>
                </label>
                <input type="number" min={2} max={200} step={1}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500"
                  value={lookbackDays}
                  onChange={e => setLookbackDays(Math.max(2, parseInt(e.target.value) || 20))}
                />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">
                  Trend MA <span className="text-slate-600">(0 = off)</span>
                </label>
                <input type="number" min={0} max={500} step={1}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500"
                  value={trendMa}
                  onChange={e => setTrendMa(Math.max(0, parseInt(e.target.value) || 0))}
                />
              </div>
              {strategy === 'auto' && (
                <div>
                  <label className="text-xs text-slate-500 block mb-1">
                    Exit lookback <span className="text-slate-600">(0 = off)</span>
                  </label>
                  <input type="number" min={0} max={200} step={1}
                    className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500"
                    value={exitLookback}
                    onChange={e => setExitLookback(Math.max(0, parseInt(e.target.value) || 0))}
                  />
                  <p className="text-[10px] text-slate-600 mt-0.5">Donchian: exit on N-day channel</p>
                </div>
              )}
              <div className="flex flex-col justify-end pb-0.5">
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input type="checkbox" checked={longOnly} onChange={e => setLongOnly(e.target.checked)}
                    className="w-4 h-4 accent-blue-500"
                  />
                  <span className="text-sm text-slate-300 font-medium">Long only</span>
                </label>
                <p className="text-xs text-slate-600 mt-0.5 ml-6">No short trades</p>
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Starting equity ($)</label>
                <input type="number" min={100} step={100}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-32 focus:outline-none focus:border-blue-500"
                  value={startingEquity}
                  onChange={e => setStartingEquity(Math.max(100, parseFloat(e.target.value) || 500000))}
                />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">CSV or Excel file</label>
                <div className="flex items-center gap-2">
                  <input ref={fileInputRef} type="file" accept=".csv,.xlsx,.xls" className="hidden"
                    onChange={e => { setUploadFile(e.target.files?.[0] ?? null); setResult(null) }}
                  />
                  <button
                    onClick={() => fileInputRef.current?.click()}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 border border-slate-600 transition-colors"
                  >
                    <Upload size={13} />
                    {uploadFile ? uploadFile.name : 'Choose file…'}
                  </button>
                  {uploadFile && (
                    <span className="text-xs text-slate-500">{(uploadFile.size / 1024).toFixed(0)} KB</span>
                  )}
                </div>
              </div>
              <button
                onClick={() => void runUpload()}
                disabled={loading || !uploadFile}
                className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:opacity-50 transition-colors"
              >
                <Play size={14} />
                {loading ? 'Running…' : 'Run Backtest'}
              </button>
            </div>

            {/* ── Advanced settings ────────────────────────────────────────── */}
            <div className="border-t border-slate-700/60 pt-3">
              <button
                onClick={() => setShowAdvanced(v => !v)}
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-300 transition-colors"
              >
                {showAdvanced ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                Advanced settings
              </button>
              {showAdvanced && (
                <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                  {/* Fast MA */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Fast MA <span className="text-slate-600">(0 = off)</span>
                    </label>
                    <input type="number" min={0} max={500} step={1}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-full focus:outline-none focus:border-blue-500"
                      value={fastMa}
                      onChange={e => setFastMa(Math.max(0, parseInt(e.target.value) || 0))}
                    />
                    <p className="text-[10px] text-slate-600 mt-0.5">e.g. 50-day</p>
                  </div>

                  {/* Volume filter */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Volume filter <span className="text-slate-600">(0 = off)</span>
                    </label>
                    <input type="number" min={0} max={200} step={1}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-full focus:outline-none focus:border-blue-500"
                      value={volumeFilterDays}
                      onChange={e => setVolumeFilterDays(Math.max(0, parseInt(e.target.value) || 0))}
                    />
                    <p className="text-[10px] text-slate-600 mt-0.5">avg vol lookback days</p>
                  </div>

                  {/* ATR stop toggle */}
                  <div className="flex flex-col justify-between">
                    <label className="text-xs text-slate-500 block mb-1">ATR stop</label>
                    <label className="flex items-center gap-2 cursor-pointer select-none mt-1">
                      <input type="checkbox" checked={useAtrStop} onChange={e => setUseAtrStop(e.target.checked)}
                        className="w-4 h-4 accent-blue-500"
                      />
                      <span className="text-sm text-slate-300 font-medium">Use ATR stop</span>
                    </label>
                    <p className="text-[10px] text-slate-600 mt-0.5">instead of fixed %</p>
                  </div>

                  {/* ATR period */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">ATR period</label>
                    <input type="number" min={2} max={50} step={1}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-full focus:outline-none focus:border-blue-500"
                      value={atrPeriod}
                      disabled={!useAtrStop}
                      onChange={e => setAtrPeriod(Math.max(2, parseInt(e.target.value) || 14))}
                    />
                    <p className="text-[10px] text-slate-600 mt-0.5">days for ATR calc</p>
                  </div>

                  {/* ATR multiplier */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">ATR multiplier</label>
                    <input type="number" min={0.1} max={10} step={0.1}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-full focus:outline-none focus:border-blue-500"
                      value={atrMultiplier}
                      disabled={!useAtrStop}
                      onChange={e => setAtrMultiplier(Math.max(0.1, parseFloat(e.target.value) || 1.5))}
                    />
                    <p className="text-[10px] text-slate-600 mt-0.5">stop = ATR × this</p>
                  </div>

                  {/* Trailing activation */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">
                      Trail activate % <span className="text-slate-600">(0 = off)</span>
                    </label>
                    <input type="number" min={0} max={50} step={0.5}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-full focus:outline-none focus:border-blue-500"
                      value={trailingActivationPct}
                      onChange={e => setTrailingActivationPct(Math.max(0, parseFloat(e.target.value) || 0))}
                    />
                    <p className="text-[10px] text-slate-600 mt-0.5">profit % before trailing kicks in</p>
                  </div>

                  {/* Trailing stop % */}
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Trailing stop %</label>
                    <input type="number" min={0} max={50} step={0.5}
                      className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-full focus:outline-none focus:border-blue-500"
                      value={trailingPct}
                      disabled={trailingActivationPct <= 0}
                      onChange={e => setTrailingPct(Math.max(0, parseFloat(e.target.value) || 0))}
                    />
                    <p className="text-[10px] text-slate-600 mt-0.5">% below peak to trail</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {source === 'alpaca' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="text-xs text-slate-500 block mb-1">Symbol</label>
                <select value={symbol} onChange={e => setSymbol(e.target.value)}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                >
                  {SYMBOLS.map(s => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Start date</label>
                <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">End date</label>
                <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500" />
              </div>
              <div>
                <label className="text-xs text-slate-500 block mb-1">Starting equity ($)</label>
                <input type="number" min={100} step={100}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-32 focus:outline-none focus:border-blue-500"
                  value={startingEquity}
                  onChange={e => setStartingEquity(Math.max(100, parseFloat(e.target.value) || 500000))}
                />
              </div>
              <button
                onClick={() => void runAlpaca()}
                disabled={loading}
                className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:opacity-50 transition-colors"
              >
                <Play size={14} />
                {loading ? 'Running…' : 'Run Backtest'}
              </button>
            </div>
            <p className="text-xs text-slate-600">Requires Alpaca IEX data. Max range: 180 days.</p>
          </div>
        )}

        {error && (
          <p className="text-xs text-red-400 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2 whitespace-pre-wrap">
            {error}
          </p>
        )}
      </div>

      {loading && (
        <div className="card p-10 text-center text-slate-500 text-sm animate-pulse">
          Running strategy…
        </div>
      )}

      {result && (
        <>
          {/* Strategy badge + return summary + download */}
          <div className="flex items-center gap-3 flex-wrap justify-between">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${
                isDaily
                  ? 'bg-purple-900/40 text-purple-300 border-purple-700/50'
                  : 'bg-blue-900/40 text-blue-300 border-blue-700/50'
              }`}>
                {result.strategy_used}
              </span>
              <span className={`text-sm font-bold tabular-nums ${pnlPositive ? 'text-green-400' : 'text-red-400'}`}>
                {fmtPct(returnPct)} return on {fmtUsd(startingEquity)}
              </span>
            </div>
            <button
              onClick={downloadReport}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-700 hover:bg-slate-600 border border-slate-600 text-slate-200 transition-colors"
              title="Download report JSON — upload to Claude for analysis"
            >
              <Download size={12} />
              Download for Claude
            </button>
          </div>

          {/* ── Stats grid ────────────────────────────────────────────────── */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
            <StatCard
              label="Net Return"
              value={fmtPct(returnPct)}
              sub={`${fmtUsd(result.stats.total_pnl, true)} on ${fmtUsd(startingEquity)}`}
              positive={pnlPositive}
              large
            />
            <StatCard
              label="Win Rate"
              value={`${result.stats.win_rate}%`}
              sub={`${result.stats.winning_trades}W / ${result.stats.losing_trades}L of ${result.stats.total_trades}`}
              positive={result.stats.win_rate >= 50}
            />
            <StatCard
              label="Profit Factor"
              value={result.stats.profit_factor >= 999 ? '∞' : result.stats.profit_factor.toFixed(2)}
              sub="gross profit ÷ gross loss"
              positive={result.stats.profit_factor >= 1}
            />
            <StatCard
              label="Sharpe Ratio"
              value={result.stats.sharpe_ratio.toFixed(2)}
              sub="annualised, daily rets"
              positive={result.stats.sharpe_ratio >= 1}
            />
            <StatCard
              label="Max Drawdown"
              value={`-${fmtUsd(result.stats.max_drawdown)}`}
              positive={false}
            />
            <StatCard
              label="Avg Win / Loss"
              value={`${fmtUsd(result.stats.avg_win, true)}`}
              sub={`Loss: -${fmtUsd(result.stats.avg_loss)}`}
              positive={parseFloat(result.stats.avg_win) >= parseFloat(result.stats.avg_loss)}
            />
            <StatCard
              label="Avg Hold"
              value={result.stats.avg_hold_days < 1
                ? `${Math.round(result.stats.avg_hold_days * 24)}h`
                : `${result.stats.avg_hold_days.toFixed(1)}d`}
              sub="avg days per trade"
            />
          </div>

          {/* ── Equity curve + drawdown overlay ───────────────────────────── */}
          <div className="card p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-slate-200">Equity Curve</h3>
              <span className="text-xs text-slate-500">Starting {fmtUsd(startingEquity)}</span>
            </div>
            {result.equity_curve.length === 0 ? (
              <p className="text-slate-600 text-sm text-center py-8">No trades in this period.</p>
            ) : (
              <div style={{ height: 300 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={equityWithDd} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor={equityColor} stopOpacity={0.25} />
                        <stop offset="95%" stopColor={equityColor} stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" tickFormatter={formatDate}
                      tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={40} />
                    <YAxis yAxisId="equity"
                      tickFormatter={v => `$${((v as number) / 1000).toFixed(0)}k`}
                      tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} width={52} domain={['auto', 'auto']} />
                    <YAxis yAxisId="dd" orientation="right"
                      tickFormatter={v => `${(v as number).toFixed(0)}%`}
                      tick={{ fill: '#94a3b8', fontSize: 10 }} tickLine={false} axisLine={false} width={38}
                      domain={['auto', 0]}
                    />
                    <Tooltip
                      contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8 }}
                      labelFormatter={v => formatDate(v as number)}
                      formatter={(v: number, name: string) => {
                        if (name === 'drawdown') return [`${v.toFixed(2)}%`, 'Drawdown']
                        return [v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }), 'Equity']
                      }}
                    />
                    <ReferenceLine yAxisId="equity" y={equityStart} stroke="#475569" strokeDasharray="4 2"
                      label={{ value: 'Start', fill: '#475569', fontSize: 10, position: 'insideTopRight' }}
                    />
                    <Area yAxisId="equity" type="monotone" dataKey="equity"
                      stroke={equityColor} strokeWidth={2} fill="url(#btGrad)" dot={false} activeDot={{ r: 4 }} />
                    <Area yAxisId="dd" type="monotone" dataKey="drawdown"
                      stroke="#f97316" strokeWidth={1} fill="#f9731615" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
            {/* Drawdown legend */}
            <div className="flex items-center gap-4 mt-2 text-xs text-slate-500">
              <span className="flex items-center gap-1.5">
                <span className="w-3 h-0.5 rounded" style={{ background: equityColor, display: 'inline-block' }} />
                Equity
              </span>
              <span className="flex items-center gap-1.5">
                <TrendingDown size={11} className="text-orange-400" />
                <span>Drawdown % (right axis)</span>
              </span>
            </div>
          </div>

          {/* ── Per-trade P&L bar chart ────────────────────────────────────── */}
          {tradeBarData.length > 0 && (
            <div className="card p-4">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-slate-200">Trade P&amp;L — per trade</h3>
                <span className="text-xs text-slate-500">{tradeBarData.length} trades</span>
              </div>
              <div style={{ height: 180 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={tradeBarData} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
                    <XAxis dataKey="trade" tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false}
                      label={{ value: 'Trade #', fill: '#475569', fontSize: 10, position: 'insideBottom', offset: -2 }}
                    />
                    <YAxis tickFormatter={v => `$${(v as number).toFixed(0)}`}
                      tick={{ fill: '#64748b', fontSize: 10 }} tickLine={false} axisLine={false} width={56} />
                    <Tooltip
                      contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8 }}
                      labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                      formatter={(v: number, _name: string, props: { payload?: { label?: string } }) => [
                        v.toLocaleString('en-US', { style: 'currency', currency: 'USD' }),
                        props.payload?.label ?? 'P&L',
                      ]}
                    />
                    <ReferenceLine y={0} stroke="#475569" strokeWidth={1} />
                    <Bar dataKey="pnl" radius={[2, 2, 0, 0]} maxBarSize={24}>
                      {tradeBarData.map((entry, i) => (
                        <Cell key={i} fill={entry.pnl >= 0 ? '#4ade80' : '#f87171'} fillOpacity={0.85} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* ── Trade log ─────────────────────────────────────────────────── */}
          <div className="card overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-200">Trade Log</h3>
              <span className="text-xs text-slate-500">{result.trades.length} trades</span>
            </div>
            <div className="overflow-auto max-h-80">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-900 z-10">
                  <tr className="text-slate-500 uppercase tracking-wider border-b border-slate-800">
                    <th className="px-4 py-2 text-left">#</th>
                    <th className="px-4 py-2 text-left">Dir</th>
                    <th className="px-4 py-2 text-left">Entry time</th>
                    <th className="px-4 py-2 text-right">Entry $</th>
                    <th className="px-4 py-2 text-left">Exit time</th>
                    <th className="px-4 py-2 text-right">Exit $</th>
                    <th className="px-4 py-2 text-right">Qty</th>
                    <th className="px-4 py-2 text-center">Exit</th>
                    <th className="px-4 py-2 text-right">P&amp;L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {result.trades.map((t, i) => {
                    const pnl = parseFloat(t.pnl)
                    return (
                      <tr key={i} className="hover:bg-slate-800/40 transition-colors">
                        <td className="px-4 py-2 text-slate-600 tabular-nums">{i + 1}</td>
                        <td className={`px-4 py-2 font-bold ${t.direction === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>
                          {t.direction}
                        </td>
                        <td className="px-4 py-2 text-slate-400 tabular-nums">{fmtTime(t.entry_time)}</td>
                        <td className="px-4 py-2 text-right text-slate-300 tabular-nums">{fmtPrice(t.entry_price)}</td>
                        <td className="px-4 py-2 text-slate-400 tabular-nums">{t.exit_time ? fmtTime(t.exit_time) : '—'}</td>
                        <td className="px-4 py-2 text-right text-slate-300 tabular-nums">{fmtPrice(t.exit_price)}</td>
                        <td className="px-4 py-2 text-right text-slate-400 tabular-nums">{t.qty}</td>
                        <td className="px-4 py-2 text-center">
                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${
                            t.exit_reason === 'stop' ? 'bg-red-900/40 text-red-400' :
                            t.exit_reason === 'trail' ? 'bg-orange-900/40 text-orange-400' :
                            t.exit_reason === 'eod' || t.exit_reason === 'eod_forced' ? 'bg-slate-800 text-slate-400' :
                            t.exit_reason === 'channel' ? 'bg-purple-900/40 text-purple-400' :
                            'bg-slate-800 text-slate-500'
                          }`}>
                            {t.exit_reason}
                          </span>
                        </td>
                        <td className={`px-4 py-2 text-right font-bold tabular-nums ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {pnl >= 0 ? '+' : '-'}${Math.abs(pnl).toFixed(2)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
