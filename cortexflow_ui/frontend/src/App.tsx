import { useEffect, useState } from 'react'
import { Allotment } from 'allotment'
import 'allotment/dist/style.css'
import './App.css'
import { Panel } from './components/Panel'
import { ExperimentTree } from './components/ExperimentTree'

type Dashboard = {
  id: string
  url: string
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

  return (
    <>
      <header className="navbar">
        <div className="navbar__title">cortexflow</div>
        <nav className="navbar__links" aria-label="Dashboards">
          {state.status === 'loading' && (
            <span className="navbar__status">Loading…</span>
          )}
          {state.status === 'error' && (
            <span className="navbar__status navbar__status--error">
              Failed to load dashboards: {state.message}
            </span>
          )}
          {state.status === 'ready' &&
            state.dashboards.map((d) => (
              <a
                key={d.id}
                className="navbar__link"
                href={d.url}
                target="_blank"
                rel="noopener noreferrer"
              >
                {d.id}
              </a>
            ))}
        </nav>
      </header>
      <div className="layout">
        <Allotment>
          <Allotment.Pane preferredSize={280} minSize={180} maxSize={500}>
            <Panel>
              <ExperimentTree />
            </Panel>
          </Allotment.Pane>
          <Allotment.Pane>
            <main className="main" />
          </Allotment.Pane>
        </Allotment>
      </div>
    </>
  )
}

export default App
