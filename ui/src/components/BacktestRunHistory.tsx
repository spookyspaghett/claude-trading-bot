import { useCallback, useEffect, useState } from 'react'
import { History, Trash2, GitCompare, RotateCcw, ChevronDown, ChevronUp } from 'lucide-react'
import type { BacktestParams } from './BacktestSettingsLoader'

interface RunStats {
  total_trades?: number
  win_rate?: number
  profit_factor?: number
  total_pnl?: string
  total_return_pct?: number
  max_drawdown_pct?: number
  sharpe_ratio?: number
  sortino_ratio?: number
  calmar_ratio?: number
  expectancy_r?: number
  max_consecutive_losses?: number
  exposure_pct?: number
  mc_p95_max_dd_pct?: number
}

interface Sample { level: 'ok' | 'warn' | 'bad'; ratio: number; free_parameters: number }

export interface RunRow {
  id: string
  created_at: string
  symbol: string
  strategy_used: string
  start_date: string
  end_date: string
  has_params: boolean
  params: BacktestParams
  sample: Sample | null
  stats: RunStats
}

interface Props {
  /** Bumped by the panel after a run completes, to refresh the list. */
  refreshKey: number
  /** Push a stored run's settings back into the form. */
  onLoadParams: (params: BacktestParams, source: string) => void
}

const METRICS: { key: keyof RunStats; label: string; fmt: (v: number) => string; better: 'high' | 'low' }[] = [
  { key: 'total_return_pct',      label: 'Return',        fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`, better: 'high' },
  { key: 'total_trades',          label: 'Trades',        fmt: v => `${v}`,            better: 'high' },
  { key: 'win_rate',              label: 'Win rate',      fmt: v => `${v.toFixed(1)}%`, better: 'high' },
  { key: 'profit_factor',         label: 'Profit factor', fmt: v => v >= 999 ? '∞' : v.toFixed(2), better: 'high' },
  { key: 'expectancy_r',          label: 'Expectancy',    fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}R`, better: 'high' },
  { key: 'sortino_ratio',         label: 'Sortino',       fmt: v => v.toFixed(2),      better: 'high' },
  { key: 'calmar_ratio',          label: 'Calmar',        fmt: v => v.toFixed(2),      better: 'high' },
  { key: 'max_drawdown_pct',      label: 'Max DD',        fmt: v => `-${v.toFixed(1)}%`, better: 'low' },
  { key: 'mc_p95_max_dd_pct',     label: 'MC p95 DD',     fmt: v => `-${v.toFixed(1)}%`, better: 'low' },
  { key: 'max_consecutive_losses',label: 'Worst streak',  fmt: v => `${v}`,            better: 'low' },
  { key: 'exposure_pct',          label: 'Exposure',      fmt: v => `${v.toFixed(0)}%`, better: 'high' },
]

const SAMPLE_DOT: Record<Sample['level'], string> = {
  ok: 'bg-green-500', warn: 'bg-amber-500', bad: 'bg-red-500',
}

function when(iso: string) {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—'
    : d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

/** Which settings differ between two runs. This is the whole point of the
 *  comparison: docs/trend_sr_filters.md prescribes changing ONE filter at a
 *  time and reading the effect, which is unusable if you can't see what
 *  changed. Runs saved before parameters were recorded have nothing to diff. */
function paramDiff(a: BacktestParams, b: BacktestParams) {
  const keys = [...new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})])].sort()
  return keys
    .filter(k => k !== 'file' && String(a?.[k] ?? '') !== String(b?.[k] ?? ''))
    .map(k => ({ key: k, a: a?.[k], b: b?.[k] }))
}

