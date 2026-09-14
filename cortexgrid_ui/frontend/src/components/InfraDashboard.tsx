import { useEffect, useState } from 'react'
import { TitledFrame } from './TitledFrame'
import './InfraDashboard.css'

type PodStatus = {
  name: string
  namespace: string
  kind: string
  node: string | null
  state: string
  health: string
  healthy: boolean
  logs: string | null
}

type InfraStatus = {
  overall: boolean
  pods: PodStatus[]
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

function PodCard({ p }: { p: PodStatus }) {
  const heartClass = p.healthy ? 'infra-card__heart--ok' : 'infra-card__heart--bad'
  return (
    <TitledFrame title={p.name}>
      <div className="infra-card__meta">
        <span className={`infra-card__heart ${heartClass}`}>♥</span>
        <span>state: {p.state}</span>
        <span>health: {p.health}</span>
        <span>node: {p.node ?? '—'}</span>
      </div>
      {p.logs && (
        <details className="infra-card__logs">
          <summary>Recent logs</summary>
          <pre>{p.logs}</pre>
        </details>
      )}
      <div className="infra-card__footer">
        <span className="infra-card__kind">{p.kind}</span>
      </div>
    </TitledFrame>
  )
}

function groupByNamespace(pods: PodStatus[]): Map<string, PodStatus[]> {
  const groups = new Map<string, PodStatus[]>()
  for (const p of pods) {
    const arr = groups.get(p.namespace) ?? []
    arr.push(p)
    groups.set(p.namespace, arr)
  }
  return groups
}

export function InfraDashboard() {
  const state = useInfraStatus()
  if (state.status === 'loading') {
    return <div className="infra-dashboard__empty">Loading…</div>
  }
  if (state.status === 'error') {
    return <div className="infra-dashboard__empty">Error: {state.message}</div>
  }
  const groups = groupByNamespace(state.data.pods)
  return (
    <div className="infra-dashboard">
      <header className="infra-dashboard__header">
        <h1>Infrastructure Status</h1>
      </header>
      {[...groups.entries()].map(([ns, pods]) => (
        <section key={ns} className="infra-dashboard__namespace">
          <h2>{ns}</h2>
          <div className="infra-dashboard__grid">
            {pods.map((p) => <PodCard key={`${ns}/${p.name}`} p={p} />)}
          </div>
        </section>
      ))}
    </div>
  )
}
