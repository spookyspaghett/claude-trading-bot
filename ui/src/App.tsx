import { useEffect, useState } from 'react'
import { TrendingUp, FlaskConical, Wallet, Coins, LineChart, Zap } from 'lucide-react'
import { usePolling, apiPost } from './hooks/useApi'
import BacktestPanel from './components/BacktestPanel'
import ProfilesPanel from './components/ProfilesPanel'
import ProfileDashboard from './components/ProfileDashboard'
import ErrorBoundary from './components/ErrorBoundary'
import MarketStatus from './components/MarketStatus'
import type { BotStatusMap, ProfileSummary } from './types'

// Selected view: a profile slug, or one of the global tabs.
type View = { kind: 'profile'; slug: string } | { kind: 'backtest' } | { kind: 'manage' }

// The view lives in the URL hash so a refresh, a bookmark, or the browser's
// back button all land where you left off. Tabs render as real links, which
// also buys Cmd/Ctrl-click and middle-click for free.
function viewToHash(v: View): string {
  if (v.kind === 'backtest') return '#/backtest'
  if (v.kind === 'manage') return '#/profiles'
  return `#/profile/${encodeURIComponent(v.slug)}`
}

function parseHash(hash: string): View | null {
  const h = hash.replace(/^#\/?/, '')
  if (h === 'backtest') return { kind: 'backtest' }
  if (h === 'profiles') return { kind: 'manage' }
  const m = /^profile\/(.+)$/.exec(h)
  return m ? { kind: 'profile', slug: decodeURIComponent(m[1]) } : null
}

export default function App() {
  const [view, setView] = useState<View>(
    () => parseHash(window.location.hash) ?? { kind: 'manage' },
  )

  // Follow browser navigation (back/forward, pasted links).
  useEffect(() => {
    const onHashChange = () =>
      setView(parseHash(window.location.hash) ?? { kind: 'manage' })
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const { data: profiles, refresh: refreshProfiles } = usePolling<ProfileSummary[]>(
    '/api/profiles', 30_000, [],
  )
  const { data: status, refresh: refreshStatus } = usePolling<BotStatusMap>(
    '/api/bot/status', 5_000, { bots: {} },
  )

  const [killingAll, setKillingAll] = useState(false)
  const [confirmingKill, setConfirmingKill] = useState(false)

  // Once profiles load, default to the active profile (or the first one) —
  // but only on a bare URL, so landing directly on #/profiles isn't bounced
  // away the moment the profile list arrives.
  useEffect(() => {
    if (!window.location.hash && view.kind === 'manage' && profiles.length > 0) {
      const active = profiles.find(p => p.active) ?? profiles[0]
      const next: View = { kind: 'profile', slug: active.slug }
      window.history.replaceState(null, '', viewToHash(next))
      setView(next)
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
      setConfirmingKill(false)
    }
  }

  const selectedSlug = view.kind === 'profile' ? view.slug : null
  const selectedProfile = profiles.find(p => p.slug === selectedSlug) ?? null

  return (
    <div className="min-h-screen flex flex-col">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50
                   focus:rounded-lg focus:bg-blue-600 focus:px-3 focus:py-2
                   focus:text-sm focus:font-semibold focus:text-white"
      >
        Skip to Main Content
      </a>
      <header className="sticky top-0 z-40 bg-slate-950/80 backdrop-blur-md border-b border-slate-800 px-4 py-2 shadow-lg shadow-black/20">
        <div className="max-w-screen-2xl mx-auto flex items-center gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="grid place-items-center w-7 h-7 rounded-lg bg-gradient-to-br from-blue-500 to-violet-600 shadow-md shadow-blue-900/50 shrink-0">
              <TrendingUp size={15} className="text-white" />
            </span>
            <h1 translate="no" className="font-extrabold text-sm tracking-tight whitespace-nowrap bg-gradient-to-r from-slate-50 to-slate-400 bg-clip-text text-transparent">
              Claude&nbsp;Trading
            </h1>
          </div>
          <MarketStatus />

          {/* Profile tabs + global tabs */}
          <nav aria-label="Views"
            className="flex items-center gap-0.5 bg-slate-800 rounded-lg p-0.5 flex-wrap">
            {profiles.map(p => {
              const bot = status.bots[p.slug]
              const running = bot?.running ?? false
              const died = bot?.stopped_unexpectedly ?? false
              const isSel = selectedSlug === p.slug
              const state = running ? 'running' : died ? 'died unexpectedly' : 'stopped'
              return (
                <a key={p.slug}
                  href={viewToHash({ kind: 'profile', slug: p.slug })}
                  aria-current={isSel ? 'page' : undefined}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                    isSel ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
                  }`}
                  title={running ? 'Bot running' : died ? 'Bot died unexpectedly — check crash log' : 'Bot stopped'}
                >
                  <span aria-hidden="true" className={`inline-block w-1.5 h-1.5 rounded-full ${
                    running ? 'bg-green-400' : died ? 'bg-amber-400 animate-pulse' : 'bg-slate-600'
                  }`} />
                  {p.asset_class === 'crypto'
                    ? <Coins size={12} aria-hidden="true" />
                    : <LineChart size={12} aria-hidden="true" />}
                  <span className="max-w-[12rem] truncate">{p.name}</span>
                  <span className="sr-only"> — bot {state}</span>
                </a>
              )
            })}

            <span aria-hidden="true" className="w-px h-4 bg-slate-700 mx-0.5" />

            <a
              href="#/backtest"
              aria-current={view.kind === 'backtest' ? 'page' : undefined}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                view.kind === 'backtest' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <FlaskConical size={12} aria-hidden="true" /> Backtest
            </a>
            <a
              href="#/profiles"
              aria-current={view.kind === 'manage' ? 'page' : undefined}
              className={`flex items-center gap-1 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors ${
                view.kind === 'manage' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Wallet size={12} aria-hidden="true" /> Profiles
            </a>
          </nav>

          <div className="flex-1" />

          {/* Master kill — only meaningful while something runs. Two-step:
              flattening every book at market is not an undoable action. */}
          {confirmingKill ? (
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-red-300">
                Flatten &amp; stop {runningCount} {runningCount === 1 ? 'bot' : 'bots'}?
              </span>
              <button
                onClick={() => void killAll()}
                disabled={killingAll}
                className="px-3 py-1.5 rounded-lg text-xs font-bold bg-red-600 hover:bg-red-500 disabled:opacity-50 transition-colors"
              >
                {killingAll ? 'Killing…' : 'Confirm Kill All'}
              </button>
              <button
                onClick={() => setConfirmingKill(false)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-700 hover:bg-slate-600 transition-colors"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmingKill(true)}
              disabled={runningCount === 0}
              title={runningCount === 0 ? 'No bots running' : `Flatten & stop all ${runningCount} running bots`}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-bold bg-red-700 hover:bg-red-600 disabled:opacity-40 border border-red-500 transition-colors"
            >
              <Zap size={13} aria-hidden="true" />
              Kill All{runningCount > 0 ? ` (${runningCount})` : ''}
            </button>
          )}
        </div>
      </header>

      <main id="main" className="flex-1 p-4 max-w-screen-2xl mx-auto w-full">
        {view.kind === 'profile' && selectedProfile && (
          <ErrorBoundary label={`${selectedProfile.name} dashboard`}>
            <ProfileDashboard
              key={selectedProfile.slug}
              slug={selectedProfile.slug}
              name={selectedProfile.name}
              assetClass={selectedProfile.asset_class}
              symbols={selectedProfile.symbols}
              strategy={selectedProfile.strategy}
              onStatusChange={refreshStatus}
            />
          </ErrorBoundary>
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