export default function BacktestRunHistory({ refreshKey, onLoadParams }: Props) {
  const [runs, setRuns] = useState<RunRow[]>([])
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/backtest/runs?limit=50')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setRuns(await res.json() as RunRow[])
      setError(null)
    } catch (err) {
      setError(String(err))
    }
  }, [])

  useEffect(() => { void load() }, [load, refreshKey])

  async function remove(id: string) {
    try {
      await fetch(`/api/backtest/runs/${encodeURIComponent(id)}`, { method: 'DELETE' })
      setPicked(p => p.filter(x => x !== id))
      void load()
    } catch (err) { setError(String(err)) }
  }

  function toggle(id: string) {
    setPicked(p => p.includes(id) ? p.filter(x => x !== id)
      // Two at a time: a side-by-side of three columns stops being readable,
      // and the workflow this serves is one change against one baseline.
      : [...p, id].slice(-2))
  }

  const [a, b] = picked.map(id => runs.find(r => r.id === id)).filter(Boolean) as RunRow[]
  const comparing = Boolean(a && b)
  const diff = comparing ? paramDiff(a.params, b.params) : []

  return (
    <div className="card">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        aria-expanded={open}
        className="w-full px-4 py-3 flex items-center justify-between gap-2 hover:bg-slate-800/40 transition-colors"
      >
        <span className="flex items-center gap-2 text-sm font-semibold text-slate-200">
          <History size={14} aria-hidden="true" />
          Run History
          <span className="text-xs font-normal text-slate-500">
            {runs.length} saved{runs.length ? ' · tick two to compare' : ''}
          </span>
        </span>
        {open ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
      </button>

      {open && (
        <div className="border-t border-slate-700">
          {error && <p className="px-4 py-2 text-xs text-red-400">{error}</p>}
          {runs.length === 0 && !error && (
            <p className="px-4 py-6 text-sm text-slate-500 text-center">
              No saved runs yet. Every backtest you run is stored here automatically.
            </p>
          )}

          {runs.length > 0 && (
            <div className="overflow-auto max-h-72">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-900 z-10">
                  <tr className="text-slate-500 uppercase tracking-wider border-b border-slate-800">
                    <th className="px-3 py-2 w-8"><span className="sr-only">Compare</span></th>
                    <th className="px-3 py-2 text-left">When</th>
                    <th className="px-3 py-2 text-left">Symbol</th>
                    <th className="px-3 py-2 text-left">Strategy</th>
                    <th className="px-3 py-2 text-right">Trades</th>
                    <th className="px-3 py-2 text-right">Return</th>
                    <th className="px-3 py-2 text-right">Max DD</th>
                    <th className="px-3 py-2 text-right">Expectancy</th>
                    <th className="px-3 py-2 text-center">Sample</th>
                    <th className="px-3 py-2 w-20"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {runs.map(r => {
                    const ret = r.stats.total_return_pct ?? 0
                    return (
                      <tr key={r.id} className={`hover:bg-slate-800/40 transition-colors ${
                        picked.includes(r.id) ? 'bg-blue-950/30' : ''
                      }`}>
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={picked.includes(r.id)}
                            onChange={() => toggle(r.id)}
                            className="w-3.5 h-3.5 accent-blue-500"
                            aria-label={`Compare run from ${when(r.created_at)}`}
                          />
                        </td>
                        <td className="px-3 py-2 text-slate-400 tabular-nums">{when(r.created_at)}</td>
                        <td className="px-3 py-2 text-slate-300 font-medium">{r.symbol}</td>
                        <td className="px-3 py-2 text-slate-400">{r.strategy_used}</td>
                        <td className="px-3 py-2 text-right text-slate-300 tabular-nums">{r.stats.total_trades ?? '—'}</td>
                        <td className={`px-3 py-2 text-right font-bold tabular-nums ${
                          ret >= 0 ? 'text-green-400' : 'text-red-400'
                        }`}>{ret >= 0 ? '+' : ''}{ret.toFixed(1)}%</td>
                        <td className="px-3 py-2 text-right text-red-400/80 tabular-nums">
                          -{(r.stats.max_drawdown_pct ?? 0).toFixed(1)}%
                        </td>
                        <td className="px-3 py-2 text-right text-slate-300 tabular-nums">
                          {r.stats.expectancy_r === undefined ? '—'
                            : `${r.stats.expectancy_r >= 0 ? '+' : ''}${r.stats.expectancy_r.toFixed(2)}R`}
                        </td>
                        <td className="px-3 py-2 text-center">
                          {r.sample
                            ? <span
                                className={`inline-block w-2 h-2 rounded-full ${SAMPLE_DOT[r.sample.level]}`}
                                title={`${r.sample.ratio}:1 trades per tuned parameter`}
                              />
                            : <span className="text-slate-700" title="saved before parameters were recorded">·</span>}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1 justify-end">
                            <button
                              type="button"
                              onClick={() => onLoadParams(r.params, `run ${when(r.created_at)}`)}
                              disabled={!r.has_params}
                              title={r.has_params ? 'Load these settings into the form' : 'This run predates parameter recording'}
                              className="p-1 rounded text-slate-400 hover:text-blue-300 hover:bg-slate-700 disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
                            >
                              <RotateCcw size={12} />
                            </button>
                            <button
                              type="button"
                              onClick={() => void remove(r.id)}
                              title="Delete this run"
                              className="p-1 rounded text-slate-400 hover:text-red-300 hover:bg-slate-700 transition-colors"
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {comparing && (
            <div className="border-t border-slate-700 p-4 space-y-3">
              <h4 className="flex items-center gap-1.5 text-xs font-semibold text-slate-200 uppercase tracking-wide">
                <GitCompare size={13} aria-hidden="true" /> Comparison
              </h4>

              <div className="overflow-x-auto">
                <table className="text-xs w-full">
                  <thead>
                    <tr className="text-slate-500">
                      <th className="text-left font-medium py-1 pr-4">Metric</th>
                      <th className="text-right font-medium py-1 px-3">{when(a.created_at)}</th>
                      <th className="text-right font-medium py-1 px-3">{when(b.created_at)}</th>
                      <th className="text-right font-medium py-1 pl-3">Change</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {METRICS.map(m => {
                      const va = a.stats[m.key] as number | undefined
                      const vb = b.stats[m.key] as number | undefined
                      if (va === undefined && vb === undefined) return null
                      const delta = (vb ?? 0) - (va ?? 0)
                      const improved = m.better === 'high' ? delta > 0 : delta < 0
                      return (
                        <tr key={m.key}>
                          <td className="py-1 pr-4 text-slate-400">{m.label}</td>
                          <td className="py-1 px-3 text-right text-slate-300 tabular-nums">
                            {va === undefined ? '—' : m.fmt(va)}
                          </td>
                          <td className="py-1 px-3 text-right text-slate-300 tabular-nums">
                            {vb === undefined ? '—' : m.fmt(vb)}
                          </td>
                          <td className={`py-1 pl-3 text-right tabular-nums font-medium ${
                            Math.abs(delta) < 1e-9 ? 'text-slate-600'
                              : improved ? 'text-green-400' : 'text-red-400'
                          }`}>
                            {Math.abs(delta) < 1e-9 ? '=' : `${delta > 0 ? '+' : ''}${delta.toFixed(2)}`}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div>
                <p className="text-xs font-semibold text-slate-300 mb-1">What changed</p>
                {!a.has_params || !b.has_params ? (
                  <p className="text-[11px] text-slate-500">
                    One of these runs was saved before parameters were recorded,
                    so there is nothing to diff.
                  </p>
                ) : diff.length === 0 ? (
                  <p className="text-[11px] text-slate-500">
                    Identical settings — any difference is data, not tuning.
                  </p>
                ) : (
                  <>
                    <ul className="space-y-0.5">
                      {diff.map(d => (
                        <li key={d.key} className="flex items-baseline gap-2 font-mono text-[11px]">
                          <span className="text-slate-400">{d.key}</span>
                          <span className="text-red-300">{String(d.a ?? '—')}</span>
                          <span className="text-slate-600">→</span>
                          <span className="text-green-300 font-semibold">{String(d.b ?? '—')}</span>
                        </li>
                      ))}
                    </ul>
                    {diff.length > 3 && (
                      <p className="text-[11px] text-amber-300/90 mt-1.5">
                        {diff.length} settings changed at once — with more than one
                        knob moving you cannot attribute the difference to any of them.
                      </p>
                    )}
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
