import { useRef, useState } from 'react'
import { Play, Upload, ExternalLink } from 'lucide-react'
import {
  ResponsiveContainer, AreaChart, Area,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from 'recharts'

interface BacktestStats {
  total_trades: number
  winning_trades: number
  losing_trades: number
  win_rate: number
  avg_win: string
  avg_loss: string
  profit_factor: number
  total_pnl: string
  max_drawdown: string
  sharpe_ratio: number
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
}

type DataSource = 'alpaca' | 'upload'

const SYMBOLS = ['SPY', 'AAPL', 'MSFT', 'NVDA', 'QQQ', 'TSLA', 'AMZN', 'META']

function StatCard({
  label, value, sub, positive,
}: { label: string; value: string; sub?: string; positive?: boolean }) {
  const color = positive === undefined ? 'text-slate-100'
    : positive ? 'text-green-400' : 'text-red-400'
  return (
    <div className="bg-slate-800 rounded-xl p-4 border border-slate-700">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-xl font-bold ${color}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  )
}

function formatDate(ts: number) {
  return new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function fmtUsd(val: string) {
  const n = parseFloat(val)
  return (n >= 0 ? '+' : '') + n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
    timeZone: 'America/New_York',
  })
}

export default function BacktestPanel() {
  const today = new Date()
  const thirtyDaysAgo = new Date(today)
  thirtyDaysAgo.setDate(today.getDate() - 30)

  const [source, setSource] = useState<DataSource>('upload')

  // Alpaca mode
  const [symbol, setSymbol] = useState('SPY')
  const [startDate, setStartDate] = useState(thirtyDaysAgo.toISOString().slice(0, 10))
  const [endDate, setEndDate] = useState(today.toISOString().slice(0, 10))

  // Upload mode
  const [uploadSymbol, setUploadSymbol] = useState('SPY')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [lookbackDays, setLookbackDays] = useState(20)
  const [longOnly, setLongOnly] = useState(false)
  const [trendMa, setTrendMa] = useState(0)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Shared
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BacktestResult | null>(null)

  async function runAlpaca() {
    setLoading(true); setError(null); setResult(null)
    try {
      const res = await fetch('/api/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, start_date: startDate, end_date: endDate }),
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

  const pnlPositive  = result ? parseFloat(result.stats.total_pnl) >= 0 : true
  const equityStart  = result?.equity_curve[0]?.equity ?? 100000
  const equityColor  = pnlPositive ? '#4ade80' : '#f87171'
  const isDaily      = result?.strategy_used.includes('daily') ?? false

  return (
    <div className="space-y-4">
      {/* ── Controls ─────────────────────────────────────────────────────── */}
      <div className="bg-slate-900 rounded-xl border border-slate-700 p-4 space-y-4">
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

        {/* ── Upload mode ────────────────────────────────────────────────── */}
        {source === 'upload' && (
          <div className="space-y-3">
            {/* Stooq quick-download links */}
            <div className="rounded-lg bg-slate-800 border border-slate-600 p-3 text-xs text-slate-400 space-y-2">
              <p className="text-slate-300 font-semibold">Free historical data — no account needed</p>
              <div className="space-y-1 text-slate-400">
                <p>
                  <span className="text-slate-300 font-medium">Daily bars</span>
                  {' '}— any stock, years of history. Click to download:
                </p>
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
              <div className="space-y-1 text-slate-400 pt-1 border-t border-slate-700">
                <p>
                  <span className="text-slate-300 font-medium">1-minute bars</span>
                  {' '}— last ~5–10 trading days only (for ORB strategy):
                </p>
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
                The strategy is chosen automatically: daily files → N-day Donchian Breakout · 1-minute files → ORB
              </p>
            </div>

            <div className="flex flex-wrap gap-3 items-end">
              {/* Symbol */}
              <div>
                <label className="text-xs text-slate-500 block mb-1">Symbol</label>
                <input
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-28 focus:outline-none focus:border-blue-500 uppercase"
                  value={uploadSymbol}
                  onChange={e => setUploadSymbol(e.target.value.toUpperCase())}
                  placeholder="SPY"
                />
              </div>

              {/* Lookback */}
              <div>
                <label className="text-xs text-slate-500 block mb-1">
                  Lookback days
                  <span className="ml-1 text-slate-600">(daily strategy)</span>
                </label>
                <input
                  type="number"
                  min={2} max={200} step={1}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500"
                  value={lookbackDays}
                  onChange={e => setLookbackDays(Math.max(2, parseInt(e.target.value) || 20))}
                />
              </div>

              {/* Trend MA filter */}
              <div>
                <label className="text-xs text-slate-500 block mb-1">
                  Trend filter MA
                  <span className="ml-1 text-slate-600">(0 = off)</span>
                </label>
                <input
                  type="number"
                  min={0} max={500} step={1}
                  className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 w-24 focus:outline-none focus:border-blue-500"
                  value={trendMa}
                  onChange={e => setTrendMa(Math.max(0, parseInt(e.target.value) || 0))}
                />
              </div>

              {/* Long only */}
              <div className="flex flex-col justify-end pb-0.5">
                <label className="flex items-center gap-2 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={longOnly}
                    onChange={e => setLongOnly(e.target.checked)}
                    className="w-4 h-4 accent-blue-500"
                  />
                  <span className="text-sm text-slate-300 font-medium">Long only</span>
                </label>
                <p className="text-xs text-slate-600 mt-0.5 ml-6">No short trades</p>
              </div>

              {/* File picker */}
              <div>
                <label className="text-xs text-slate-500 block mb-1">CSV or Excel file</label>
                <div className="flex items-center gap-2">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".csv,.xlsx,.xls"
                    className="hidden"
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
                    <span className="text-xs text-slate-500">
                      {(uploadFile.size / 1024).toFixed(0)} KB
                    </span>
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
          </div>
        )}

        {/* ── Alpaca mode ─────────────────────────────────────────────────── */}
        {source === 'alpaca' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="text-xs text-slate-500 block mb-1">Symbol</label>
                <select
                  value={symbol}
                  onChange={e => setSymbol(e.target.value)}
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
              <button
                onClick={() => void runAlpaca()}
                disabled={loading}
                className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:opacity-50 transition-colors"
              >
                <Play size={14} />
                {loading ? 'Running…' : 'Run Backtest'}
              </button>
            </div>
            <p className="text-xs text-slate-600">
              Requires an Alpaca SIP data subscription. Max range: 180 days.
            </p>
          </div>
        )}

        {error && (
          <p className="text-xs text-red-400 bg-red-900/20 border border-red-800/40 rounded-lg px-3 py-2 whitespace-pre-wrap">
            {error}
          </p>
        )}
      </div>

      {loading && (
        <div className="bg-slate-900 rounded-xl border border-slate-700 p-10 text-center text-slate-500 text-sm animate-pulse">
          Running strategy…
        </div>
      )}

      {result && (
        <>
          {/* Strategy badge */}
          <div className="flex items-center gap-2">
            <span className={`text-xs font-semibold px-2.5 py-1 rounded-full border ${
              isDaily
                ? 'bg-purple-900/40 text-purple-300 border-purple-700/50'
                : 'bg-blue-900/40 text-blue-300 border-blue-700/50'
            }`}>
              {result.strategy_used}
            </span>
          </div>

          {/* ── Stats cards ──────────────────────────────────────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <StatCard label="Total Trades" value={String(result.stats.total_trades)} />
            <StatCard
              label="Win Rate"
              value={`${result.stats.win_rate}%`}
              sub={`${result.stats.winning_trades}W / ${result.stats.losing_trades}L`}
              positive={result.stats.win_rate >= 50}
            />
            <StatCard
              label="Total P&L"
              value={fmtUsd(result.stats.total_pnl)}
              positive={parseFloat(result.stats.total_pnl) >= 0}
            />
            <StatCard
              label="Profit Factor"
              value={result.stats.profit_factor === Infinity ? '∞' : String(result.stats.profit_factor)}
              sub="gross profit / gross loss"
              positive={result.stats.profit_factor >= 1}
            />
            <StatCard label="Max Drawdown" value={`-$${result.stats.max_drawdown}`} positive={false} />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Avg Win"  value={fmtUsd(result.stats.avg_win)}  positive={true} />
            <StatCard label="Avg Loss" value={`-$${result.stats.avg_loss}`}  positive={false} />
            <StatCard
              label="Sharpe Ratio"
              value={String(result.stats.sharpe_ratio)}
              sub="annualised, daily returns"
              positive={result.stats.sharpe_ratio >= 0}
            />
            <StatCard
              label="Period"
              value={`${result.start_date} → ${result.end_date}`}
              sub={result.symbol}
            />
          </div>

          {/* ── Equity curve ─────────────────────────────────────────────── */}
          <div className="bg-slate-900 rounded-xl border border-slate-700 p-4">
            <h3 className="text-sm font-semibold text-slate-200 mb-3">Backtest Equity Curve</h3>
            {result.equity_curve.length === 0 ? (
              <p className="text-slate-600 text-sm text-center py-8">No trades in this period.</p>
            ) : (
              <div style={{ height: 240 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={result.equity_curve} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="btGrad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor={equityColor} stopOpacity={0.2} />
                        <stop offset="95%" stopColor={equityColor} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" tickFormatter={formatDate}
                      tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={40} />
                    <YAxis tickFormatter={v => `$${((v as number) / 1000).toFixed(0)}k`}
                      tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} width={52} domain={['auto', 'auto']} />
                    <Tooltip
                      contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
                      labelFormatter={v => formatDate(v as number)}
                      formatter={(v: number) => [v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }), 'Equity']}
                    />
                    <ReferenceLine y={equityStart} stroke="#475569" strokeDasharray="4 2" />
                    <Area type="monotone" dataKey="equity" stroke={equityColor} strokeWidth={2}
                      fill="url(#btGrad)" dot={false} activeDot={{ r: 4 }} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>

          {/* ── Trade log ────────────────────────────────────────────────── */}
          <div className="bg-slate-900 rounded-xl border border-slate-700 overflow-hidden">
            <div className="px-4 py-3 border-b border-slate-700">
              <h3 className="text-sm font-semibold text-slate-200">Trade Log ({result.trades.length} trades)</h3>
            </div>
            <div className="overflow-auto max-h-80">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-slate-500 uppercase tracking-wider">
                    <th className="px-4 py-2 text-left">Dir</th>
                    <th className="px-4 py-2 text-left">Entry</th>
                    <th className="px-4 py-2 text-right">Entry $</th>
                    <th className="px-4 py-2 text-left">Exit</th>
                    <th className="px-4 py-2 text-right">Exit $</th>
                    <th className="px-4 py-2 text-right">Qty</th>
                    <th className="px-4 py-2 text-center">Reason</th>
                    <th className="px-4 py-2 text-right">P&L</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {result.trades.map((t, i) => {
                    const pnl = parseFloat(t.pnl)
                    return (
                      <tr key={i} className="hover:bg-slate-800/40">
                        <td className={`px-4 py-2 font-semibold ${t.direction === 'BUY' ? 'text-green-400' : 'text-red-400'}`}>{t.direction}</td>
                        <td className="px-4 py-2 text-slate-400">{fmtTime(t.entry_time)}</td>
                        <td className="px-4 py-2 text-right text-slate-300">${t.entry_price}</td>
                        <td className="px-4 py-2 text-slate-400">{t.exit_time ? fmtTime(t.exit_time) : '—'}</td>
                        <td className="px-4 py-2 text-right text-slate-300">{t.exit_price ? `$${t.exit_price}` : '—'}</td>
                        <td className="px-4 py-2 text-right text-slate-400">{t.qty}</td>
                        <td className="px-4 py-2 text-center text-slate-500">{t.exit_reason}</td>
                        <td className={`px-4 py-2 text-right font-semibold ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          {pnl >= 0 ? '+' : ''}${Math.abs(pnl).toFixed(2)}
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
