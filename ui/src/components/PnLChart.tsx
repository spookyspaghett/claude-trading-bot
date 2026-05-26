import {
  ResponsiveContainer,
  LineChart,
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
  const lastPnL = data.length ? data[data.length - 1].profit_loss : 0
  const lineColor = lastPnL >= 0 ? '#4ade80' : '#f87171'

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 flex flex-col">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Intraday P&amp;L</h2>
        <span className={`text-sm font-bold ${lastPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
          {lastPnL >= 0 ? '+' : ''}{formatUsd(lastPnL)}
        </span>
      </div>

      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-600 text-sm py-10">
          No data yet — starts at market open
        </div>
      ) : (
        <div className="p-2 flex-1 min-h-0" style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
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
                width={56}
              />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                labelFormatter={v => formatTime(v as number)}
                formatter={(v: number) => [formatUsd(v), 'P&L']}
              />
              <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 2" />
              <Line
                type="monotone"
                dataKey="profit_loss"
                stroke={lineColor}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4, fill: lineColor }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
