import { useEffect, useState } from 'react'
import { Save, RotateCcw } from 'lucide-react'
import { apiPost, apiPut } from '../hooks/useApi'
import type { Config } from '../types'

interface Props {
  onRestart: () => void
}

const DEFAULT: Config = {
  live: false,
  symbols: ['SPY', 'AAPL', 'MSFT', 'NVDA'],
  risk: { max_position_usd: 5000, stop_loss_pct: 1.0, daily_loss_limit_usd: 500, max_open_positions: 4 },
  strategy: { name: 'orb', orb: { opening_range_minutes: 15, entry_order_type: 'limit', eod_exit_time: '15:50' } },
}

export default function ConfigEditor({ onRestart }: Props) {
  const [cfg, setCfg] = useState<Config>(DEFAULT)
  const [symbolsText, setSymbolsText] = useState('')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then((data: Config) => {
        setCfg(data)
        setSymbolsText(data.symbols.join(', '))
      })
      .catch((err: unknown) => {
        if (err instanceof SyntaxError) console.error('[config] JSON parse error:', err)
        setSymbolsText(DEFAULT.symbols.join(', '))
      })
  }, [])

  function setRisk(key: keyof Config['risk'], val: number) {
    setCfg(prev => ({ ...prev, risk: { ...prev.risk, [key]: val } }))
  }

  function setOrb(key: keyof Config['strategy']['orb'], val: string | number) {
    setCfg(prev => ({
      ...prev,
      strategy: { ...prev.strategy, orb: { ...prev.strategy.orb, [key]: val } },
    }))
  }

  async function handleSave(andRestart: boolean) {
    setSaving(true)
    setMsg(null)
    const symbols = symbolsText.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    const payload: Config = { ...cfg, symbols }
    try {
      await apiPut('/api/config', payload)
      if (andRestart) {
        await apiPost('/api/bot/restart')
        onRestart()
      }
      setMsg({ text: andRestart ? 'Saved & restarted.' : 'Saved.', ok: true })
    } catch (err) {
      setMsg({ text: String(err), ok: false })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700">
      <div className="px-4 py-3 border-b border-slate-700">
        <h2 className="text-sm font-semibold text-slate-200">Configuration</h2>
        <p className="text-xs text-slate-500 mt-0.5">Changes take effect on next bot start. API credentials stay in .env.</p>
      </div>

      <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Symbols */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Symbols</h3>
          <div>
            <label className="text-xs text-slate-500">Watch list (comma-separated)</label>
            <input
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
              value={symbolsText}
              onChange={e => setSymbolsText(e.target.value)}
              placeholder="SPY, AAPL, MSFT"
            />
          </div>
          <div className="flex items-center gap-2 mt-1">
            <input
              type="checkbox"
              id="live-mode"
              checked={cfg.live}
              onChange={e => setCfg(prev => ({ ...prev, live: e.target.checked }))}
              className="accent-red-500"
            />
            <label htmlFor="live-mode" className="text-xs text-red-400 font-medium cursor-pointer">
              Live trading mode (real money!)
            </label>
          </div>
        </div>

        {/* Risk */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">Risk Parameters</h3>
          {(
            [
              { key: 'max_position_usd', label: 'Max position (USD)', step: 100 },
              { key: 'stop_loss_pct', label: 'Stop loss (%)', step: 0.1 },
              { key: 'daily_loss_limit_usd', label: 'Daily loss limit (USD)', step: 50 },
              { key: 'max_open_positions', label: 'Max open positions', step: 1 },
            ] as { key: keyof Config['risk']; label: string; step: number }[]
          ).map(({ key, label, step }) => (
            <div key={key}>
              <label className="text-xs text-slate-500">{label}</label>
              <input
                type="number"
                step={step}
                min={0}
                className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
                value={cfg.risk[key]}
                onChange={e => setRisk(key, parseFloat(e.target.value))}
              />
            </div>
          ))}
        </div>

        {/* Strategy */}
        <div className="space-y-2">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">ORB Strategy</h3>
          <div>
            <label className="text-xs text-slate-500">Opening range (minutes)</label>
            <input
              type="number"
              min={1} max={60}
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
              value={cfg.strategy.orb.opening_range_minutes}
              onChange={e => setOrb('opening_range_minutes', parseInt(e.target.value))}
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">Entry order type</label>
            <select
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
              value={cfg.strategy.orb.entry_order_type}
              onChange={e => setOrb('entry_order_type', e.target.value)}
            >
              <option value="limit">Limit</option>
              <option value="market">Market</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500">EOD exit time (ET, HH:MM)</label>
            <input
              className="mt-1 w-full bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
              value={cfg.strategy.orb.eod_exit_time}
              onChange={e => setOrb('eod_exit_time', e.target.value)}
              placeholder="15:50"
            />
          </div>
        </div>
      </div>

      <div className="px-4 pb-4 flex items-center gap-3">
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
