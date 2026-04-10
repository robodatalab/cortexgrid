import { useEffect, useState } from 'react'
import './App.css'

type Dashboard = {
  id: string
  name: string
  description: string
  port: number
}

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; dashboards: Dashboard[] }
  | { status: 'error'; message: string }

function App() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/dashboards', { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<Dashboard[]>
      })
      .then((dashboards) => setState({ status: 'ready', dashboards }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setState({
          status: 'error',
          message: err instanceof Error ? err.message : String(err),
        })
      })
    return () => controller.abort()
  }, [])

  const dashboardUrl = (port: number) =>
    `${window.location.protocol}//${window.location.hostname}:${port}`

  return (
    <main className="landing">
      <header className="landing-header">
        <h1>CortexFlow</h1>
        <p>RoboLab ML Infrastructure</p>
      </header>

      <nav className="dashboard-nav" aria-label="Dashboards">
        {state.status === 'loading' && (
          <p className="nav-status">Loading dashboards…</p>
        )}

        {state.status === 'error' && (
          <p className="nav-status nav-status--error">
            Failed to load dashboards: {state.message}
          </p>
        )}

        {state.status === 'ready' &&
          state.dashboards.map((d) => (
            <a
              key={d.id}
              className="dashboard-card"
              href={dashboardUrl(d.port)}
              target="_blank"
              rel="noopener noreferrer"
            >
              <div className="dashboard-card__title">
                <h2>{d.name}</h2>
                <span className="dashboard-card__arrow" aria-hidden="true">
                  ↗
                </span>
              </div>
              <p className="dashboard-card__desc">{d.description}</p>
              <span className="dashboard-card__port">:{d.port}</span>
            </a>
          ))}
      </nav>
    </main>
  )
}

export default App
