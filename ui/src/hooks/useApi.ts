import { useCallback, useEffect, useRef, useState } from 'react'

export function usePolling<T>(
  url: string,
  intervalMs: number,
  defaultValue: T,
): { data: T; error: string | null; refresh: () => void } {
  const [data, setData] = useState<T>(defaultValue)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(url)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = (await res.json()) as T
      if (mountedRef.current) {
        setData(json)
        setError(null)
      }
    } catch (err) {
      if (mountedRef.current) {
        if (err instanceof SyntaxError) {
          console.error('[usePolling] JSON parse error on:', url, err)
        }
        setError(String(err))
      }
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

  return { data, error, refresh: fetchData }
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
