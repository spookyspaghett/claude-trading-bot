import { ShieldCheck, ShieldAlert, ShieldX } from 'lucide-react'

export interface SampleVerdict {
  level: 'ok' | 'warn' | 'bad'
  headline: string
  trades: number
  free_parameters: number
  ratio: number
  target_ratio: number
}

interface Props {
  sample: SampleVerdict
  /** Names of the knobs counted as tuned, for the explanation line. */
  freeParameters: string[]
  /** Trades that carried a usable stop - the sample the R metrics rest on. */
  rTrades?: number
  exposurePct?: number
}

/** Whether this run's trade count can support the statistics printed above it.
 *
 *  Every other number on this page gets more confident as you tune, because
 *  tuning is what makes an in-sample curve look good. This one gets less
 *  confident, which is the only reason it is worth showing.
 *
 *  The verdict is computed server-side (backtest_params.sample_verdict) so the
 *  thresholds are defined once and a fresh run is judged the same way a stored
 *  one is. Under ~30 trades there is nothing to infer from, 100+ is where
 *  metrics read normally, and ~30 trades per free parameter is the usual guard
 *  against a curve-fitted result - a strategy at 10:1 is very likely overfitted
 *  rather than good. */
export default function BacktestQuality({ sample, freeParameters, rTrades, exposurePct }: Props) {
  const { level, headline, trades, ratio, target_ratio: target } = sample
  const n = sample.free_parameters

  const skin = {
    ok:   { box: 'border-green-800/60 bg-green-950/30', text: 'text-green-300', Icon: ShieldCheck },
    warn: { box: 'border-amber-800/60 bg-amber-950/30', text: 'text-amber-300', Icon: ShieldAlert },
    bad:  { box: 'border-red-800/60 bg-red-950/30',     text: 'text-red-300',   Icon: ShieldX },
  }[level]
  const { Icon } = skin

  // Capped at the benchmark - past it, more is not more meaningful.
  const pct = Math.max(0, Math.min(100, (ratio / target) * 100))

  return (
    <div className={`rounded-xl border p-4 ${skin.box}`}>
      <div className="flex items-start gap-3 flex-wrap">
        <Icon size={18} className={`${skin.text} shrink-0 mt-0.5`} aria-hidden="true" />
        <div className="flex-1 min-w-[16rem]">
          <p className={`text-sm font-semibold ${skin.text}`}>{headline}</p>
          <p className="text-xs text-slate-400 mt-1">
            <b className="tabular-nums text-slate-200">{trades}</b> trades across{' '}
            <b className="tabular-nums text-slate-200">{n}</b> tuned parameter{n === 1 ? '' : 's'}
            {' '}={' '}
            <b className={`tabular-nums ${skin.text}`}>{ratio.toFixed(1)}:1</b>
            <span className="text-slate-500"> · the usual benchmark is {target}:1</span>
          </p>

          <div className="mt-2 h-1.5 rounded-full bg-slate-800 overflow-hidden max-w-md">
            <div
              className={`h-full rounded-full ${
                level === 'ok' ? 'bg-green-500' : level === 'warn' ? 'bg-amber-500' : 'bg-red-500'
              }`}
              style={{ width: `${pct}%` }}
            />
          </div>

          {n > 0 && (
            <p className="text-[11px] text-slate-500 mt-2">
              Counted as tuned: {freeParameters.join(', ')}. Filters left at 0 and
              knobs this strategy ignores are excluded.
            </p>
          )}
          {rTrades !== undefined && rTrades < trades && (
            <p className="text-[11px] text-slate-500 mt-1">
              Only {rTrades} of {trades} trades carried a usable stop, so the R
              metrics rest on that smaller sample.
            </p>
          )}
          {exposurePct !== undefined && exposurePct < 15 && trades >= 30 && (
            <p className="text-[11px] text-slate-500 mt-1">
              In the market {exposurePct.toFixed(0)}% of the time — a high return
              on little exposure is a small number of lucky windows as often as
              it is an edge.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
