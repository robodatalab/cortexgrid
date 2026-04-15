import { useEffect, useState } from 'react'
import { Allotment } from 'allotment'
import 'allotment/dist/style.css'
import './App.css'
import { LayoutPane } from './components/LayoutPane'
import { ExperimentTree } from './components/ExperimentTree'
import type { Selection } from './components/ExperimentTree'
import { ExperimentDashboard } from './components/ExperimentDashboard'
import { RunDashboard } from './components/RunDashboard'
import { JobDashboard } from './components/JobDashboard'
import { InfraStatusIndicator } from './components/InfraStatusIndicator'
import { InfraDashboard } from './components/InfraDashboard'
import { SecretsDashboard } from './components/SecretsDashboard'

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
  const [selection, setSelection] = useState<Selection | null>(null)
  const [view, setView] = useState<'experiments' | 'infra' | 'secrets'>('experiments')

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
        <button
          type="button"
          className="navbar__title"
          onClick={() => setView('experiments')}
        >
          cortexflow
        </button>
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
          <button
            type="button"
            className="navbar__link"
            onClick={() => setView(view === 'secrets' ? 'experiments' : 'secrets')}
          >
            secrets
          </button>
          <InfraStatusIndicator
            onClick={() => setView(view === 'infra' ? 'experiments' : 'infra')}
          />
        </nav>
      </header>
      <div className="layout">
        {view === 'infra' ? (
          <InfraDashboard />
        ) : view === 'secrets' ? (
          <SecretsDashboard />
        ) : (
          <Allotment>
            <Allotment.Pane preferredSize={280} minSize={180} maxSize={500}>
              <LayoutPane>
                <ExperimentTree onSelect={setSelection} />
              </LayoutPane>
            </Allotment.Pane>
            <Allotment.Pane>
              <LayoutPane>
                {selection?.kind === 'experiment' ? (
                  <ExperimentDashboard experimentName={selection.experiment_name} />
                ) : selection?.kind === 'run' ? (
                  <RunDashboard runId={selection.run_id} runName={selection.run_name} experimentName={selection.experiment_name} />
                ) : selection?.kind === 'job' ? (
                  <JobDashboard runId={selection.run_id} jobId={selection.job_id} />
                ) : (
                  <main className="main" />
                )}
              </LayoutPane>
            </Allotment.Pane>
          </Allotment>
        )}
      </div>
    </>
  )
}

export default App
