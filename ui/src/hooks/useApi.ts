import { useCallback, useEffect, useRef, useState } from 'react'

/** Give up on a request rather than letting it stack behind a hung API. */
const REQUEST_TIMEOUT_MS = 20_000

export function usePolling<T>(
  url: string,
  intervalMs: number,
  defaultValue: T,
): { data: T; error: string | null; loaded: boolean; refresh: () => void } {
  const [data, setData] = useState<T>(defaultValue)
  const [error, setError] = useState<string | null>(null)
  // Whether a request has EVER succeeded. Without it, callers cannot tell
  // `defaultValue` apart from real data — an account endpoint returning 502
  // rendered as a confident $0.00 equity.
  const [loaded, setLoaded] = useState(false)
  const mountedRef = useRef(true)
  // Guards against a slow endpoint: without these, a new request fired every
  // interval regardless of whether the previous one had returned. Requests
  // stacked up, saturated the browser's 6-connections-per-origin limit, and
  // stalled every other panel's polling — the dashboard kept rendering its last
  // good values, so it looked live while being completely frozen.
  const inFlightRef = useRef(false)
  const seqRef = useRef(0)

  const fetchData = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true

    const seq = ++seqRef.current
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)

    try {
      const res = await fetch(url, { signal: controller.signal })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = (await res.json()) as T
      // Drop a late response that a newer request has already superseded,
      // so out-of-order replies can't overwrite fresher data.
      if (mountedRef.current && seq === seqRef.current) {
        setData(json)
        setError(null)
        setLoaded(true)
      }
    } catch (err) {
      if (mountedRef.current && seq === seqRef.current) {
        if (err instanceof SyntaxError) {
          console.error('[usePolling] JSON parse error on:', url, err)
        }
        setError(err instanceof DOMException && err.name === 'AbortError'
          ? `timed out after ${REQUEST_TIMEOUT_MS / 1000}s`
          : String(err))
      }
    } finally {
      clearTimeout(timer)
      inFlightRef.current = false
    }
  }, [url])

  useEffect(() => {
    mountedRef.current = true
    void fetchData()
    const id = setInterval(() => void fetchData(), intervalMs)
    return () => {
      mountedRef.current = false
      clearInterval(id)
    }
  }, [fetchData, intervalMs])

  return { data, error, loaded, refresh: fetchData }
}

export async function apiPost(url: string, body?: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json()
}

export async function apiPut(url: string, body: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  return res.json()
}
