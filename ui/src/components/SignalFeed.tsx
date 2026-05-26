import type { LogEvent } from '../types'

interface Props {
  events: LogEvent[]
  wsConnected: boolean
}

const EVENT_STYLES: Record<string, { label: string; color: string }> = {
  signal:          { label: 'SIGNAL',  color: 'text-blue-400 bg-blue-950 border-blue-800' },
  order_submitted: { label: 'ORDER',   color: 'text-yellow-400 bg-yellow-950 border-yellow-800' },
  fill:            { label: 'FILL',    color: 'text-green-400 bg-green-950 border-green-800' },
  order_rejected:  { label: 'REJECT',  color: 'text-red-400 bg-red-950 border-red-800' },
  startup:         { label: 'START',   color: 'text-slate-400 bg-slate-800 border-slate-600' },
  shutting_down:   { label: 'STOP',    color: 'text-slate-400 bg-slate-800 border-slate-600' },
}

function eventStyle(event: string) {
  return EVENT_STYLES[event] ?? { label: event.toUpperCase().slice(0, 6), color: 'text-slate-400 bg-slate-800 border-slate-600' }
}

function formatTime(ts: string) {
  try {
    return new Date(ts).toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZone: 'America/New_York',
    })
  } catch {
    return ts.slice(11, 19)
  }
}

function EventRow({ evt }: { evt: LogEvent }) {
  const style = eventStyle(evt.event)
  const details: string[] = []

  if (evt.symbol) details.push(evt.symbol)
  if (evt.direction) details.push(evt.direction)
  if (evt.price) details.push(`@ ${evt.price}`)
  if (evt.qty) details.push(`qty ${evt.qty}`)
  if (evt.filled_qty) details.push(`filled ${evt.filled_qty} @ ${evt.filled_avg_price ?? '?'}`)
  if (evt.reason && evt.event !== 'signal') details.push(evt.reason)
  if (evt.error) details.push(evt.error)
  if (evt.event === 'signal' && evt.reason) details.push(`(${evt.reason})`)

  return (
    <div className="flex items-start gap-2.5 px-3 py-2 hover:bg-slate-800/40 transition-colors text-xs">
      <span className="text-slate-600 shrink-0 mt-0.5 tabular-nums">{formatTime(evt.timestamp)}</span>
      <span className={`shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded border ${style.color}`}>
        {style.label}
      </span>
      <span className="text-slate-300 min-w-0 break-words">{details.join(' · ') || evt.event}</span>
    </div>
  )
}

export default function SignalFeed({ events, wsConnected }: Props) {
  return (
    <div className="bg-slate-900 rounded-xl border border-slate-700 flex flex-col overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-700 flex items-center justify-between shrink-0">
        <h2 className="text-sm font-semibold text-slate-200">Live Feed</h2>
        <span className={`text-xs ${wsConnected ? 'text-blue-400' : 'text-slate-600'}`}>
          {wsConnected ? '● live' : '○ reconnecting…'}
        </span>
      </div>
      <div className="overflow-y-auto flex-1 divide-y divide-slate-800/60" style={{ maxHeight: 340 }}>
        {events.length === 0 ? (
          <div className="flex items-center justify-center text-slate-600 text-sm py-10">
            Waiting for bot activity…
          </div>
        ) : (
          events.map((evt, i) => <EventRow key={i} evt={evt} />)
        )}
      </div>
    </div>
  )
}
