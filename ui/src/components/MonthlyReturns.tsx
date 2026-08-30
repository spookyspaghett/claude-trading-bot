interface Point { timestamp: number; equity: number }

interface Props { equityCurve: Point[] }

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Month-by-month returns, the standard tearsheet grid.
 *
 *  An equity curve answers "did this make money". This answers "how did it get
 *  there" — whether the result is a steady edge or two good months carrying a
 *  year of chop. That distinction survives out-of-sample; a total return often
 *  doesn't.
 *
 *  Returns are computed from last-equity-of-month to last-equity-of-month, so a
 *  month with no trades correctly shows flat rather than absent. */
export default function MonthlyReturns({ equityCurve }: Props) {
  if (equityCurve.length < 2) return null

  // Last equity value seen in each calendar month, in order.
  const monthEnd = new Map<string, { year: number; month: number; equity: number }>()
  for (const p of equityCurve) {
    const d = new Date(p.timestamp * 1000)
    const key = `${d.getUTCFullYear()}-${d.getUTCMonth()}`
    monthEnd.set(key, { year: d.getUTCFullYear(), month: d.getUTCMonth(), equity: p.equity })
  }
  const ordered = [...monthEnd.values()].sort((a, b) =>
    a.year - b.year || a.month - b.month)
  if (ordered.length === 0) return null

  // The first month's return is measured from the opening equity, not from
  // itself — otherwise the run always starts with a spurious 0%.
  const opening = equityCurve[0].equity
  const cells = new Map<string, number>()
  const yearTotals = new Map<number, { start: number; end: number }>()
  let prev = opening
  for (const m of ordered) {
    const ret = prev > 0 ? ((m.equity - prev) / prev) * 100 : 0
    cells.set(`${m.year}-${m.month}`, ret)
    const y = yearTotals.get(m.year)
    if (y) y.end = m.equity
    else yearTotals.set(m.year, { start: prev, end: m.equity })
    prev = m.equity
  }

  const years = [...new Set(ordered.map(m => m.year))].sort()
  const magnitudes = [...cells.values()].map(Math.abs)
  // Scale colour to the run's own range, floored so a quiet strategy doesn't
  // render every small month as a screaming green.
  const scale = Math.max(2, ...magnitudes)

  function cellStyle(ret: number | undefined) {
    if (ret === undefined) return { background: 'transparent' }
    const a = Math.min(0.85, (Math.abs(ret) / scale) * 0.85 + 0.08)
    return { background: ret >= 0 ? `rgba(74,222,128,${a})` : `rgba(248,113,113,${a})` }
  }

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <h3 className="text-sm font-semibold text-slate-200">Monthly Returns</h3>
        <span className="text-xs text-slate-500">
          steady edge vs. two good months — the part a total return hides
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="text-xs border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="text-left text-slate-500 font-medium px-2 py-1 sticky left-0 bg-slate-900">
                Year
              </th>
              {MONTHS.map(m => (
                <th key={m} className="text-slate-500 font-medium px-1.5 py-1 w-12">{m}</th>
              ))}
              <th className="text-slate-400 font-semibold px-2 py-1">Year</th>
            </tr>
          </thead>
          <tbody>
            {years.map(year => {
              const yt = yearTotals.get(year)
              const total = yt && yt.start > 0 ? ((yt.end - yt.start) / yt.start) * 100 : 0
              return (
                <tr key={year}>
                  <td className="text-slate-400 tabular-nums px-2 py-1 sticky left-0 bg-slate-900 font-medium">
                    {year}
                  </td>
                  {MONTHS.map((_, i) => {
                    const ret = cells.get(`${year}-${i}`)
                    return (
                      <td
                        key={i}
                        className="text-center tabular-nums rounded px-1 py-1.5 text-[11px] text-slate-100"
                        style={cellStyle(ret)}
                        title={ret === undefined ? 'no data' : `${MONTHS[i]} ${year}: ${ret.toFixed(2)}%`}
                      >
                        {ret === undefined ? <span className="text-slate-700">·</span> : ret.toFixed(1)}
                      </td>
                    )
                  })}
                  <td className={`text-center tabular-nums px-2 py-1 font-bold ${
                    total >= 0 ? 'text-green-400' : 'text-red-400'
                  }`}>
                    {total >= 0 ? '+' : ''}{total.toFixed(1)}%
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-slate-600 mt-2">
        Percent change in equity within each calendar month. A month with no
        trades shows flat, not blank.
      </p>
    </div>
  )
}
