import { Component, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'

interface Props {
  /** Short label shown in the fallback, e.g. the panel or view name. */
  label?: string
  children: ReactNode
}

interface State {
  error: Error | null
}

/** Catches render errors so one broken panel can't white-screen the whole
 *  dashboard. Shows the error and a retry button instead. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="bg-slate-900 rounded-xl border border-red-900/60 p-4 flex flex-col gap-2">
          <div className="flex items-center gap-2 text-red-400 text-sm font-semibold">
            <AlertTriangle size={15} />
            {this.props.label ?? 'This panel'} crashed
          </div>
          <pre className="text-xs text-red-300/80 whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
            {String(this.state.error)}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            className="self-start px-3 py-1 rounded-lg text-xs font-medium bg-slate-700 hover:bg-slate-600 transition-colors"
          >
            Retry
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
