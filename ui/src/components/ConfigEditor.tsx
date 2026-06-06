import { useEffect, useState } from 'react'
import { Save, RotateCcw, HelpCircle } from 'lucide-react'
import { apiPost, apiPut } from '../hooks/useApi'
import type { Config } from '../types'

interface Props {
  onRestart: () => void
  slug: string
}

const DEFAULT: Config = {
  live: false,
  asset_class: 'stock',
  symbols: ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'TSLA'],
  risk: { max_position_usd: 50000, stop_loss_pct: 1.0, daily_loss_limit_usd: 500, max_open_positions: 4 },
  strategy: {
    name: 'orb',
    orb: { opening_range_minutes: 15, entry_order_type: 'limit', eod_exit_time: '15:50' },
    ema: { fast_period: 9, slow_period: 21, entry_order_type: 'market', eod_exit_time: '15:50' },
    donchian: { lookback_days: 40, trend_ma: 200, trailing_activation_pct: 1.0, trailing_pct: 8.0, long_only: true },
    trend_sr: {
      bar_minutes: 15, ma_fast: 21, ma_slow: 55, regime_ma: 200,
      pivot_lookback: 20, pivot_strength: 3, atr_period: 14, atr_mult: 2.0,
      breakout_buffer_atr: 0.25, cooldown_bars: 4,
      trailing_activation_pct: 3.0, trailing_pct: 8.0, long_only: true,
      min_adx: 0, adx_period: 14, volume_mult: 0, volume_ma: 20,
    },
  },
}

// ── Tooltip ───────────────────────────────────────────────────────────────────

function Tip({ text }: { text: string }) {
  const [show, setShow] = useState(false)
  return (
    <span className="relative inline-flex items-center ml-1">
      <HelpCircle
        size={12}
        className="text-slate-600 hover:text-slate-400 cursor-help transition-colors"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      />
      {show && (
        <span className="absolute z-50 left-5 top-0 w-56 rounded-lg bg-slate-700 border border-slate-600 text-xs text-slate-200 p-2.5 shadow-xl leading-relaxed pointer-events-none">
          {text}
        </span>
      )}
    </span>
  )
}

// ── Field label with tooltip ──────────────────────────────────────────────────

function Label({ children, tip }: { children: React.ReactNode; tip: string }) {
  return (
    <label className="flex items-center text-xs text-slate-500 mb-1">
      {children}
      <Tip text={tip} />
    </label>
  )
}

// ── Section heading ───────────────────────────────────────────────────────────

function Section({ title, tip, children }: { title: string; tip?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2.5">
      <h3 className="flex items-center text-xs font-semibold text-slate-400 uppercase tracking-wider">
        {title}
        {tip && <Tip text={tip} />}
      </h3>
      {children}
    </div>
  )
}

// ── Number input ──────────────────────────────────────────────────────────────

