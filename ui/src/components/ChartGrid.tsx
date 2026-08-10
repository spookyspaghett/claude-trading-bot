import { useEffect, useState } from 'react'
import { LayoutGrid } from 'lucide-react'
import PriceChart from './PriceChart'
import ErrorBoundary from './ErrorBoundary'

interface Props {
  slug: string
  symbols: string[]
}

interface PaneConfig { symbol: string; timeframe: string }

const PANE_COUNTS = [1, 2, 4, 6] as const
type PaneCount = (typeof PANE_COUNTS)[number]

// Each pane fetches its own /api/bars, so the tab's request load scales with
// the count. Shorter charts as the count grows keeps every pane on screen
// without the page turning into a scroll marathon.
const HEIGHTS: Record<PaneCount, number> = { 1: 360, 2: 320, 4: 260, 6: 220 }
const COLUMNS: Record<PaneCount, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-1 xl:grid-cols-2',
  4: 'grid-cols-1 xl:grid-cols-2',
  6: 'grid-cols-1 lg:grid-cols-2 2xl:grid-cols-3',
}

function storageKey(slug: string) {
  return `chartPanes:${slug}`
}

/** Panes are seeded across the profile's symbols so a fresh 4-up view shows
 *  four different tickers rather than four copies of the first one. */
function defaultPane(index: number, symbols: string[]): PaneConfig {
  return { symbol: symbols[index % symbols.length] ?? '', timeframe: '15Min' }
}

interface GridState { slug: string; count: PaneCount; panes: PaneConfig[] }

function loadSaved(slug: string, symbols: string[]): GridState {
  const fallback: GridState = { slug, count: 1, panes: [defaultPane(0, symbols)] }
  try {
    const raw = localStorage.getItem(storageKey(slug))
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as { count?: number; panes?: PaneConfig[] }
    const count = (PANE_COUNTS as readonly number[]).includes(parsed.count ?? 0)
      ? (parsed.count as PaneCount)
      : 1
    // A saved symbol can outlive the profile that had it (symbols edited, or a
    // different profile reusing the slug) — fall back per pane rather than
    // rendering a chart that can only 404.
    const panes = Array.from({ length: count }, (_, i) => {
      const saved = parsed.panes?.[i]
      if (saved && symbols.includes(saved.symbol)) return saved
      return defaultPane(i, symbols)
    })
    return { slug, count, panes }
  } catch {
    return fallback   // private mode / corrupt entry — not worth surfacing
  }
}

export default function ChartGrid({ slug, symbols }: Props) {
  const [state, setState] = useState<GridState>(() => loadSaved(slug, symbols))
  const { count, panes } = state

  // Re-seed when the profile changes: this component stays mounted across
  // profile switches, so without this the panes keep the old profile's symbols.
  // Keyed on the symbols' *contents* — a fresh array identity from the parent
  // on every render would otherwise make this effect re-fire in a loop.
  const symbolsKey = symbols.join(',')
  useEffect(
    () => { setState(loadSaved(slug, symbolsKey.split(',').filter(Boolean))) },
    [slug, symbolsKey],
  )

  useEffect(() => {
    // On a profile switch this effect and the re-seed above run in the same
    // commit, so without the slug guard it would write the outgoing profile's
    // panes under the incoming profile's key.
    if (state.slug !== slug) return
    try {
      localStorage.setItem(storageKey(slug), JSON.stringify({ count, panes }))
    } catch { /* storage unavailable — the layout just won't persist */ }
  }, [slug, state.slug, count, panes])

  function setCount(next: PaneCount) {
    setState(prev => ({
      ...prev,
      count: next,
      // Growing keeps what's configured and seeds the new panes; shrinking
      // trims from the end, so pane 1 never moves under the user.
      panes: Array.from({ length: next }, (_, i) => prev.panes[i] ?? defaultPane(i, symbols)),
    }))
  }

  function updatePane(index: number, cfg: PaneConfig) {
    setState(prev => ({
      ...prev,
      panes: prev.panes.map((p, i) => (i === index ? cfg : p)),
    }))
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="flex items-center gap-1.5 text-xs text-slate-500">
          <LayoutGrid size={13} className="text-slate-600" />
          Charts
        </span>
        <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5">
          {PANE_COUNTS.map(n => (
            <button
              key={n}
              onClick={() => setCount(n)}
              aria-pressed={count === n}
              title={`Show ${n} chart${n === 1 ? '' : 's'}`}
              className={`px-2.5 py-0.5 rounded text-[11px] font-semibold transition-colors ${
                count === n ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {n}
            </button>
          ))}
        </div>
      </div>

      <div className={`grid ${COLUMNS[count]} gap-4`}>
        {panes.map((pane, i) => (
          <ErrorBoundary key={i} label={`Price chart ${i + 1}`}>
            <PriceChart
              slug={slug}
              symbols={symbols}
              initialSymbol={pane.symbol}
              initialTimeframe={pane.timeframe}
              height={HEIGHTS[count]}
              onConfigChange={cfg => updatePane(i, cfg)}
            />
          </ErrorBoundary>
        ))}
      </div>
    </div>
  )
}
