import { Moon, ArrowUpRight, ArrowDownRight, LogOut } from 'lucide-react'
import { usePolling } from '../hooks/useApi'
import type { DonchianState } from '../types'

interface Props {
  slug: string
}

const DEFAULT_STATE: DonchianState = {
  positions: [],
  queued_entries: {},
  queued_exits: [],
  queued_date: '',
  pending_reanchor: [],
  ran_eod_date: '',
  ran_open_date: '',
}

function usd(val: number) {
  return val.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export default function DonchianPanel({ slug }: Props) {
  const { data } = usePolling<DonchianState>(
    `/api/donchian?profile=${encodeURIComponent(slug)}`, 60_000, DEFAULT_STATE,
  )

  const entries = Object.entries(data.queued_entries ?? {})
  const exits = data.queued_exits ?? []
  const positions = data.positions ?? []
  const hasQueue = entries.length > 0 || exits.length > 0

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
          <Moon size={14} className="text-indigo-400" />
          Donchian Overnight Plan
        </h2>
        <span className="text-xs text-slate-500">
          scan {data.ran_eod_date || '—'} · orders {data.ran_open_date || '—'}
        </span>
      </div>

      {/* Queued actions from last night's scan, fired at the next open */}
      <div className="px-4 py-2.5 border-b border-slate-800 text-xs">
        {hasQueue ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">
              For next open{data.queued_date ? ` (scanned ${data.queued_date})` : ''}:
            </span>
            {entries.map(([sym, action]) => (
              <span key={sym} className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border font-semibold ${
                action === 'enter_long'
                  ? 'text-green-400 bg-green-950 border-green-800'
                  : 'text-red-400 bg-red-950 border-red-800'
              }`}>
                {action === 'enter_long' ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                {sym}
              </span>
            ))}
            {exits.map(sym => (
              <span key={sym} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border font-semibold text-amber-400 bg-amber-950 border-amber-800">
                <LogOut size={11} /> close {sym}
              </span>
            ))}
          </div>
        ) : (
          <span className="text-slate-600">No actions queued — next scan runs ~16:05 ET.</span>
        )}
      </div>

      {/* Tracked positions with their stops */}
      {positions.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-600 text-sm py-8">
          No tracked Donchian positions
        </div>
      ) : (
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-4 py-2 text-left">Symbol</th>
                <th className="px-4 py-2 text-right">Entry</th>
                <th className="px-4 py-2 text-right">Stop</th>
                <th className="px-4 py-2 text-right">Qty</th>
                <th className="px-4 py-2 text-left">Since</th>
                <th className="px-4 py-2 text-left">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {positions.map(p => (
                <tr key={p.symbol} className="hover:bg-slate-800/50 transition-colors">
                  <td className="px-4 py-2 font-semibold text-slate-100">
                    {p.symbol}
                    <span className={`ml-1.5 text-xs font-normal ${p.direction === 'BUY' ? 'text-green-500' : 'text-red-500'}`}>
                      {p.direction === 'BUY' ? 'long' : 'short'}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right text-slate-300">{usd(p.entry_price)}</td>
                  <td className="px-4 py-2 text-right text-slate-200 font-medium">{usd(p.stop_price)}</td>
                  <td className="px-4 py-2 text-right text-slate-300">{p.qty || '—'}</td>
                  <td className="px-4 py-2 text-slate-400 text-xs">{p.entry_date}</td>
                  <td className="px-4 py-2 text-xs">
                    {p.pending_exit ? (
                      <span className="text-amber-400 font-semibold">exiting at open</span>
                    ) : p.qty === 0 ? (
                      <span className="text-blue-400 font-semibold">awaiting fill</span>
                    ) : p.trailing_active ? (
                      <span className="text-green-400">trailing stop</span>
                    ) : (
                      <span className="text-slate-500">fixed stop</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
