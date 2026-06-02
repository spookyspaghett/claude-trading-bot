import { useEffect, useRef, useState } from 'react'
import {
  createChart, ColorType, type IChartApi, type ISeriesApi,
  type CandlestickData, type LineData, type SeriesMarker, type IPriceLine, type UTCTimestamp,
} from 'lightweight-charts'

interface Props {
  slug: string
  symbols: string[]
}

interface Bar { time: number; open: number; high: number; low: number; close: number; volume: number }
interface Marker { time: number; side: string; price: number }
interface Indicators { strategy: string; ma_fast: number; ma_slow: number; pivot_lookback: number; pivot_strength: number }
interface BarsResponse { symbol: string; timeframe: string; bars: Bar[]; markers: Marker[]; indicators: Indicators }

const TIMEFRAMES = ['1Min', '5Min', '15Min', '1Hour', '1Day']

// EMA matching the bot's strategies (k = 2/(n+1), seeded with the first close).
function ema(bars: Bar[], period: number): LineData[] {
  if (period < 2 || bars.length === 0) return []
  const k = 2 / (period + 1)
  let prev = bars[0].close
  const out: LineData[] = []
  for (let i = 0; i < bars.length; i++) {
    prev = i === 0 ? bars[i].close : bars[i].close * k + prev * (1 - k)
    out.push({ time: bars[i].time as UTCTimestamp, value: prev })
  }
  return out
}

// Latest confirmed pivot high (resistance) and low (support), mirroring TrendSRStrategy.
function pivotLevels(bars: Bar[], strength: number): { resistance?: number; support?: number } {
  if (strength < 1 || bars.length < strength * 2 + 1) return {}
  let resistance: number | undefined
  let support: number | undefined
  for (let i = strength; i < bars.length - strength; i++) {
    let isHigh = true, isLow = true
    for (let j = i - strength; j <= i + strength; j++) {
      if (bars[j].high > bars[i].high) isHigh = false
      if (bars[j].low < bars[i].low) isLow = false
    }
    if (isHigh) resistance = bars[i].high
    if (isLow) support = bars[i].low
  }
  return { resistance, support }
}

