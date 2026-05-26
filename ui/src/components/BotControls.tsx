import { useState, useEffect } from 'react'
import { Play, Square, RotateCcw, ChevronDown, ChevronUp, Terminal } from 'lucide-react'

import type { BotStatus } from '../types'

interface Props {
  botStatus: BotStatus
  onStatusChange: () => void
}

export default function BotControls({ botStatus, onStatusChange }: Props) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [stderrLog, setStderrLog] = useState<string>('')
  const [showLog, setShowLog] = useState(false)
  const [prevRunning, setPrevRunning] = useState(botStatus.running)

  // When the bot transitions from running → stopped, fetch the crash log
  useEffect(() => {
    if (prevRunning && !botStatus.running) {
      fetchStderr()
    }
    setPrevRunning(botStatus.running)
  }, [botStatus.running])

  async function fetchStderr() {
    try {
      const res = await fetch('/api/bot/stderr')
      const data = await res.json() as { log: string }
      if (data.log.trim()) {
        setStderrLog(data.log)
        setShowLog(true)   // auto-open if there's something to show
      }
    } catch {
      // ignore
    }
  }

  async function call(endpoint: string) {
    setLoading(true)
    setError(null)
    if (endpoint === 'start') {
      setStderrLog('')
      setShowLog(false)
    }
    try {
      const res = await fetch(`/api/bot/${endpoint}`, { method: 'POST' })
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(`Server error ${res.status}${text ? ': ' + text.slice(0, 120) : ''}`)
      }
      const data = await res.json() as { ok?: boolean; error?: string }
      if (!data.ok && data.error) {
        setError(data.error)
      } else {
        onStatusChange()
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  // Determine if the log looks like a crash (non-empty + bot not running)
  const hasCrashLog = stderrLog.trim().length > 0 && !botStatus.running

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <button
          onClick={() => void call('start')}
          disabled={loading || botStatus.running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-green-700 hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Play size={14} />
          Start
        </button>
        <button
          onClick={() => void call('stop')}
          disabled={loading || !botStatus.running}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <Square size={14} />
          Stop
        </button>
        <button
          onClick={() => void call('restart')}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          <RotateCcw size={14} />
          Restart
        </button>

        {/* Crash log toggle — only visible when there's a log */}
        {hasCrashLog && (
          <button
            onClick={() => setShowLog(v => !v)}
            className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium bg-red-900/60 text-red-300 hover:bg-red-900 transition-colors"
          >
            <Terminal size={12} />
            Crash log
            {showLog ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}

        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>

      {/* Crash log panel */}
      {showLog && stderrLog && (
        <div className="mt-1 rounded-lg border border-red-800/50 bg-slate-950 overflow-hidden">
          <div className="flex items-center justify-between px-3 py-1.5 bg-red-900/30 border-b border-red-800/50">
            <span className="text-xs font-semibold text-red-300 flex items-center gap-1.5">
              <Terminal size={11} /> Bot crash output
            </span>
            <button
              onClick={() => setShowLog(false)}
              className="text-slate-500 hover:text-slate-300 text-xs"
            >
              ✕
            </button>
          </div>
          <pre className="p-3 text-xs text-red-200 font-mono whitespace-pre-wrap break-all max-h-48 overflow-y-auto leading-relaxed">
            {stderrLog}
          </pre>
        </div>
      )}
    </div>
  )
}
