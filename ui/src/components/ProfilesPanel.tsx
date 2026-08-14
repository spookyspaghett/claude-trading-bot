import { useEffect, useId, useState } from 'react'
import { Plus, Check, Trash2, Pencil, Power, X, Coins, LineChart, KeyRound } from 'lucide-react'
import { apiPost, apiPut } from '../hooks/useApi'
import { Wallet } from 'lucide-react'
import type { AssetClass, ProfileSummary } from '../types'

interface Props {
  runningSlugs: string[]
  onActivated: () => void
}

const STRATEGIES_BY_ASSET: Record<AssetClass, { id: string; label: string }[]> = {
  stock: [
    { id: 'orb', label: 'ORB' },
    { id: 'ema', label: 'EMA' },
    { id: 'donchian', label: 'Donchian' },
    { id: 'trend_sr', label: 'Trend/SR' },
    { id: 'vwap_revert', label: 'VWAP Reversion' },
  ],
  crypto: [
    { id: 'trend_sr', label: 'Trend/SR (recommended)' },
    { id: 'ema', label: 'EMA' },
    { id: 'donchian', label: 'Donchian' },
    { id: 'vwap_revert', label: 'VWAP Reversion' },
  ],
}

const SYMBOL_SUGGESTIONS: Record<AssetClass, string> = {
  stock: 'SPY, AAPL, MSFT, NVDA, QQQ, TSLA',
  crypto: 'BTC/USD, ETH/USD, SOL/USD, LTC/USD',
}

interface FormState {
  slug: string | null          // null = creating new
  name: string
  asset_class: AssetClass
  live: boolean
  alpaca_api_key: string
  alpaca_secret_key: string
  symbolsText: string
  strategyName: string
  max_position_usd: number
  stop_loss_pct: number
  daily_loss_limit_usd: number
  max_open_positions: number
}

const BLANK_FORM: FormState = {
  slug: null,
  name: '',
  asset_class: 'crypto',
  live: false,
  alpaca_api_key: '',
  alpaca_secret_key: '',
  symbolsText: 'BTC/USD, ETH/USD',
  strategyName: 'trend_sr',
  max_position_usd: 200,
  stop_loss_pct: 3.0,
  daily_loss_limit_usd: 50,
  max_open_positions: 2,
}

