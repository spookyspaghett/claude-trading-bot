import type { Account, BotStatus } from '../types'

interface Props {
  botStatus: BotStatus
  wsConnected: boolean
  account: Account | null
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

export default function StatusBar({ botStatus, wsConnected, account }: Props) {
  const dailyPnl = account?.daily_pnl ?? '0'
  const equity = account ? parseFloat(account.equity).toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '—'

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <Badge label={botStatus.running ? 'Bot Running' : 'Bot Stopped'} active={botStatus.running} color="bg-green-500" />
      <Badge label={wsConnected ? 'Feed Live' : 'Feed Offline'} active={wsConnected} color="bg-blue-500" />
      <span className="text-xs text-slate-500">|</span>
      <span className="text-xs text-slate-400">Equity: <span className="text-slate-200 font-medium">{equity}</span></span>
      <span className="text-xs text-slate-400">
        Daily P&amp;L:{' '}
        <span className={`font-semibold ${pnlColor(dailyPnl)}`}>
          {parseFloat(dailyPnl) >= 0 ? '+' : ''}
          {parseFloat(dailyPnl).toLocaleString('en-US', { style: 'currency', currency: 'USD' })}
        </span>
      </span>
      {botStatus.pid && (
        <span className="text-xs text-slate-600">pid {botStatus.pid}</span>
      )}
    </div>
  )
}
