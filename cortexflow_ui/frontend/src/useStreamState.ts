import { useEffect, useState } from 'react'

/**
 * Subscribe to a backend KeyedStream WebSocket. Returns the latest
 * `state` payload pushed by the server, or `null` until one arrives.
 * Pass `null` as `path` to disable the subscription (e.g. when there
 * is no active selection). Auto-reconnects every 3s on close.
 */
export function useStreamState<T>(path: string | null): T | null {
  const [state, setState] = useState<T | null>(null)

  useEffect(() => {
    setState(null)
    if (path === null) return
    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}${path}`
      socket = new WebSocket(url)
      socket.onmessage = (e) => {
        const msg = JSON.parse(e.data) as { type: string; data?: T }
        if (msg.type === 'state' && msg.data !== undefined) {
          setState(msg.data)
        }
      }
      socket.onclose = () => {
        if (!cancelled) setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      cancelled = true
      socket?.close()
    }
  }, [path])

  return state
}