function AssetBadge({ asset }: { asset: AssetClass }) {
  const crypto = asset === 'crypto'
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-1.5 py-0.5 rounded border ${
      crypto ? 'text-amber-300 bg-amber-950 border-amber-800' : 'text-sky-300 bg-sky-950 border-sky-800'
    }`}>
      {crypto ? <Coins size={10} /> : <LineChart size={10} />}
      {crypto ? 'CRYPTO' : 'STOCK'}
    </span>
  )
}

export default function ProfilesPanel({ runningSlugs, onActivated }: Props) {
  const [profiles, setProfiles] = useState<ProfileSummary[]>([])
  const [form, setForm] = useState<FormState | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  // Deleting a profile is irreversible, so it takes two clicks rather than one.
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const fid = useId()   // stable prefix so every label can address its control

  async function load() {
    try {
      const res = await fetch('/api/profiles')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setProfiles(await res.json() as ProfileSummary[])
    } catch {
      // A dead API used to surface as a raw "SyntaxError: Unexpected end of
      // JSON input", which tells you nothing about what to do next. The
      // overwhelmingly likely cause on a fresh clone is simply that the
      // backend isn't up yet, so say that and give the command.
      setMsg({
        text: 'Can’t reach the dashboard API — start the server with '
            + '“uvicorn api.main:app --reload”, then reload this page.',
        ok: false,
      })
    }
  }

  useEffect(() => { void load() }, [])

  function startCreate() {
    setMsg(null)
    setForm({ ...BLANK_FORM })
  }

  async function startEdit(slug: string) {
    setMsg(null)
    try {
      const res = await fetch(`/api/profiles/${slug}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const p = await res.json() as Record<string, unknown>
      const risk = (p.risk ?? {}) as Record<string, number>
      const strat = (p.strategy ?? {}) as Record<string, string>
      setForm({
        slug,
        name: String(p.name ?? ''),
        asset_class: (p.asset_class as AssetClass) ?? 'stock',
        live: Boolean(p.live),
        alpaca_api_key: String(p.alpaca_api_key ?? ''),
        alpaca_secret_key: String(p.alpaca_secret_key ?? ''),
        symbolsText: ((p.symbols as string[]) ?? []).join(', '),
        strategyName: strat.name ?? 'trend_sr',
        max_position_usd: risk.max_position_usd ?? 200,
        stop_loss_pct: risk.stop_loss_pct ?? 3.0,
        daily_loss_limit_usd: risk.daily_loss_limit_usd ?? 50,
        max_open_positions: risk.max_open_positions ?? 2,
      })
    } catch (err) {
      setMsg({ text: String(err), ok: false })
    }
  }

  function patch(p: Partial<FormState>) {
    setForm(prev => prev ? { ...prev, ...p } : prev)
  }

  async function save() {
    if (!form) return
    // Validate on submit and move focus to the offending field, rather than
    // greying out the button and leaving you to guess which one it wants.
    if (!form.name.trim()) {
      setMsg({ text: 'Enter a profile name — it labels the tab you’ll start this bot from.', ok: false })
      document.getElementById(`${fid}-name`)?.focus()
      return
    }
    setBusy(true); setMsg(null)
    const symbols = form.symbolsText.split(',').map(s => s.trim().toUpperCase()).filter(Boolean)
    const body = {
      name: form.name.trim(),
      asset_class: form.asset_class,
      live: form.live,
      alpaca_api_key: form.alpaca_api_key,
      alpaca_secret_key: form.alpaca_secret_key,
      symbols,
      risk: {
        max_position_usd: form.max_position_usd,
        stop_loss_pct: form.stop_loss_pct,
        daily_loss_limit_usd: form.daily_loss_limit_usd,
        max_open_positions: form.max_open_positions,
      },
      strategy: { name: form.strategyName },
    }
    try {
      if (form.slug) await apiPut(`/api/profiles/${form.slug}`, body)
      else await apiPost('/api/profiles', body)
      setForm(null)
      setMsg({ text: 'Profile saved.', ok: true })
      await load()
    } catch (err) {
      setMsg({ text: String(err), ok: false })
    } finally {
      setBusy(false)
    }
  }

  async function activate(slug: string) {
    setBusy(true); setMsg(null)
    try {
      await apiPost(`/api/profiles/${slug}/activate`)
      setMsg({ text: 'Profile activated.', ok: true })
      await load()
      onActivated()
    } catch (err) {
      setMsg({ text: String(err), ok: false })
    } finally {
      setBusy(false)
    }
  }

  async function remove(slug: string) {
    setBusy(true); setMsg(null)
    try {
      const res = await fetch(`/api/profiles/${slug}`, { method: 'DELETE' })
      if (!res.ok) throw new Error((await res.json() as { detail?: string }).detail ?? `HTTP ${res.status}`)
      setMsg({ text: 'Profile deleted.', ok: true })
      await load()
    } catch (err) {
      setMsg({ text: String(err), ok: false })
    } finally {
      setBusy(false)
      setConfirmDelete(null)
    }
  }

  const strategyOptions = form ? STRATEGIES_BY_ASSET[form.asset_class] : []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h2 className="text-sm font-semibold text-slate-200">Trading Profiles</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Each profile has its own API keys, asset class, symbols, strategy and risk. Run several at once from their tabs.
          </p>
        </div>
        {!form && (
          <button onClick={startCreate}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 transition-colors">
            <Plus size={14} aria-hidden="true" /> New Profile
          </button>
        )}
      </div>

      {/* Saves and deletes resolve asynchronously — announce the outcome. */}
      <p aria-live="polite" className={`text-xs font-medium ${msg?.ok ? 'text-green-400' : 'text-red-400'}`}>
        {msg?.text ?? ''}
      </p>

      {/* ── Profile list ──────────────────────────────────────────────────── */}
      {!form && profiles.length === 0 && (
        // First run. An empty grid with one grey line reads like a failure, so
        // say plainly what this screen is for and what the next step is.
        <div className="card p-8 text-center max-w-lg mx-auto">
          <span aria-hidden="true" className="grid place-items-center w-12 h-12 rounded-xl mx-auto mb-4 bg-gradient-to-br from-blue-500 to-violet-600 shadow-lg shadow-blue-900/40">
            <Wallet size={22} className="text-white" />
          </span>
          <h3 className="text-base font-semibold text-slate-100 text-balance">
            Create Your First Profile
          </h3>
          <p className="text-sm text-slate-400 mt-2 text-pretty">
            A profile bundles your Alpaca keys, symbols, strategy and risk limits
            into one bot you can start and stop. Paper trading is the default —
            nothing touches real money until you turn it on.
          </p>
          <button onClick={startCreate}
            className="mt-5 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 transition-colors">
            <Plus size={14} aria-hidden="true" /> Create Profile
          </button>
        </div>
      )}
      {!form && profiles.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {profiles.map(p => (
            <div key={p.slug}
              className={`bg-slate-900 rounded-xl border p-4 flex flex-col gap-3 ${
                p.active ? 'border-green-700/70 ring-1 ring-green-700/40' : 'border-slate-700'
              }`}>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-semibold text-slate-100 truncate">{p.name}</span>
                    {p.active && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-green-400">
                        <Check size={11} /> DEFAULT
                      </span>
                    )}
                    {runningSlugs.includes(p.slug) && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-emerald-300">
                        ● RUNNING
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 mt-1.5">
                    <AssetBadge asset={p.asset_class} />
                    <span className="text-[10px] uppercase tracking-wide text-slate-500">{p.strategy}</span>
                    {p.live && <span className="text-[10px] font-bold text-red-400">LIVE</span>}
                  </div>
                </div>
              </div>

              <div className="text-xs text-slate-400 truncate">{p.symbols.join(', ') || '—'}</div>
              <div className="flex items-center gap-1.5 text-[10px] text-slate-600">
                <KeyRound size={10} />
                {p.has_keys ? 'API keys set' : 'no keys — add them'}
              </div>

              {confirmDelete === p.slug ? (
                <div className="flex items-center gap-1.5 mt-auto pt-1">
                  <span className="text-xs text-red-300 font-medium">Delete for good?</span>
                  <button
                    onClick={() => void remove(p.slug)}
                    disabled={busy}
                    className="px-2.5 py-1.5 rounded-md text-xs font-bold bg-red-600 hover:bg-red-500 disabled:opacity-50 transition-colors ml-auto">
                    {busy ? 'Deleting…' : 'Delete'}
                  </button>
                  <button onClick={() => setConfirmDelete(null)}
                    className="px-2.5 py-1.5 rounded-md text-xs font-medium bg-slate-700 hover:bg-slate-600 transition-colors">
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 mt-auto pt-1">
                  <button
                    onClick={() => void activate(p.slug)}
                    disabled={busy || p.active}
                    title="Make this the default profile for Backtest and slug-less views"
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-semibold bg-green-700 hover:bg-green-600 disabled:opacity-40 transition-colors">
                    <Power size={12} aria-hidden="true" /> {p.active ? 'Default' : 'Make Default'}
                  </button>
                  <button onClick={() => void startEdit(p.slug)} disabled={busy}
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium bg-slate-700 hover:bg-slate-600 disabled:opacity-40 transition-colors">
                    <Pencil size={12} aria-hidden="true" /> Edit
                  </button>
                  <button
                    onClick={() => setConfirmDelete(p.slug)}
                    disabled={busy || runningSlugs.includes(p.slug)}
                    aria-label={`Delete profile ${p.name}`}
                    title={runningSlugs.includes(p.slug) ? 'Stop this profile’s bot before deleting' : 'Delete profile'}
                    className="flex items-center gap-1 px-2 py-1.5 rounded-md text-xs font-medium bg-slate-800 hover:bg-red-900/60 text-slate-400 hover:text-red-300 disabled:opacity-40 transition-colors ml-auto">
                    <Trash2 size={12} aria-hidden="true" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── Create / edit form ────────────────────────────────────────────── */}
      {form && (
        <div className="card p-4 space-y-4 max-w-2xl">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-200">
              {form.slug ? `Edit “${form.name}”` : 'New Profile'}
            </h3>
            <button onClick={() => setForm(null)} aria-label="Close editor"
              className="text-slate-500 hover:text-slate-300 rounded">
              <X size={16} aria-hidden="true" />
            </button>
          </div>

          {/* Asset class toggle */}
          <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5 w-fit">
            {(['crypto', 'stock'] as AssetClass[]).map(a => (
              <button key={a}
                onClick={() => patch({
                  asset_class: a,
                  symbolsText: form.symbolsText || SYMBOL_SUGGESTIONS[a],
                  strategyName: STRATEGIES_BY_ASSET[a][0].id,
                })}
                className={`flex items-center gap-1 px-3 py-1.5 rounded-md text-xs font-semibold transition-colors ${
                  form.asset_class === a ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
                }`}>
                {a === 'crypto' ? <Coins size={12} /> : <LineChart size={12} />}
                {a === 'crypto' ? 'Crypto' : 'Stocks'}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="sm:col-span-2">
              <label htmlFor={`${fid}-name`} className="text-xs text-slate-500 block mb-1">Profile Name</label>
              <input id={`${fid}-name`} name="profile-name" value={form.name}
                onChange={e => patch({ name: e.target.value })}
                autoComplete="off" spellCheck={false}
                placeholder={form.asset_class === 'crypto' ? 'Crypto BTC swing…' : 'Stocks ORB…'}
                className="field" />
            </div>

            <div>
              <label htmlFor={`${fid}-key`} className="text-xs text-slate-500 block mb-1">Alpaca API Key</label>
              {/* Credentials, not a login: keep password managers out of it and
                  stop the browser spell-checking an opaque token. */}
              <input id={`${fid}-key`} name="alpaca-api-key" value={form.alpaca_api_key}
                onChange={e => patch({ alpaca_api_key: e.target.value })}
                autoComplete="off" spellCheck={false} translate="no"
                placeholder={form.slug ? '(unchanged)' : 'PK…'}
                className="field font-mono" />
            </div>
            <div>
              <label htmlFor={`${fid}-secret`} className="text-xs text-slate-500 block mb-1">Alpaca Secret Key</label>
              <input id={`${fid}-secret`} name="alpaca-secret-key" type="password"
                value={form.alpaca_secret_key}
                onChange={e => patch({ alpaca_secret_key: e.target.value })}
                autoComplete="new-password" spellCheck={false} translate="no"
                placeholder={form.slug ? '(unchanged)' : 'Paste your secret…'}
                className="field font-mono" />
            </div>

            <div className="sm:col-span-2">
              <label htmlFor={`${fid}-symbols`} className="text-xs text-slate-500 block mb-1">
                Symbols {form.asset_class === 'crypto' && <span className="text-slate-600">(use BASE/QUOTE, e.g. BTC/USD)</span>}
              </label>
              <input id={`${fid}-symbols`} name="symbols" value={form.symbolsText}
                onChange={e => patch({ symbolsText: e.target.value })}
                autoComplete="off" spellCheck={false} translate="no"
                placeholder={`${SYMBOL_SUGGESTIONS[form.asset_class]}…`}
                className="field" />
            </div>

            <div>
              <label htmlFor={`${fid}-strategy`} className="text-xs text-slate-500 block mb-1">Strategy</label>
              <select id={`${fid}-strategy`} name="strategy" value={form.strategyName}
                onChange={e => patch({ strategyName: e.target.value })}
                className="field">
                {strategyOptions.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
              </select>
              <p className="text-[10px] text-slate-600 mt-1">Fine-tune parameters in the Dashboard → Configuration panel.</p>
            </div>
            <div>
              <label htmlFor={`${fid}-maxpos`} className="text-xs text-slate-500 block mb-1">Max Position (USD)</label>
              <input id={`${fid}-maxpos`} name="max-position-usd" type="number" inputMode="decimal"
                min={10} step={10} value={form.max_position_usd}
                onChange={e => patch({ max_position_usd: parseFloat(e.target.value) || 0 })}
                className="field tabular-nums" />
            </div>
            <div>
              <label htmlFor={`${fid}-stop`} className="text-xs text-slate-500 block mb-1">Stop Loss %</label>
              <input id={`${fid}-stop`} name="stop-loss-pct" type="number" inputMode="decimal"
                min={0.1} step={0.1} value={form.stop_loss_pct}
                onChange={e => patch({ stop_loss_pct: parseFloat(e.target.value) || 0 })}
                className="field tabular-nums" />
            </div>
            <div>
              <label htmlFor={`${fid}-daily`} className="text-xs text-slate-500 block mb-1">Daily Loss Limit (USD)</label>
              <input id={`${fid}-daily`} name="daily-loss-limit-usd" type="number" inputMode="decimal"
                min={1} step={10} value={form.daily_loss_limit_usd}
                onChange={e => patch({ daily_loss_limit_usd: parseFloat(e.target.value) || 0 })}
                className="field tabular-nums" />
            </div>
            <div>
              <label htmlFor={`${fid}-open`} className="text-xs text-slate-500 block mb-1">Max Open Positions</label>
              <input id={`${fid}-open`} name="max-open-positions" type="number" inputMode="numeric"
                min={1} max={20} step={1} value={form.max_open_positions}
                onChange={e => patch({ max_open_positions: parseInt(e.target.value) || 1 })}
                className="field tabular-nums" />
            </div>
          </div>

          <label htmlFor={`${fid}-live`}
            className="flex items-center gap-2 cursor-pointer select-none w-fit">
            <input id={`${fid}-live`} name="live" type="checkbox" checked={form.live}
              onChange={e => patch({ live: e.target.checked })}
              className="w-4 h-4 accent-red-500" />
            <span className="text-xs text-red-400 font-medium">Live trading (real money!)</span>
          </label>

          <div className="flex items-center gap-2">
            {/* Stays enabled so submitting with an empty name surfaces the
                reason inline instead of leaving a dead button with no
                explanation. */}
            <button onClick={() => void save()} disabled={busy}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-blue-600 hover:bg-blue-500 disabled:opacity-40 transition-colors">
              <Check size={14} aria-hidden="true" />
              {busy ? 'Saving…' : form.slug ? 'Save Changes' : 'Create Profile'}
            </button>
            <button onClick={() => setForm(null)} disabled={busy}
              className="px-4 py-2 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 transition-colors">
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