export default function PriceChart({ slug, symbols }: Props) {
  const [symbol, setSymbol] = useState(symbols[0] ?? '')
  const [timeframe, setTimeframe] = useState('15Min')
  const [data, setData] = useState<BarsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const fastRef = useRef<ISeriesApi<'Line'> | null>(null)
  const slowRef = useRef<ISeriesApi<'Line'> | null>(null)
  const priceLinesRef = useRef<IPriceLine[]>([])

  // Keep the selected symbol valid as profiles/symbols change.
  useEffect(() => {
    if (symbols.length && !symbols.includes(symbol)) setSymbol(symbols[0])
  }, [symbols, symbol])

  // Fetch bars whenever the target changes.
  useEffect(() => {
    if (!symbol) return
    let cancelled = false
    setLoading(true); setError(null)
    const q = new URLSearchParams({ profile: slug, symbol, timeframe, limit: '300' })
    fetch(`/api/bars?${q.toString()}`)
      .then(async r => {
        if (!r.ok) throw new Error((await r.json() as { detail?: string }).detail ?? `HTTP ${r.status}`)
        return r.json() as Promise<BarsResponse>
      })
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) { setError(String(e)); setData(null) } })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [slug, symbol, timeframe])

  // Create the chart once.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, {
      width: el.clientWidth,
      height: 360,
      layout: { background: { type: ColorType.Solid, color: '#0f172a' }, textColor: '#94a3b8', fontSize: 11 },
      grid: { vertLines: { color: '#1e293b' }, horzLines: { color: '#1e293b' } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#334155' },
      rightPriceScale: { borderColor: '#334155' },
      crosshair: { mode: 0 },
    })
    chartRef.current = chart
    candleRef.current = chart.addCandlestickSeries({
      upColor: '#4ade80', downColor: '#f87171', borderVisible: false,
      wickUpColor: '#4ade80', wickDownColor: '#f87171',
    })
    fastRef.current = chart.addLineSeries({ color: '#38bdf8', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })
    slowRef.current = chart.addLineSeries({ color: '#a78bfa', lineWidth: 1, priceLineVisible: false, lastValueVisible: false })

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }))
    ro.observe(el)
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null }
  }, [])

  // Push data into the chart when it changes.
  useEffect(() => {
    const candle = candleRef.current
    if (!candle || !data) return
    const bars = data.bars
    candle.setData(bars.map(b => ({
      time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close,
    })) as CandlestickData[])

    fastRef.current?.setData(data.indicators.ma_fast > 0 ? ema(bars, data.indicators.ma_fast) : [])
    slowRef.current?.setData(data.indicators.ma_slow > 0 ? ema(bars, data.indicators.ma_slow) : [])

    // Buy/sell markers.
    const markers: SeriesMarker<UTCTimestamp>[] = data.markers.map(m => {
      const buy = m.side.toLowerCase() === 'buy'
      return {
        time: m.time as UTCTimestamp,
        position: buy ? 'belowBar' : 'aboveBar',
        color: buy ? '#4ade80' : '#f87171',
        shape: buy ? 'arrowUp' : 'arrowDown',
        text: buy ? 'BUY' : 'SELL',
      }
    })
    candle.setMarkers(markers)

    // Support / resistance price lines.
    for (const pl of priceLinesRef.current) candle.removePriceLine(pl)
    priceLinesRef.current = []
    if (data.indicators.pivot_strength > 0) {
      const { resistance, support } = pivotLevels(bars, data.indicators.pivot_strength)
      if (resistance) priceLinesRef.current.push(candle.createPriceLine({
        price: resistance, color: '#f59e0b', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'R',
      }))
      if (support) priceLinesRef.current.push(candle.createPriceLine({
        price: support, color: '#22d3ee', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'S',
      }))
    }
    chartRef.current?.timeScale().fitContent()
  }, [data])

  const ind = data?.indicators

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 flex flex-col overflow-hidden lg:col-span-2">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-200">Price</h2>
          <select value={symbol} onChange={e => setSymbol(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded-lg px-2 py-1 text-xs text-slate-100 focus:outline-none focus:border-blue-500">
            {symbols.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
          <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5">
            {TIMEFRAMES.map(tf => (
              <button key={tf} onClick={() => setTimeframe(tf)}
                className={`px-2 py-0.5 rounded text-[11px] font-semibold transition-colors ${
                  timeframe === tf ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
                }`}>
                {tf.replace('Min', 'm').replace('Hour', 'h').replace('Day', 'D')}
              </button>
            ))}
          </div>
        </div>
        {ind && (ind.ma_fast > 0 || ind.ma_slow > 0 || ind.pivot_strength > 0) && (
          <div className="flex items-center gap-3 text-[10px] text-slate-500">
            {ind.ma_fast > 0 && <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-sky-400 inline-block" />MA{ind.ma_fast}</span>}
            {ind.ma_slow > 0 && <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-violet-400 inline-block" />MA{ind.ma_slow}</span>}
            {ind.pivot_strength > 0 && <span className="text-amber-400">R</span>}
            {ind.pivot_strength > 0 && <span className="text-cyan-400">S</span>}
          </div>
        )}
      </div>
      <div className="relative">
        <div ref={containerRef} className="w-full" style={{ height: 360 }} />
        {loading && <div className="absolute inset-0 flex items-center justify-center text-slate-500 text-sm bg-slate-900/40">Loading…</div>}
        {error && <div className="absolute inset-0 flex items-center justify-center text-red-400 text-sm px-4 text-center">{error}</div>}
        {!loading && !error && data && data.bars.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-slate-600 text-sm">No bars returned for {symbol}.</div>
        )}
      </div>
    </div>
  )
}
