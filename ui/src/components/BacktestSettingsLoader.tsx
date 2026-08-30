import { useEffect, useRef, useState } from 'react'
import { FileUp, Boxes, AlertTriangle, X } from 'lucide-react'

export interface BacktestParams { [key: string]: string | number | boolean }

interface ProfileRow { slug: string; name: string; strategy: string }

interface Props {
  /** Apply loaded settings to the form. */
  onLoad: (params: BacktestParams, source: string) => void
}

/** Load the backtest form from a profile, or from a settings CSV.
 *
 *  The gap this closes: the panel had ~25 knobs that had to be retyped to match
 *  whatever the bot was actually running, so the thing you tested and the thing
 *  you ran drifted apart silently. Now they're the same file.
 *
 *  Deliberately distinct from the bars upload above it. Both take a .csv, and
 *  mixing them up is the obvious failure — so this one is labelled by what the
 *  file contains rather than by its extension, and the server rejects a
 *  bars-sized file here with a message pointing back up the page. */
export default function BacktestSettingsLoader({ onLoad }: Props) {
  const [profiles, setProfiles] = useState<ProfileRow[]>([])
  const [slug, setSlug] = useState('')
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState<string[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [loaded, setLoaded] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    fetch('/api/profiles')
      .then(r => r.json())
      .then((rows: ProfileRow[]) => {
        setProfiles(rows)
        if (rows.length && !slug) setSlug(rows[0].slug)
      })
      .catch(() => setProfiles([]))
    // Runs once: the profile list is not expected to change mid-backtest.

  }, [])

  function accept(json: { params: BacktestParams; warnings?: string[]; source: string }) {
    setErrors([])
    setWarnings(json.warnings ?? [])
    setLoaded(json.source)
    onLoad(json.params, json.source)
  }

  async function fromProfile() {
    if (!slug) return
    setBusy(true); setErrors([]); setWarnings([]); setLoaded('')
    try {
      const res = await fetch(`/api/backtest/params/from-profile/${encodeURIComponent(slug)}`)
      const json: unknown = await res.json().catch(() => null)
      if (!res.ok) {
        const d = (json as { detail?: unknown } | null)?.detail
        setErrors([typeof d === 'string' ? d : `HTTP ${res.status}`])
        return
      }
      accept(json as { params: BacktestParams; warnings?: string[]; source: string })
    } catch (err) {
      setErrors([String(err)])
    } finally { setBusy(false) }
  }

  async function fromCsv(file: File) {
    setBusy(true); setErrors([]); setWarnings([]); setLoaded('')
    const body = new FormData()
    body.append('file', file)
    if (slug) body.append('profile', slug)
    try {
      const res = await fetch('/api/backtest/params/from-csv', { method: 'POST', body })
      const json: unknown = await res.json().catch(() => null)
      if (!res.ok) {
        const d = (json as { detail?: unknown } | null)?.detail
        const list = (d as { errors?: string[] } | undefined)?.errors
        setErrors(list ?? [typeof d === 'string' ? d : `HTTP ${res.status}`])
        return
      }
      accept(json as { params: BacktestParams; warnings?: string[]; source: string })
    } catch (err) {
      setErrors([String(err)])
    } finally { setBusy(false) }
  }

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/40 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-slate-300">Load settings</span>
        <span className="text-[11px] text-slate-500">
          test what the bot is actually configured to run
        </span>
      </div>

      <div className="flex items-end gap-2 flex-wrap">
        <div>
          <label className="text-[11px] text-slate-500 block mb-1" htmlFor="bt-profile">
            From profile
          </label>
          <select
            id="bt-profile"
            value={slug}
            onChange={e => setSlug(e.target.value)}
            className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-1.5 text-sm text-slate-100 focus:outline-none focus:border-blue-500"
          >
            {profiles.length === 0 && <option value="">no profiles</option>}
            {profiles.map(p => (
              <option key={p.slug} value={p.slug}>{p.name} · {p.strategy}</option>
            ))}
          </select>
        </div>
        <button
          type="button"
          onClick={() => void fromProfile()}
          disabled={busy || !slug}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-700 hover:bg-slate-600 border border-slate-600 text-slate-200 disabled:opacity-40 transition-colors"
        >
          <Boxes size={13} aria-hidden="true" />
          Load
        </button>

        <span className="text-[11px] text-slate-600 px-1 self-center">or</span>

        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-700 hover:bg-slate-600 border border-slate-600 text-slate-200 disabled:opacity-40 transition-colors"
        >
          <FileUp size={13} aria-hidden="true" />
          {busy ? 'Reading…' : 'Settings CSV…'}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          aria-label="Load backtest settings from a CSV file"
          onChange={e => {
            const f = e.target.files?.[0]
            if (f) void fromCsv(f)
            e.target.value = ''
          }}
        />

        {loaded && (
          <span className="text-xs text-green-400 font-medium self-center">
            loaded from {loaded}
          </span>
        )}
      </div>

      <p className="text-[11px] text-slate-600">
        A settings CSV — the same file the Config tab exports. Not price bars;
        those go in the data upload above.
      </p>

      {errors.length > 0 && (
        <div role="alert" className="rounded-lg border border-red-800 bg-red-950/60 p-2 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-red-300 mb-1">
            <X size={12} aria-hidden="true" /> Settings not loaded
          </div>
          <ul className="space-y-0.5 text-red-200/90 list-disc pl-4">
            {errors.map(e => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      {warnings.map(w => (
        <p key={w} className="flex items-start gap-1.5 text-[11px] text-amber-300">
          <AlertTriangle size={11} className="mt-0.5 shrink-0" aria-hidden="true" />
          {w}
        </p>
      ))}
    </div>
  )
}
