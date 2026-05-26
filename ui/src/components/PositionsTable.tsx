import type { Position } from '../types'

interface Props {
  positions: Position[]
}

function fmt(val: string, style: 'currency' | 'percent' = 'currency') {
  const n = parseFloat(val)
  if (isNaN(n)) return val
  if (style === 'percent') return `${(n * 100).toFixed(2)}%`
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

function plClass(val: string) {
  const n = parseFloat(val)
  if (n > 0) return 'text-green-400'
  if (n < 0) return 'text-red-400'
  return 'text-slate-400'
}

export default function PositionsTable({ positions }: Props) {
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 overflow-hidden flex flex-col">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-200">Open Positions</h2>
        <span className="text-xs text-slate-500">{positions.length} position{positions.length !== 1 ? 's' : ''}</span>
      </div>

      {positions.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-slate-600 text-sm py-10">
          No open positions
        </div>
      ) : (
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-4 py-2 text-left">Symbol</th>
                <th className="px-4 py-2 text-right">Qty</th>
                <th className="px-4 py-2 text-right">Entry</th>
                <th className="px-4 py-2 text-right">Last</th>
                <th className="px-4 py-2 text-right">Unr. P&amp;L</th>
                <th className="px-4 py-2 text-right">%</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {positions.map(p => (
                <tr key={p.symbol} className="hover:bg-slate-800/50 transition-colors">
                  <td className="px-4 py-2.5 font-semibold text-slate-100">
                    {p.symbol}
                    <span className={`ml-1.5 text-xs font-normal ${p.side === 'long' ? 'text-green-500' : 'text-red-500'}`}>
                      {p.side}
                    </span>
                  </td>
                  <td className="px-4 py-2.5 text-right text-slate-300">{p.qty}</td>
                  <td className="px-4 py-2.5 text-right text-slate-300">{fmt(p.avg_entry_price)}</td>
                  <td className="px-4 py-2.5 text-right text-slate-200 font-medium">{fmt(p.current_price)}</td>
                  <td className={`px-4 py-2.5 text-right font-semibold ${plClass(p.unrealized_pl)}`}>
                    {parseFloat(p.unrealized_pl) >= 0 ? '+' : ''}{fmt(p.unrealized_pl)}
                  </td>
                  <td className={`px-4 py-2.5 text-right ${plClass(p.unrealized_plpc)}`}>
                    {parseFloat(p.unrealized_plpc) >= 0 ? '+' : ''}{fmt(p.unrealized_plpc, 'percent')}
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
