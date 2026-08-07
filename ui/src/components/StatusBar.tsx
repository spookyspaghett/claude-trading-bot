import type { Account, BotStatus } from '../types'

interface Props {
  botStatus: BotStatus
  wsConnected: boolean
  account: Account | null
  /** True once the account endpoint has returned at least once. */
  accountLoaded?: boolean
  /** Set while the latest account request is failing. */
  accountError?: string | null
}

function Dot({ active, color }: { active: boolean; color: string }) {
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full mr-1.5 ${active ? color : 'bg-slate-600'}`}
    />
  )
}

function Badge({ label, active, color }: { label: string; active: boolean; color: string }) {
  return (
    <span className="flex items-center text-xs font-medium text-slate-300 bg-slate-800 px-2.5 py-1 rounded-full">
      <Dot active={active} color={color} />
      {label}
    </span>
  )
}

function pnlColor(val: string) {
  const n = parseFloat(val)
  if (n > 0) return 'text-green-400'
  if (n < 0) return 'text-red-400'
  return 'text-slate-400'
}

export default function StatusBar({
  botStatus, wsConnected, account, accountLoaded = true, accountError = null,
}: Props) {
  // Never render an unanswered request as a number. The account endpoint 502s
  // whenever the broker is unreachable, and usePolling holds its zero-filled
  // default — which showed a confident "$0.00" equity on a funded account.
  const known = account !== null && accountLoaded
  const usd = (val: string) =>
    parseFloat(val).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  const dailyPnl = known ? account.daily_pnl : null
  const equity = known ? usd(account.equity) : '—'
  const staleTitle = accountError
    ? `Account data unavailable: ${accountError}`
    : undefined

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {botStatus.stopped_unexpectedly && !botStatus.running ? (
        <span className="flex items-center text-xs font-medium text-amber-300 bg-amber-950 border border-amber-800 px-2.5 py-1 rounded-full">
          <span className="inline-block w-2 h-2 rounded-full mr-1.5 bg-amber-400 animate-pulse" />
          Bot Died — check crash log
        </span>
      ) : (
        <Badge label={botStatus.running ? 'Bot Running' : 'Bot Stopped'} active={botStatus.running} color="bg-green-500" />
      )}
      <Badge label={wsConnected ? 'Feed Live' : 'Feed Offline'} active={wsConnected} color="bg-blue-500" />
      {accountError && (
        <span
          title={staleTitle}
          className="flex items-center text-xs font-medium text-amber-300 bg-amber-950 border border-amber-800 px-2.5 py-1 rounded-full"
        >
          <span className="inline-block w-2 h-2 rounded-full mr-1.5 bg-amber-400 animate-pulse" />
          Broker unreachable
        </span>
      )}
      <span className="w-px h-6 bg-slate-700/70" />
      <div className="flex flex-col leading-tight" title={staleTitle}>
        <span className="text-[9px] uppercase tracking-widest text-slate-500">Equity</span>
        <span className="text-sm font-bold text-slate-100 tabular-nums">{equity}</span>
      </div>
      <div className="flex flex-col leading-tight" title={staleTitle}>
        <span className="text-[9px] uppercase tracking-widest text-slate-500">Daily P&amp;L</span>
        {dailyPnl === null ? (
          <span className="text-sm font-bold tabular-nums text-slate-400">—</span>
        ) : (
          <span className={`text-sm font-bold tabular-nums ${pnlColor(dailyPnl)}`}>
            {parseFloat(dailyPnl) >= 0 ? '+' : ''}
            {usd(dailyPnl)}
          </span>
        )}
      </div>
      {botStatus.pid && (
        <span className="text-xs text-slate-600">pid {botStatus.pid}</span>
      )}
    </div>
  )
}
