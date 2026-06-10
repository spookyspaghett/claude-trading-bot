import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts'
import { Wallet } from 'lucide-react'
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
  const first      = data[0]?.equity ?? 0
  const last       = data[data.length - 1]?.equity ?? 0
  const change     = last - first
  const returnPct  = first > 0 ? ((change / first) * 100).toFixed(2) : '0.00'
  const isUp       = change >= 0
  const color      = isUp ? '#4ade80' : '#f87171'

  return (
    <div className="card flex flex-col">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
            <Wallet size={14} className="text-violet-400" />
            Account Equity (30d)
          </h2>
          {data.length > 0 && (
            <p className="text-xs text-slate-500 mt-0.5">
              Start {formatUsd(first)} · {data.length} days
            </p>
          )}
        </div>
        {data.length > 0 && (
          <div className="text-right">
            <p className="text-lg font-bold text-slate-100 tabular-nums">{formatUsd(last)}</p>
            <p className={`text-xs font-semibold tabular-nums ${isUp ? 'text-green-400' : 'text-red-400'}`}>
              {isUp ? '+' : ''}{formatUsd(change)} ({isUp ? '+' : ''}{returnPct}%)
            </p>
          </div>
        )}
      </div>

      {data.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-600 text-sm py-10">
          No history available
        </div>
      ) : (
        <div className="p-2" style={{ height: 260 }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor={color} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={color} stopOpacity={0.02} />
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
                width={52}
                domain={['auto', 'auto']}
              />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                labelFormatter={v => formatDate(v as number)}
                formatter={(v: number) => [formatUsd(v), 'Equity']}
              />
              {/* Starting equity baseline */}
              <ReferenceLine
                y={first}
                stroke="#475569"
                strokeDasharray="4 2"
                label={{ value: 'Start', fill: '#475569', fontSize: 10, position: 'insideTopRight' }}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke={color}
                strokeWidth={2}
                fill="url(#equityGrad)"
                dot={false}
                activeDot={{ r: 4, fill: color }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
