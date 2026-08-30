import { useRef, useState } from 'react'
import { Download, Upload, AlertTriangle, Check, X } from 'lucide-react'

interface Props {
  slug: string
  /** Re-read the config after a successful apply. */
  onImported: () => void
}

interface CsvChange { path: string; old: unknown; new: unknown }

interface CsvReport {
  mode: 'settings' | 'symbols'
  changes: CsvChange[]
  unchanged: string[]
  skipped: { path: string; reason: string }[]
  warnings: string[]
  applied: boolean
}

function fmt(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ')
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

/** Import and export the profile's settings as a CSV file.
 *
 *  The import is deliberately two steps. These rows are position sizing, loss
 *  limits and stop distances; a file picker that silently rewrote them the
 *  moment you chose a file would be the wrong shape for the job. Choosing a
 *  file only ever produces a diff — nothing is written until the diff is
 *  accepted. */
export default function SettingsCsv({ slug, onImported }: Props) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [report, setReport] = useState<CsvReport | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  // Held here rather than read back off the input: the input is cleared after
  // the preview so re-picking the same edited file still fires a change event,
  // which would otherwise leave Apply with nothing to send.
  const [file, setFile] = useState<File | null>(null)
  const [done, setDone] = useState('')
  const fileName = file?.name ?? ''

  async function send(file: File, apply: boolean) {
    setBusy(true); setErrors([]); setDone('')
    const body = new FormData()
    body.append('file', file)
    body.append('apply', String(apply))
    try {
      const res = await fetch(
        `/api/profiles/${encodeURIComponent(slug)}/settings/import`,
        { method: 'POST', body },
      )
      const json: unknown = await res.json().catch(() => null)
      if (!res.ok) {
        // 422 carries every problem in the file at once; 413 and the rest
        // carry a single string.
        const detail = (json as { detail?: unknown } | null)?.detail
        const list = (detail as { errors?: string[] } | undefined)?.errors
        setErrors(list ?? [typeof detail === 'string' ? detail : `HTTP ${res.status}`])
        setReport(null)
        return
      }
      const rep = json as CsvReport
      setReport(rep)
      if (rep.applied) {
        setDone(`Applied ${rep.changes.length} change${rep.changes.length === 1 ? '' : 's'}.`)
        onImported()
      }
    } catch (err) {
      setErrors([String(err)])
    } finally {
      setBusy(false)
    }
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = e.target.files?.[0]
    if (!picked) return
    setFile(picked)
    void send(picked, false)
    // Reset the input so re-picking the same file after an edit still fires.
    e.target.value = ''
  }

  function apply() {
    if (file) void send(file, true)
  }

  const pending = report && !report.applied && report.changes.length > 0

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <a
          href={`/api/profiles/${encodeURIComponent(slug)}/settings.csv`}
          download
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 transition-colors"
        >
          <Download size={13} aria-hidden="true" />
          Export CSV
        </a>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 border border-slate-600 text-slate-200 disabled:opacity-40 transition-colors"
        >
          <Upload size={13} aria-hidden="true" />
          {busy ? 'Reading…' : 'Import CSV'}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,text/csv"
          onChange={onPick}
          className="sr-only"
          aria-label="Choose a settings CSV to import"
        />
        {done && <span className="text-xs text-green-400 font-medium">{done}</span>}
      </div>

      {errors.length > 0 && (
        <div role="alert" className="rounded-lg border border-red-800 bg-red-950/60 p-2.5 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-red-300 mb-1">
            <X size={12} aria-hidden="true" />
            {fileName || 'That file'} was not applied
          </div>
          <ul className="space-y-0.5 text-red-200/90 list-disc pl-4">
            {errors.map(e => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      {report && (
        <div className="rounded-lg border border-slate-600 bg-slate-800/60 p-2.5 text-xs space-y-2">
          <div className="font-semibold text-slate-200">
            {fileName}
            {report.mode === 'symbols' && (
              <span className="ml-2 font-normal text-slate-400">read as a watchlist</span>
            )}
          </div>

          {report.changes.length === 0 ? (
            <p className="text-slate-400">
              No changes — this profile already matches the file.
            </p>
          ) : (
            <ul className="space-y-0.5 max-h-56 overflow-y-auto">
              {report.changes.map(c => (
                <li key={c.path} className="flex items-baseline gap-2 font-mono text-[11px]">
                  <span className="text-slate-400 shrink-0">{c.path}</span>
                  <span className="text-red-300 line-through">{fmt(c.old)}</span>
                  <span className="text-slate-500" aria-label="becomes">→</span>
                  <span className="text-green-300 font-semibold">{fmt(c.new)}</span>
                </li>
              ))}
            </ul>
          )}

          {report.warnings.map(w => (
            <p key={w} className="flex items-start gap-1.5 text-amber-300">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
              {w}
            </p>
          ))}

          {report.skipped.length > 0 && (
            <p className="text-slate-500">
              {report.skipped.length} row{report.skipped.length === 1 ? '' : 's'} skipped
              (no value given). {report.unchanged.length} already matched.
            </p>
          )}

          {pending && (
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={apply}
                disabled={busy}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-700 hover:bg-blue-600 disabled:opacity-40 transition-colors"
              >
                <Check size={13} aria-hidden="true" />
                Apply {report.changes.length} change{report.changes.length === 1 ? '' : 's'}
              </button>
              <button
                type="button"
                onClick={() => { setReport(null); setFile(null) }}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-700 hover:bg-slate-600 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
