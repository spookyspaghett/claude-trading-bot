import { useEffect, useRef, useState } from 'react'
import type { LogEvent } from '../types'

const MAX_EVENTS = 500

export function useWebSocket(path: string): { events: LogEvent[]; connected: boolean } {
  const [events, setEvents] = useState<LogEvent[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const host = window.location.host
    const url = `${proto}://${host}${path}`

    function connect() {
      const ws = new WebSocket(url)
      wsRef.current = ws

      ws.onopen = () => {
        setConnected(true)
        // The server replays today's full backlog on every connect — reset so
        // reconnects (and day rollovers) don't fill the feed with duplicates.
        setEvents([])
      }

      ws.onmessage = (e: MessageEvent<string>) => {
        try {
          const evt = JSON.parse(e.data) as LogEvent
          if (!evt || typeof evt !== 'object' || typeof evt.event !== 'string') return
          setEvents(prev => [evt, ...prev].slice(0, MAX_EVENTS))
        } catch {
          // ignore malformed lines
        }
      }

      ws.onclose = () => {
        setConnected(false)
        timerRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
    }
  }, [path])

  return { events, connected }
}
