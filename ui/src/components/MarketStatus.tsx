import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

/** Live ET clock + US equity market open/closed chip (Mon–Fri 9:30–16:00 ET,
 *  holidays not modelled — this is a glanceable hint, not an oracle). */
export default function MarketStatus() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  // Read the ET wall-clock pieces in one formatToParts pass.
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false, weekday: 'short',
  }).formatToParts(now)
  const get = (type: string) => parts.find(p => p.type === type)?.value ?? ''
  const weekday = get('weekday')
  const hh = parseInt(get('hour'), 10)
  const mm = parseInt(get('minute'), 10)

  const minutes = hh * 60 + mm
  const isWeekday = !['Sat', 'Sun'].includes(weekday)
  const open = isWeekday && minutes >= 9 * 60 + 30 && minutes < 16 * 60
  const preMarket = isWeekday && minutes >= 4 * 60 && minutes < 9 * 60 + 30
  const afterHours = isWeekday && minutes >= 16 * 60 && minutes < 20 * 60

  const label = open ? 'Market open' : preMarket ? 'Pre-market' : afterHours ? 'After hours' : 'Market closed'
  const dot = open ? 'bg-green-400' : preMarket || afterHours ? 'bg-amber-400' : 'bg-slate-600'
  const text = open ? 'text-green-300' : preMarket || afterHours ? 'text-amber-300' : 'text-slate-500'

  return (
    <div className="flex items-center gap-2 text-xs bg-slate-800/70 border border-slate-700/70 rounded-full px-3 py-1">
      <span className={`inline-block w-1.5 h-1.5 rounded-full ${dot} ${open ? 'animate-pulse' : ''}`} />
      <span className={`font-semibold ${text}`}>{label}</span>
      <span className="w-px h-3 bg-slate-700" />
      <span className="flex items-center gap-1 text-slate-400 tabular-nums">
        <Clock size={11} />
        {get('hour')}:{get('minute')}:{get('second')} ET
      </span>
    </div>
  )
}
