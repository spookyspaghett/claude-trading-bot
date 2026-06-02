import { useState } from 'react'
import { TrendingUp, LayoutDashboard, FlaskConical, Wallet } from 'lucide-react'
import { useWebSocket } from './hooks/useWebSocket'
import { usePolling } from './hooks/useApi'
import StatusBar from './components/StatusBar'
import BotControls from './components/BotControls'
import KillSwitch from './components/KillSwitch'
import PositionsTable from './components/PositionsTable'
import PnLChart from './components/PnLChart'
import EquityChart from './components/EquityChart'
import SignalFeed from './components/SignalFeed'
import ConfigEditor from './components/ConfigEditor'
import BacktestPanel from './components/BacktestPanel'
import ProfilesPanel from './components/ProfilesPanel'
import type { Account, BotStatus, EquityPoint, PnLPoint, Position } from './types'

type Tab = 'dashboard' | 'backtest' | 'profiles'

const DEFAULT_STATUS: BotStatus = { running: false, pid: null }
const DEFAULT_ACCOUNT: Account = { equity: '0', portfolio_value: '0', buying_power: '0', cash: '0', daily_pnl: '0' }

export default function App() {
  const [tab, setTab] = useState<Tab>('dashboard')

  const { events, connected: wsConnected } = useWebSocket('/api/ws/logs')

  const { data: botStatus, refresh: refreshBot } = usePolling<BotStatus>(
    '/api/bot/status', 5_000, DEFAULT_STATUS,
  )
  const { data: positions } = usePolling<Position[]>(
    '/api/positions', 10_000, [],
  )
  const { data: account } = usePolling<Account>(
    '/api/account', 30_000, DEFAULT_ACCOUNT,
  )
  const { data: equityHistory } = usePolling<EquityPoint[]>(
    '/api/equity-history', 300_000, [],
  )
  const { data: pnlData } = usePolling<PnLPoint[]>(
    '/api/pnl-intraday', 60_000, [],
  )

  return (
    <div className="min-h-screen flex flex-col">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <header className="bg-slate-900 border-b border-slate-700 px-4 py-2">
        {/* ── Top row: brand + tabs + controls ───────────────────────────── */}
        <div className="max-w-screen-2xl mx-auto flex items-center gap-3 flex-wrap">
          {/* Brand */}
          <div className="flex items-center gap-2">
            <TrendingUp size={18} className="text-blue-400 shrink-0" />
            <span className="font-bold text-slate-100 text-sm tracking-tight whitespace-nowrap">Claude Trading</span>
          </div>

          {/* Tab switcher */}
          <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5">
            <button
              onClick={() => setTab('dashboard')}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                tab === 'dashboard' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <LayoutDashboard size={12} />
              Dashboard
            </button>
            <button
              onClick={() => setTab('backtest')}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                tab === 'backtest' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <FlaskConical size={12} />
              Backtest
            </button>
            <button
              onClick={() => setTab('profiles')}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                tab === 'profiles' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Wallet size={12} />
              Profiles
            </button>
          </div>

          {/* Status badges */}
          <StatusBar botStatus={botStatus} wsConnected={wsConnected} account={account} />

          {/* Spacer */}
          <div className="flex-1" />

          {/* Controls — grouped tightly */}
          <div className="flex items-center gap-2 shrink-0">
            <BotControls botStatus={botStatus} onStatusChange={refreshBot} />
            <KillSwitch onTriggered={refreshBot} />
          </div>
        </div>
      </header>

      {/* ── Main content ───────────────────────────────────────────────────── */}
      <main className="flex-1 p-4 max-w-screen-2xl mx-auto w-full">
        {tab === 'dashboard' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Row 1 */}
            <PositionsTable positions={positions} />
            <PnLChart data={pnlData} />

            {/* Row 2 */}
            <SignalFeed events={events} wsConnected={wsConnected} />
            <EquityChart data={equityHistory} />

            {/* Row 3 — full width */}
            <div className="lg:col-span-2">
              <ConfigEditor onRestart={refreshBot} />
            </div>
          </div>
        )}
        {tab === 'backtest' && <BacktestPanel />}
        {tab === 'profiles' && (
          <ProfilesPanel botRunning={botStatus.running} onActivated={refreshBot} />
        )}
      </main>
    </div>
  )
}
