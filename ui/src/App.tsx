import { useEffect, useState } from 'react'
import { TrendingUp, FlaskConical, Wallet, Coins, LineChart, Zap } from 'lucide-react'
import { usePolling, apiPost } from './hooks/useApi'
import BacktestPanel from './components/BacktestPanel'
import ProfilesPanel from './components/ProfilesPanel'
import ProfileDashboard from './components/ProfileDashboard'
import type { BotStatusMap, ProfileSummary } from './types'

// Selected view: a profile slug, or one of the global tabs.
type View = { kind: 'profile'; slug: string } | { kind: 'backtest' } | { kind: 'manage' }

export default function App() {
  const [view, setView] = useState<View>({ kind: 'manage' })

  const { data: profiles, refresh: refreshProfiles } = usePolling<ProfileSummary[]>(
    '/api/profiles', 30_000, [],
  )
  const { data: status, refresh: refreshStatus } = usePolling<BotStatusMap>(
    '/api/bot/status', 5_000, { bots: {} },
  )

  const [killingAll, setKillingAll] = useState(false)

  // Once profiles load, default to the active profile (or the first one).
  useEffect(() => {
    if (view.kind === 'manage' && profiles.length > 0) {
      const active = profiles.find(p => p.active) ?? profiles[0]
      setView({ kind: 'profile', slug: active.slug })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profiles])

  const runningCount = Object.values(status.bots).filter(b => b.running).length

  async function killAll() {
    setKillingAll(true)
    try {
      await apiPost('/api/kill-all')
      refreshStatus()
    } catch {
      // ignore — kill files may still have been created
    } finally {
      setKillingAll(false)
    }
  }

  const selectedSlug = view.kind === 'profile' ? view.slug : null
  const selectedProfile = profiles.find(p => p.slug === selectedSlug) ?? null

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-slate-900 border-b border-slate-700 px-4 py-2">
        <div className="max-w-screen-2xl mx-auto flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <TrendingUp size={18} className="text-blue-400 shrink-0" />
            <span className="font-bold text-slate-100 text-sm tracking-tight whitespace-nowrap">Claude Trading</span>
          </div>

          {/* Profile tabs + global tabs */}
          <div className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5 flex-wrap">
            {profiles.map(p => {
              const running = status.bots[p.slug]?.running ?? false
              const isSel = selectedSlug === p.slug
              return (
                <button key={p.slug}
                  onClick={() => setView({ kind: 'profile', slug: p.slug })}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                    isSel ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
                  }`}
                  title={running ? 'Bot running' : 'Bot stopped'}
                >
                  <span className={`inline-block w-1.5 h-1.5 rounded-full ${running ? 'bg-green-400' : 'bg-slate-600'}`} />
                  {p.asset_class === 'crypto' ? <Coins size={12} /> : <LineChart size={12} />}
                  {p.name}
                </button>
              )
            })}

            <span className="w-px h-4 bg-slate-700 mx-0.5" />

            <button
              onClick={() => setView({ kind: 'backtest' })}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                view.kind === 'backtest' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <FlaskConical size={12} /> Backtest
            </button>
            <button
              onClick={() => setView({ kind: 'manage' })}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                view.kind === 'manage' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Wallet size={12} /> Profiles
            </button>
          </div>

          <div className="flex-1" />

          {/* Master kill — only meaningful while something runs */}
          <button
            onClick={() => void killAll()}
            disabled={killingAll || runningCount === 0}
            title={runningCount === 0 ? 'No bots running' : `Flatten & stop all ${runningCount} running bots`}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-red-700 hover:bg-red-600 disabled:opacity-40 border border-red-500 transition-colors"
          >
            <Zap size={13} />
            Kill all{runningCount > 0 ? ` (${runningCount})` : ''}
          </button>
        </div>
      </header>

      <main className="flex-1 p-4 max-w-screen-2xl mx-auto w-full">
        {view.kind === 'profile' && selectedProfile && (
          <ProfileDashboard
            key={selectedProfile.slug}
            slug={selectedProfile.slug}
            name={selectedProfile.name}
            assetClass={selectedProfile.asset_class}
            symbols={selectedProfile.symbols}
            onStatusChange={refreshStatus}
          />
        )}
        {view.kind === 'profile' && !selectedProfile && (
          <p className="text-slate-500 text-sm">Profile not found — pick another tab.</p>
        )}
        {view.kind === 'backtest' && <BacktestPanel />}
        {view.kind === 'manage' && (
          <ProfilesPanel
            runningSlugs={Object.entries(status.bots).filter(([, b]) => b.running).map(([s]) => s)}
            onActivated={() => { refreshProfiles(); refreshStatus() }}
          />
        )}
      </main>
    </div>
  )
}
