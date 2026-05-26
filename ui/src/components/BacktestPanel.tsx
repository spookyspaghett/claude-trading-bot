import { useState } from 'react'
import { Play } from 'lucide-react'
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
  stats: BacktestStats
  equity_curve: { timestamp: number; equity: number }[]
  trades: BacktestTrade[]
}

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

  const [symbol, setSymbol] = useState('SPY')
  const [startDate, setStartDate] = useState(thirtyDaysAgo.toISOString().slice(0, 10))
  const [endDate, setEndDate] = useState(today.toISOString().slice(0, 10))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BacktestResult | null>(null)

  async function runBacktest() {
    setLoading(true)
    setError(null)
    setResult(null)
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
      const data = await res.json() as BacktestResult
      setResult(data)
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  const pnlPositive = result ? parseFloat(result.stats.total_pnl) >= 0 : true
  const equityStart = result?.equity_curve[0]?.equity ?? 100000
  const equityColor = pnlPositive ? '#4ade80' : '#f87171'

  return (
    <div className="space-y-4">
      {/* ── Controls ─────────────────────────────────────────────────────── */}
      <div className="bg-slate-900 rounded-xl border border-slate-700 p-4">
        <h2 className="text-sm font-semibold text-slate-200 mb-4">Backtest — ORB Strategy</h2>
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
            <input
              type="date"
              value={startDate}
              onChange={e => setStartDate(e.target.value)}
              className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500 block mb-1">End date</label>
            <input
              type="date"
              value={endDate}
              onChange={e => setEndDate(e.target.value)}
              className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <button
            onClick={() => void runBacktest()}
            disabled={loading}
            className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:opacity-50 transition-colors"
          >
            <Play size={14} />
            {loading ? 'Running…' : 'Run Backtest'}
          </button>
          {error && <span className="text-xs text-red-400">{error}</span>}
        </div>
        <p className="text-xs text-slate-600 mt-2">Max range: 180 days. Uses same risk params as live config.</p>
      </div>

      {loading && (
        <div className="bg-slate-900 rounded-xl border border-slate-700 p-10 text-center text-slate-500 text-sm">
          Fetching historical bars and replaying strategy… this may take 20–60 seconds.
        </div>
      )}

      {result && (
        <>
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
            <StatCard
              label="Max Drawdown"
              value={`-$${result.stats.max_drawdown}`}
              positive={false}
            />
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Avg Win" value={fmtUsd(result.stats.avg_win)} positive={true} />
            <StatCard label="Avg Loss" value={`-$${result.stats.avg_loss}`} positive={false} />
            <StatCard
              label="Sharpe Ratio"
              value={String(result.stats.sharpe_ratio)}
              sub="annualised, daily returns"
              positive={result.stats.sharpe_ratio >= 0}
            />
            <StatCard
              label="Period"
              value={`${result.start_date} → ${result.end_date}`}
              sub={`${result.symbol}`}
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
                        <stop offset="5%" stopColor={equityColor} stopOpacity={0.2} />
                        <stop offset="95%" stopColor={equityColor} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                    <XAxis dataKey="timestamp" tickFormatter={formatDate} tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={40} />
                    <YAxis tickFormatter={v => `$${((v as number) / 1000).toFixed(0)}k`} tick={{ fill: '#64748b', fontSize: 11 }} tickLine={false} axisLine={false} width={52} domain={['auto', 'auto']} />
                    <Tooltip
                      contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
                      labelFormatter={v => formatDate(v as number)}
                      formatter={(v: number) => [v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }), 'Equity']}
                    />
                    <ReferenceLine y={equityStart} stroke="#475569" strokeDasharray="4 2" />
                    <Area type="monotone" dataKey="equity" stroke={equityColor} strokeWidth={2} fill="url(#btGrad)" dot={false} activeDot={{ r: 4 }} />
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
