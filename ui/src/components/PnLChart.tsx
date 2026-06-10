import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
} from 'recharts'
import type { PnLPoint } from '../types'

interface Props {
  data: PnLPoint[]
}

function formatTime(ts: number) {
  return new Date(ts * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/New_York',
  })
}

function formatUsd(val: number) {
  return val.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export default function PnLChart({ data }: Props) {
  // Alpaca can return null P&L points early in a fresh session — treat as 0.
  const safe      = data.map(d => ({ ...d, profit_loss: d.profit_loss ?? 0 }))
  const lastPnL   = safe.length ? safe[safe.length - 1].profit_loss : 0
  const maxPnL    = safe.length ? Math.max(...safe.map(d => d.profit_loss)) : 0
  const minPnL    = safe.length ? Math.min(...safe.map(d => d.profit_loss)) : 0
  const isUp      = lastPnL >= 0
  const lineColor = isUp ? '#4ade80' : '#f87171'

  // Split data into positive and negative areas for two-color fill at zero
  const chartData = safe.map(d => ({
    ...d,
    pos: d.profit_loss > 0 ? d.profit_loss : 0,
    neg: d.profit_loss < 0 ? d.profit_loss : 0,
  }))

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 flex flex-col">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Intraday P&amp;L</h2>
          {data.length > 0 && (
            <p className="text-xs text-slate-500 mt-0.5">
              High {formatUsd(maxPnL)} · Low {formatUsd(minPnL)}
            </p>
          )}
        </div>
        <span className={`text-lg font-bold tabular-nums ${isUp ? 'text-green-400' : 'text-red-400'}`}>
          {lastPnL >= 0 ? '+' : ''}{formatUsd(lastPnL)}
        </span>
      </div>

      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-600 text-sm py-10">
          No data yet — starts at market open
        </div>
      ) : (
        <div className="p-2" style={{ height: 260 }}>
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="pnlGradPos" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#4ade80" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4ade80" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="pnlGradNeg" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#f87171" stopOpacity={0.02} />
                  <stop offset="95%" stopColor="#f87171" stopOpacity={0.3} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatTime}
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                minTickGap={60}
              />
              <YAxis
                tickFormatter={v => `$${(v as number).toFixed(0)}`}
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={62}
              />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                labelFormatter={v => formatTime(v as number)}
                formatter={(v: number) => [formatUsd(v), 'P&L']}
              />
              {/* Zero baseline — more visible */}
              <ReferenceLine y={0} stroke="#475569" strokeWidth={1.5} />
              {/* Green fill above zero */}
              <Area
                type="monotone"
                dataKey="pos"
                stroke="none"
                fill="url(#pnlGradPos)"
                isAnimationActive={false}
                baseValue={0}
              />
              {/* Red fill below zero */}
              <Area
                type="monotone"
                dataKey="neg"
                stroke="none"
                fill="url(#pnlGradNeg)"
                isAnimationActive={false}
                baseValue={0}
              />
              {/* Main P&L line on top */}
              <Line
                type="monotone"
                dataKey="profit_loss"
                stroke={lineColor}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: lineColor }}
                isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
