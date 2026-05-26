import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'
import type { EquityPoint } from '../types'

interface Props {
  data: EquityPoint[]
}

function formatDate(ts: number) {
  return new Date(ts * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function formatUsd(val: number) {
  return val.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
}

export default function EquityChart({ data }: Props) {
  const first = data[0]?.equity ?? 0
  const last = data[data.length - 1]?.equity ?? 0
  const change = last - first
  const isUp = change >= 0

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 flex flex-col">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Account Equity (30d)</h2>
        {data.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-slate-200">{formatUsd(last)}</span>
            <span className={`text-xs font-medium ${isUp ? 'text-green-400' : 'text-red-400'}`}>
              {isUp ? '+' : ''}{formatUsd(change)}
            </span>
          </div>
        )}
      </div>

      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-600 text-sm py-10">
          No history available
        </div>
      ) : (
        <div className="p-2 flex-1 min-h-0" style={{ height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={isUp ? '#4ade80' : '#f87171'} stopOpacity={0.2} />
                  <stop offset="95%" stopColor={isUp ? '#4ade80' : '#f87171'} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis
                dataKey="timestamp"
                tickFormatter={formatDate}
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
              />
              <YAxis
                tickFormatter={v => `$${((v as number) / 1000).toFixed(0)}k`}
                tick={{ fill: '#64748b', fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={48}
                domain={['auto', 'auto']}
              />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                labelFormatter={v => formatDate(v as number)}
                formatter={(v: number) => [formatUsd(v), 'Equity']}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke={isUp ? '#4ade80' : '#f87171'}
                strokeWidth={2}
                fill="url(#equityGrad)"
                dot={false}
                activeDot={{ r: 4 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
