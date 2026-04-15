import { useEffect, useState } from 'react'
import { TitledFrame } from './TitledFrame'
import './InfraDashboard.css'

type ContainerStatus = {
  name: string
  state: string
  health: string
  healthy: boolean
  logs: string | null
}

type InfraStatus = {
  overall: boolean
  containers: ContainerStatus[]
}

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; data: InfraStatus }
  | { status: 'error'; message: string }

function useInfraStatus(): LoadState {
  const [state, setState] = useState<LoadState>({ status: 'loading' })
  useEffect(() => {
    const controller = new AbortController()
    const poll = () => {
      fetch('/api/infra/status', { signal: controller.signal })
        .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
        .then((data: InfraStatus) => setState({ status: 'ready', data }))
        .catch((err: unknown) => {
          if (err instanceof DOMException && err.name === 'AbortError') return
          setState({
            status: 'error',
            message: err instanceof Error ? err.message : String(err),
          })
        })
    }
    poll()
    const id = window.setInterval(poll, 10_000)
    return () => {
      controller.abort()
      window.clearInterval(id)
    }
  }, [])
  return state
}

function ContainerCard({ c }: { c: ContainerStatus }) {
  return (
    <TitledFrame
      title={c.name}
      titleClassName={`infra-card__title--${c.healthy ? 'ok' : 'bad'}`}
    >
      <div className="infra-card__meta">
        <span>state: {c.state}</span>
        <span>health: {c.health}</span>
      </div>
      {c.logs && (
        <details className="infra-card__logs">
          <summary>Recent logs</summary>
          <pre>{c.logs}</pre>
        </details>
      )}
    </TitledFrame>
  )
}

export function InfraDashboard() {
  const state = useInfraStatus()
  if (state.status === 'loading') {
    return <div className="infra-dashboard__empty">Loading…</div>
  }
  if (state.status === 'error') {
    return <div className="infra-dashboard__empty">Error: {state.message}</div>
  }
  return (
    <div className="infra-dashboard">
      <header className="infra-dashboard__header">
        <h1>Infrastructure Status</h1>
      </header>
      <div className="infra-dashboard__grid">
        {state.data.containers.map((c) => (
          <ContainerCard key={c.name} c={c} />
        ))}
      </div>
    </div>
  )
}
