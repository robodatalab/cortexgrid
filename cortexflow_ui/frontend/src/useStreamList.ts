import { useEffect, useState } from 'react'

type DiffEvent<T> =
  | { type: 'added'; item: T }
  | { type: 'updated'; item: T }
  | { type: 'removed'; id: string }

/**
 * Subscribe to a backend KeyedStream WebSocket. Returns a map of
 * items keyed by id (extracted via `getId`), updated by
 * `added`/`updated`/`removed` events. Pass `null` as `path` to disable.
 * Auto-reconnects every 3s on close.
 */
export function useStreamList<T>(
  path: string | null,
  getId: (item: T) => string,
): Record<string, T> {
  const [items, setItems] = useState<Record<string, T>>({})

  useEffect(() => {
    setItems({})
    if (path === null) return
    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}${path}`
      socket = new WebSocket(url)
      socket.onmessage = (e) => {
        const event = JSON.parse(e.data) as DiffEvent<T>
        if (event.type === 'added' || event.type === 'updated') {
          const id = getId(event.item)
          setItems((prev) => ({ ...prev, [id]: event.item }))
        } else if (event.type === 'removed') {
          setItems((prev) => {
            const next = { ...prev }
            delete next[event.id]
            return next
          })
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

  return items
}