function NumInput({
  value, onChange, step = 1, min = 0, max, disabled,
}: {
  value: number; onChange: (v: number) => void
  step?: number; min?: number; max?: number; disabled?: boolean
}) {
  return (
    <input
      type="number" step={step} min={min} max={max} disabled={disabled}
      className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500 disabled:opacity-40"
      value={value}
      onChange={e => onChange(parseFloat(e.target.value) || 0)}
    />
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function ConfigEditor({ onRestart, slug }: Props) {
  const [cfg, setCfg] = useState<Config>(DEFAULT)
  const [symbolsText, setSymbolsText] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const q = `?profile=${encodeURIComponent(slug)}`

  useEffect(() => {
    fetch(`/api/config${q}`)
      .then(r => r.json())
      .then((data: Config) => {
        setCfg({ ...DEFAULT, ...data, strategy: { ...DEFAULT.strategy, ...data.strategy } })
        setSymbolsText(data.symbols.join(', '))
      })
      .catch(() => setSymbolsText(DEFAULT.symbols.join(', ')))
  }, [q])

  function setRisk(key: keyof Config['risk'], val: number) {
    setCfg(prev => ({ ...prev, risk: { ...prev.risk, [key]: val } }))
  }
  function setOrb(key: keyof Config['strategy']['orb'], val: string | number) {
    setCfg(prev => ({ ...prev, strategy: { ...prev.strategy, orb: { ...prev.strategy.orb, [key]: val } } }))
  }
  function setEma(key: keyof Config['strategy']['ema'], val: string | number) {
    setCfg(prev => ({ ...prev, strategy: { ...prev.strategy, ema: { ...prev.strategy.ema, [key]: val } } }))
  }
  function setDon(key: keyof Config['strategy']['donchian'], val: number | boolean) {
    setCfg(prev => ({ ...prev, strategy: { ...prev.strategy, donchian: { ...prev.strategy.donchian, [key]: val } } }))
  }
  function setTsr(key: keyof Config['strategy']['trend_sr'], val: number | boolean) {
    setCfg(prev => ({ ...prev, strategy: { ...prev.strategy, trend_sr: { ...prev.strategy.trend_sr, [key]: val } } }))
  }
  function setStrategyName(name: string) {
    setCfg(prev => ({ ...prev, strategy: { ...prev.strategy, name } }))
  }

  const isCrypto = cfg.asset_class === 'crypto'

  async function handleSave(andRestart: boolean) {
    setSaving(true); setMsg(null)
    const symbols = symbolsText.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    const payload: Config = { ...cfg, symbols }
    try {
      await apiPut(`/api/config${q}`, payload)
      if (andRestart) { await apiPost(`/api/bot/restart${q}`); onRestart() }
      setMsg({ text: andRestart ? 'Saved & restarted.' : 'Saved.', ok: true })
    } catch (err) {
      setMsg({ text: String(err), ok: false })
    } finally {
      setSaving(false)
    }
  }

  const mode = cfg.strategy.name

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Configuration</h2>
          <p className="text-xs text-slate-500 mt-0.5">Edits the active profile. Changes take effect on next bot start.</p>
        </div>
        <span className={`text-[10px] font-bold px-2 py-1 rounded border ${
          isCrypto ? 'text-amber-300 bg-amber-950 border-amber-800' : 'text-sky-300 bg-sky-950 border-sky-800'
        }`}>
          {isCrypto ? 'CRYPTO · 24/7' : 'STOCK'}
        </span>
      </div>

      <div className="p-4 space-y-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">

          {/* ── Symbols & live mode ───────────────────────────────────────── */}
          <Section
            title="Symbols"
            tip="The list of ticker symbols the bot will watch and potentially trade. Use comma-separated values. Add more symbols to increase the number of simultaneous opportunities — especially useful for Donchian mode."
          >
            <div>
              <Label tip="Comma-separated list of stock tickers. The bot scans all of these for signals and can hold up to max_open_positions at once.">
                Watch list
              </Label>
              <input
                className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                value={symbolsText}
                onChange={e => setSymbolsText(e.target.value)}
                placeholder="SPY, QQQ, AAPL, MSFT, NVDA, TSLA"
              />
              <p className="text-[10px] text-slate-600 mt-1">Donchian tip: add 8–12 symbols for daily trade opportunities</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox" id="live-mode"
                checked={cfg.live}
                onChange={e => setCfg(prev => ({ ...prev, live: e.target.checked }))}
                className="accent-red-500"
              />
              <label htmlFor="live-mode" className="text-xs text-red-400 font-medium cursor-pointer">
                Live trading (real money!)
              </label>
              <Tip text="When ON, orders are placed on a real brokerage account with real money. Leave OFF to use Alpaca paper trading." />
            </div>
          </Section>

          {/* ── Risk parameters ───────────────────────────────────────────── */}
          <Section
            title="Risk Parameters"
            tip="Controls how much money the bot risks per trade and per day. These limits apply to ALL strategies."
          >
            <div>
              <Label tip="Maximum dollar value of a single position. E.g. $50,000 means the bot buys at most $50k worth of stock per trade.">
                Max position size (USD)
              </Label>
              <NumInput value={cfg.risk.max_position_usd} step={1000} onChange={v => setRisk('max_position_usd', v)} />
            </div>
            <div>
              <Label tip="Stop-loss percentage for ORB/EMA intraday strategies. E.g. 1.0 means the stop is placed 1% below the entry price. Not used by Donchian (which uses ATR-based stops).">
                Stop loss % <span className="text-slate-600 ml-1">(ORB / EMA only)</span>
              </Label>
              <NumInput value={cfg.risk.stop_loss_pct} step={0.1} min={0.1} onChange={v => setRisk('stop_loss_pct', v)} />
            </div>
            <div>
              <Label tip="If total daily P&L drops below this amount (negative), the bot stops trading for the rest of the day and closes all positions. Protects against runaway losses.">
                Daily loss limit (USD)
              </Label>
              <NumInput value={cfg.risk.daily_loss_limit_usd} step={50} onChange={v => setRisk('daily_loss_limit_usd', v)} />
            </div>
            <div>
              <Label tip="Maximum number of positions open simultaneously. With Donchian across many symbols, set this to 4–8 to allow multiple concurrent swing trades.">
                Max open positions
              </Label>
              <NumInput value={cfg.risk.max_open_positions} step={1} min={1} max={20} onChange={v => setRisk('max_open_positions', v)} />
            </div>
          </Section>

          {/* ── Strategy selector ─────────────────────────────────────────── */}
          <Section title="Strategy">
            {/* Tabs — ORB is stock-only (built around the market open) */}
            <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5 w-fit">
              {[
                ...(isCrypto ? [] : [{ id: 'orb', label: 'ORB' }]),
                { id: 'ema',      label: 'EMA' },
                { id: 'donchian', label: 'Donchian' },
                { id: 'trend_sr', label: 'Trend/SR' },
              ].map(({ id, label }) => (
                <button key={id}
                  onClick={() => setStrategyName(id)}
                  className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                    mode === id ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>

            {/* ── ORB settings ──────────────────────────────────────────── */}
            {mode === 'orb' && (
              <div className="space-y-2.5 pt-1">
                <p className="text-xs text-slate-500 leading-relaxed">
                  <span className="text-slate-300 font-medium">Opening Range Breakout</span> — waits during the first N minutes after open to define the high/low range, then buys a breakout above the range or shorts below it. Exits by EOD. Best for volatile stocks on active mornings.
                </p>
                <div>
                  <Label tip="How many minutes after 09:30 ET the bot waits to build the 'opening range'. 15 minutes (09:30–09:45) is the classic setting. A wider range means fewer but stronger signals.">
                    Opening range (minutes)
                  </Label>
                  <NumInput value={cfg.strategy.orb.opening_range_minutes} min={1} max={60}
                    onChange={v => setOrb('opening_range_minutes', v)} />
                </div>
                <div>
                  <Label tip="Limit orders guarantee your entry price but may not fill if the price moves away. Market orders fill immediately at the current price — better for fast breakouts.">
                    Entry order type
                  </Label>
                  <select
                    className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                    value={cfg.strategy.orb.entry_order_type}
                    onChange={e => setOrb('entry_order_type', e.target.value)}
                  >
                    <option value="limit">Limit (fill at exact price)</option>
                    <option value="market">Market (fill immediately)</option>
                  </select>
                </div>
                <div>
                  <Label tip="Time (Eastern) at which the bot closes all open positions regardless of P&L. Must be before 16:00. 15:50 gives 10 minutes of buffer before market close.">
                    EOD exit time (ET, HH:MM)
                  </Label>
                  <input
                    className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                    value={cfg.strategy.orb.eod_exit_time}
                    onChange={e => setOrb('eod_exit_time', e.target.value)}
                    placeholder="15:50"
                  />
                </div>
              </div>
            )}

            {/* ── EMA settings ──────────────────────────────────────────── */}
            {mode === 'ema' && (
              <div className="space-y-2.5 pt-1">
                <p className="text-xs text-slate-500 leading-relaxed">
                  <span className="text-slate-300 font-medium">EMA Crossover</span> — buys when the fast EMA crosses above the slow EMA (golden cross), sells on the reverse (death cross). Can trade multiple times per day. Exits all positions by EOD.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label tip="The fast EMA reacts quickly to price changes. A smaller number (e.g. 9) catches trends early but generates more false signals. Common: 9, 12, 20.">
                      Fast EMA (minutes)
                    </Label>
                    <NumInput value={cfg.strategy.ema.fast_period} min={2} max={200}
                      onChange={v => setEma('fast_period', v)} />
                  </div>
                  <div>
                    <Label tip="The slow EMA filters out noise. Must be larger than the fast EMA. Common pairings: 9/21, 12/26, 20/50.">
                      Slow EMA (minutes)
                    </Label>
                    <NumInput value={cfg.strategy.ema.slow_period} min={3} max={500}
                      onChange={v => setEma('slow_period', v)} />
                  </div>
                </div>
                <div>
                  <Label tip="Market orders are strongly recommended for EMA crossovers — the signal is time-sensitive and a limit order risks not filling during a fast move.">
                    Entry order type
                  </Label>
                  <select
                    className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                    value={cfg.strategy.ema.entry_order_type}
                    onChange={e => setEma('entry_order_type', e.target.value)}
                  >
                    <option value="market">Market (recommended for EMA)</option>
                    <option value="limit">Limit</option>
                  </select>
                </div>
                <div>
                  <Label tip="Time (Eastern) at which the bot closes all open positions regardless of P&L.">
                    EOD exit time (ET, HH:MM)
                  </Label>
                  <input
                    className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                    value={cfg.strategy.ema.eod_exit_time}
                    onChange={e => setEma('eod_exit_time', e.target.value)}
                    placeholder="15:50"
                  />
                </div>
              </div>
            )}

            {/* ── Donchian settings ─────────────────────────────────────── */}
            {mode === 'donchian' && (
              <div className="space-y-2.5 pt-1">
                <p className="text-xs text-slate-500 leading-relaxed">
                  <span className="text-slate-300 font-medium">Donchian Channel Breakout</span> — daily swing strategy. Scans all symbols every day at 16:05 ET and buys any stock that closes at a new N-day high. Holds positions for days to weeks. Can trade multiple symbols simultaneously. Orders are placed at the next morning's open (09:31 ET).
                </p>

                <div className="rounded-lg bg-blue-900/20 border border-blue-800/40 p-2.5 text-[11px] text-blue-300 space-y-0.5">
                  <p className="font-semibold">How it works day-to-day:</p>
                  <p>16:05 ET → bot scans all symbols for breakouts</p>
                  <p>09:31 ET next day → market orders placed at open</p>
                  <p>Every 60s during market hours → stop prices checked</p>
                  <p>Position held until stop hit or channel reversal</p>
                </div>

                <div>
                  <Label tip="The strategy buys when today's closing price is higher than the highest high of the last N days. A larger lookback (e.g. 40) means fewer but stronger/more meaningful breakouts. Backtested optimum: 40 days.">
                    Lookback days (channel width)
                  </Label>
                  <NumInput value={cfg.strategy.donchian.lookback_days} min={5} max={200}
                    onChange={v => setDon('lookback_days', v)} />
                  <p className="text-[10px] text-slate-600 mt-0.5">Recommended: 40 (best backtested result)</p>
                </div>

                <div>
                  <Label tip="Only buy when the price is above its N-day moving average. This filters out signals during bear markets (e.g. 2008, 2022). Set to 200 for the classic 200-day MA filter. Set to 0 to disable.">
                    Trend MA filter (0 = off)
                  </Label>
                  <NumInput value={cfg.strategy.donchian.trend_ma} min={0} max={500}
                    onChange={v => setDon('trend_ma', v)} />
                  <p className="text-[10px] text-slate-600 mt-0.5">Recommended: 200 (200-day moving average)</p>
                </div>

                <div>
                  <Label tip="After the position gains this % in profit, the trailing stop activates. Until then, only the initial ATR-based stop is used. E.g. 1.0 means: once the trade is up 1%, start trailing.">
                    Trailing stop activates after (%)
                  </Label>
                  <NumInput value={cfg.strategy.donchian.trailing_activation_pct} step={0.5} min={0} max={20}
                    onChange={v => setDon('trailing_activation_pct', v)} />
                  <p className="text-[10px] text-slate-600 mt-0.5">Recommended: 1.0% — locks in profit early</p>
                </div>

                <div>
                  <Label tip="Once the trailing stop activates, the stop price follows the highest price reached, keeping a distance of this %. E.g. 8 means the stop stays 8% below the peak. Lets winners run while protecting profits.">
                    Trailing stop distance (%)
                  </Label>
                  <NumInput value={cfg.strategy.donchian.trailing_pct} step={0.5} min={1} max={30}
                    onChange={v => setDon('trailing_pct', v)} />
                  <p className="text-[10px] text-slate-600 mt-0.5">Recommended: 8% — wide enough for daily swings</p>
                </div>

                <div className="flex items-start gap-2 pt-0.5">
                  <input
                    type="checkbox" id="long-only"
                    checked={cfg.strategy.donchian.long_only}
                    onChange={e => setDon('long_only', e.target.checked)}
                    className="mt-0.5 accent-blue-500"
                  />
                  <div>
                    <label htmlFor="long-only" className="text-xs text-slate-300 font-medium cursor-pointer flex items-center gap-1">
                      Long only (no short selling)
                      <Tip text="When ON, the bot only buys stocks (never shorts). Recommended for most traders — short selling requires margin and is riskier. Turn OFF only if you want the bot to short stocks breaking down below the channel low." />
                    </label>
                    <p className="text-[10px] text-slate-600 mt-0.5">Recommended: ON — short selling adds significant risk</p>
                  </div>
                </div>
              </div>
            )}

            {/* ── Trend/SR settings ─────────────────────────────────────── */}
            {mode === 'trend_sr' && (
              <div className="space-y-2.5 pt-1">
                <p className="text-xs text-slate-500 leading-relaxed">
                  <span className="text-slate-300 font-medium">Trend + Support/Resistance</span> — buys breakouts above the
                  latest swing-high resistance while the fast MA is above the slow MA (uptrend). Stops use the nearest support
                  or an ATR distance, then trail the peak. Built for 24/7 crypto; long-only is recommended.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label tip="Timeframe the strategy trades on. The live 1-minute feed is aggregated into candles of this many minutes (e.g. 15). Higher = less noise, fewer/cleaner trades. This is the single biggest lever against whipsaw.">
                      Timeframe (minutes)
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.bar_minutes} min={1} max={1440}
                      onChange={v => setTsr('bar_minutes', v)} />
                    <p className="text-[10px] text-slate-600 mt-0.5">15 = 15-minute candles (recommended)</p>
                  </div>
                  <div>
                    <Label tip="Long-term regime filter: only take longs when price is above this MA (and below it for shorts). Set to 200 to avoid buying in downtrends — the bot stays flat instead. 0 disables it.">
                      Regime MA <span className="text-slate-600">(0 = off)</span>
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.regime_ma} min={0} max={1000}
                      onChange={v => setTsr('regime_ma', v)} />
                    <p className="text-[10px] text-slate-600 mt-0.5">200 = only trade with the trend</p>
                  </div>
                  <div>
                    <Label tip="Fast moving-average period. Must be smaller than the slow MA. The fast>slow relationship defines the uptrend filter that gates long entries.">
                      Fast MA
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.ma_fast} min={2} max={400}
                      onChange={v => setTsr('ma_fast', v)} />
                  </div>
                  <div>
                    <Label tip="Slow moving-average period. Entries only fire when the fast MA is above this (longs) — keeps you trading with the trend.">
                      Slow MA
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.ma_slow} min={3} max={800}
                      onChange={v => setTsr('ma_slow', v)} />
                  </div>
                  <div>
                    <Label tip="How many bars back to scan for swing-high / swing-low pivots that define resistance and support levels.">
                      Pivot lookback
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.pivot_lookback} min={2} max={200}
                      onChange={v => setTsr('pivot_lookback', v)} />
                  </div>
                  <div>
                    <Label tip="Bars required on each side of a pivot to confirm it. Higher = fewer but stronger S/R levels. 3 is a good default.">
                      Pivot strength
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.pivot_strength} min={1} max={20}
                      onChange={v => setTsr('pivot_strength', v)} />
                  </div>
                  <div>
                    <Label tip="Lookback period for the Average True Range used to size the initial stop distance.">
                      ATR period
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.atr_period} min={2} max={100}
                      onChange={v => setTsr('atr_period', v)} />
                  </div>
                  <div>
                    <Label tip="Initial stop = entry − ATR × this multiplier (bounded by the nearest support). Larger = more room, fewer stop-outs.">
                      ATR multiplier
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.atr_mult} step={0.1} min={0.1} max={20}
                      onChange={v => setTsr('atr_mult', v)} />
                  </div>
                  <div>
                    <Label tip="The close must clear resistance by this fraction of ATR before entering, so marginal pokes above a level don't trigger a trade. 0.25 = a quarter-ATR buffer.">
                      Breakout buffer (×ATR)
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.breakout_buffer_atr} step={0.05} min={0} max={5}
                      onChange={v => setTsr('breakout_buffer_atr', v)} />
                  </div>
                  <div>
                    <Label tip="After an exit, wait this many candles before re-entering the same symbol. Prevents instant re-buy churn on a chopping level. In 15m candles, 4 = one hour.">
                      Re-entry cooldown (bars)
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.cooldown_bars} step={1} min={0} max={100}
                      onChange={v => setTsr('cooldown_bars', v)} />
                  </div>
                  <div>
                    <Label tip="Once the trade is up this %, the trailing stop activates and follows the peak.">
                      Trail activates after (%)
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.trailing_activation_pct} step={0.5} min={0} max={50}
                      onChange={v => setTsr('trailing_activation_pct', v)} />
                  </div>
                  <div>
                    <Label tip="Once active, the stop trails this % below the highest price reached. Wider = lets winners run further.">
                      Trailing distance (%)
                    </Label>
                    <NumInput value={cfg.strategy.trend_sr.trailing_pct} step={0.5} min={0} max={50}
                      onChange={v => setTsr('trailing_pct', v)} />
                  </div>
                </div>
                <div className="flex items-start gap-2 pt-0.5">
                  <input
                    type="checkbox" id="tsr-long-only"
                    checked={cfg.strategy.trend_sr.long_only}
                    onChange={e => setTsr('long_only', e.target.checked)}
                    className="mt-0.5 accent-blue-500"
                  />
                  <label htmlFor="tsr-long-only" className="text-xs text-slate-300 font-medium cursor-pointer flex items-center gap-1">
                    Long only (no short selling)
                    <Tip text="Crypto cannot be shorted on Alpaca, so keep this ON for crypto profiles. On stocks you may turn it off to allow shorts on breakdowns below support." />
                  </label>
                </div>

                {/* ── Optional entry filters ─────────────────────────────── */}
                <div className="pt-3 mt-1 border-t border-slate-700/60">
                  <p className="text-xs font-semibold text-slate-300 mb-2">
                    Entry filters <span className="text-slate-500 font-normal">(optional — 0 = off · backtest before enabling)</span>
                  </p>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                    <div>
                      <Label tip="Require Wilder's ADX ≥ this before taking a breakout. ADX measures trend STRENGTH (0–100): below ~20 the market is choppy. Skips breakouts with no real trend behind them, so a rejected breakout is dropped entirely (not just delayed). 0 = off. 20–25 typical.">
                        Min ADX
                      </Label>
                      <NumInput value={cfg.strategy.trend_sr.min_adx} step={1} min={0} max={100}
                        onChange={v => setTsr('min_adx', v)} />
                    </div>
                    <div>
                      <Label tip="Smoothing window for the ADX calculation. Standard is 14. Only matters when Min ADX > 0.">
                        ADX period
                      </Label>
                      <NumInput value={cfg.strategy.trend_sr.adx_period} step={1} min={2} max={100}
                        onChange={v => setTsr('adx_period', v)} />
                    </div>
                    <div>
                      <Label tip="Require the breakout bar's volume to be ≥ this multiple of the recent average volume. Filters out low-conviction breakouts. 0 = off. 1.2–1.5 typical. Note: Alpaca crypto volume is single-venue (partial), so it's noisier for crypto than for stocks.">
                        Volume × (min)
                      </Label>
                      <NumInput value={cfg.strategy.trend_sr.volume_mult} step={0.1} min={0} max={10}
                        onChange={v => setTsr('volume_mult', v)} />
                    </div>
                    <div>
                      <Label tip="How many bars to average for the volume filter's baseline. Default 20. Only matters when Volume × > 0.">
                        Volume avg (bars)
                      </Label>
                      <NumInput value={cfg.strategy.trend_sr.volume_ma} step={1} min={2} max={500}
                        onChange={v => setTsr('volume_ma', v)} />
                    </div>
                  </div>
                </div>
              </div>
            )}
          </Section>
        </div>
      </div>

      {/* ── Save buttons ──────────────────────────────────────────────────── */}
      <div className="px-4 pb-4 flex items-center gap-3 flex-wrap">
        <button
          onClick={() => void handleSave(false)}
          disabled={saving}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-blue-700 hover:bg-blue-600 disabled:opacity-40 transition-colors"
        >
          <Save size={14} />
          Save
        </button>
        <button
          onClick={() => void handleSave(true)}
          disabled={saving}
          className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 disabled:opacity-40 transition-colors"
        >
          <RotateCcw size={14} />
          Save &amp; Restart Bot
        </button>
        {msg && (
          <span className={`text-xs font-medium ${msg.ok ? 'text-green-400' : 'text-red-400'}`}>
            {msg.text}
          </span>
        )}
      </div>
    </div>
  )
}
