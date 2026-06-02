import { useState } from 'react'
import { Zap } from 'lucide-react'
import { apiPost } from '../hooks/useApi'

interface Props {
  onTriggered: () => void
  slug: string
}

export default function KillSwitch({ onTriggered, slug }: Props) {
  const [confirming, setConfirming] = useState(false)
  const [loading, setLoading] = useState(false)
  const [triggered, setTriggered] = useState(false)

  async function handleConfirm() {
    setLoading(true)
    try {
      await apiPost(`/api/kill?profile=${encodeURIComponent(slug)}`)
      setTriggered(true)
      onTriggered()
    } catch {
      // still show triggered — KILL file might have been created
      setTriggered(true)
    } finally {
      setLoading(false)
      setConfirming(false)
    }
  }

  if (triggered) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-950 border border-red-700 text-red-400 text-sm font-semibold">
        <Zap size={14} className="fill-current" />
        KILL SWITCH ACTIVATED
      </div>
    )
  }

  if (confirming) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-red-400 font-medium">Flatten all positions?</span>
        <button
          onClick={() => void handleConfirm()}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg text-sm font-bold bg-red-600 hover:bg-red-500 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Executing…' : 'Confirm'}
        </button>
        <button
          onClick={() => setConfirming(false)}
          className="px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-700 hover:bg-slate-600 transition-colors"
        >
          Cancel
        </button>
      </div>
    )
  }

  return (
    <button
      onClick={() => setConfirming(true)}
      className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold bg-red-700 hover:bg-red-600 border border-red-500 transition-colors shadow-lg shadow-red-950"
    >
      <Zap size={14} />
      KILL SWITCH
    </button>
  )
}
