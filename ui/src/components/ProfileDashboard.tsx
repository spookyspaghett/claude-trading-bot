import { Coins, LineChart } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import { usePolling } from '../hooks/useApi'
import StatusBar from './StatusBar'
import BotControls from './BotControls'
import KillSwitch from './KillSwitch'
import PositionsTable from './PositionsTable'
import PnLChart from './PnLChart'
import EquityChart from './EquityChart'
import SignalFeed from './SignalFeed'
import ConfigEditor from './ConfigEditor'
import type { Account, AssetClass, BotStatus, EquityPoint, PnLPoint, Position } from '../types'

interface Props {
  slug: string
  name: string
  assetClass: AssetClass
  onStatusChange: () => void
}

const DEFAULT_STATUS: BotStatus = { running: false, pid: null }
const DEFAULT_ACCOUNT: Account = { equity: '0', portfolio_value: '0', buying_power: '0', cash: '0', daily_pnl: '0' }

export default function ProfileDashboard({ slug, name, assetClass, onStatusChange }: Props) {
  const q = `?profile=${encodeURIComponent(slug)}`

  const { events, connected: wsConnected } = useWebSocket(`/api/ws/logs${q}`)

  const { data: botStatus, refresh: refreshBot } = usePolling<BotStatus>(
    `/api/bot/status${q}`, 5_000, DEFAULT_STATUS,
  )
  const { data: positions } = usePolling<Position[]>(`/api/positions${q}`, 10_000, [])
  const { data: account } = usePolling<Account>(`/api/account${q}`, 30_000, DEFAULT_ACCOUNT)
  const { data: equityHistory } = usePolling<EquityPoint[]>(`/api/equity-history${q}`, 300_000, [])
  const { data: pnlData } = usePolling<PnLPoint[]>(`/api/pnl-intraday${q}`, 60_000, [])

  function handleStatusChange() {
    refreshBot()
    onStatusChange()
  }

  const crypto = assetClass === 'crypto'

  return (
    <div className="space-y-4">
      {/* Per-profile header: identity + status + controls */}
      <div className="bg-slate-900 rounded-xl border border-slate-700 px-4 py-3 flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1 text-[10px] font-bold px-1.5 py-0.5 rounded border ${
            crypto ? 'text-amber-300 bg-amber-950 border-amber-800' : 'text-sky-300 bg-sky-950 border-sky-800'
          }`}>
            {crypto ? <Coins size={10} /> : <LineChart size={10} />}
            {crypto ? 'CRYPTO' : 'STOCK'}
          </span>
          <span className="font-bold text-slate-100 text-sm">{name}</span>
        </div>
        <StatusBar botStatus={botStatus} wsConnected={wsConnected} account={account} />
        <div className="flex-1" />
        <div className="flex items-center gap-2 shrink-0">
          <BotControls botStatus={botStatus} onStatusChange={handleStatusChange} slug={slug} />
          <KillSwitch onTriggered={handleStatusChange} slug={slug} />
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <PositionsTable positions={positions} />
        <PnLChart data={pnlData} />
        <SignalFeed events={events} wsConnected={wsConnected} />
        <EquityChart data={equityHistory} />
        <div className="lg:col-span-2">
          <ConfigEditor onRestart={handleStatusChange} slug={slug} />
        </div>
      </div>
    </div>
  )
}
