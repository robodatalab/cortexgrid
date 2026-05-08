import { useEffect, useMemo, useState } from 'react'
import { Allotment } from 'allotment'
import 'allotment/dist/style.css'
import './App.css'
import { LayoutPane } from './components/LayoutPane'
import { ConfirmModal } from './components/ConfirmModal'
import { ExperimentTree } from './components/ExperimentTree'
import type { ExperimentRun, Selection } from './components/ExperimentTree'
import { ExperimentDashboard } from './components/ExperimentDashboard'
import { RunDashboard } from './components/RunDashboard'
import type { Job } from './components/RunDashboard'
import { JobDashboard } from './components/JobDashboard'
import { InfraStatusIndicator } from './components/InfraStatusIndicator'
import { InfraDashboard } from './components/InfraDashboard'
import { SecretsDashboard } from './components/SecretsDashboard'
import { useStreamList } from './useStreamList'

type Dashboard = {
  id: string
  url: string
}

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; dashboards: Dashboard[] }
  | { status: 'error'; message: string }

type StreamEvent =
  | { type: 'added'; item: ExperimentRun }
  | { type: 'updated'; item: ExperimentRun }
  | { type: 'removed'; id: string }

type PendingDelete =
  | { kind: 'experiment'; experiment_name: string }
  | { kind: 'run'; run_id: string; run_name: string }

function App() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })
  const [selection, setSelection] = useState<Selection | null>(null)
  const [view, setView] = useState<'experiments' | 'infra' | 'secrets'>('experiments')
  const [runsById, setRunsById] = useState<Record<string, ExperimentRun>>({})
  const [pendingDelete, setPendingDelete] = useState<PendingDelete | null>(null)

  useEffect(() => {
    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/api/experiments/stream`
      socket = new WebSocket(url)
      socket.onmessage = (e) => {
        const event = JSON.parse(e.data) as StreamEvent
        if (event.type === 'added' || event.type === 'updated') {
          setRunsById((prev) => ({ ...prev, [event.item.run_name]: event.item }))
        } else if (event.type === 'removed') {
          setRunsById((prev) => {
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
  }, [])

  async function handleConfirmDelete() {
    if (pendingDelete === null) return
    const target = pendingDelete
    setPendingDelete(null)
    const url =
      target.kind === 'experiment'
        ? `/api/experiments/${encodeURIComponent(target.experiment_name)}`
        : `/api/runs/${encodeURIComponent(target.run_id)}`
    const res = await fetch(url, { method: 'DELETE' })
    if (!res.ok) {
      alert(`Delete failed: HTTP ${res.status}\n${await res.text()}`)
      return
    }
    if (target.kind === 'experiment' && selection?.experiment_name === target.experiment_name) {
      setSelection(null)
    } else if (
      target.kind === 'run' &&
      (selection?.kind === 'run' || selection?.kind === 'job') &&
      selection.run_id === target.run_id
    ) {
      setSelection(null)
    }
  }

  function pendingDeleteMessage(target: PendingDelete): string {
    return target.kind === 'experiment'
      ? `Delete experiment "${target.experiment_name}" and all of its runs? This cannot be undone.`
      : `Delete run "${target.run_name}"? This cannot be undone.`
  }

  const experimentNames = useMemo(() => {
    const names = new Set<string>()
    for (const r of Object.values(runsById)) names.add(r.experiment_name)
    return Array.from(names).sort()
  }, [runsById])

  const runsByExperiment = useMemo(() => {
    const map: Record<string, ExperimentRun[]> = {}
    for (const r of Object.values(runsById)) {
      ;(map[r.experiment_name] ??= []).push(r)
    }
    for (const list of Object.values(map)) {
      list.sort((a, b) => a.run_name.localeCompare(b.run_name))
    }
    return map
  }, [runsById])

  const activeRunId =
    selection?.kind === 'run' || selection?.kind === 'job'
      ? selection.run_id
      : null
  const activeRunJobsMap = useStreamList<Job>(
    activeRunId ? `/api/runs/${activeRunId}/jobs/stream` : null,
    (j) => j.job_id,
  )
  const activeRunJobs = activeRunId ? Object.values(activeRunJobsMap) : null

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
                <ExperimentTree
                  experimentNames={experimentNames}
                  runsByExperiment={runsByExperiment}
                  selection={selection}
                  onSelect={setSelection}
                  onDeleteExperiment={(experiment_name) =>
                    setPendingDelete({ kind: 'experiment', experiment_name })
                  }
                  onDeleteRun={(run_id, run_name) =>
                    setPendingDelete({ kind: 'run', run_id, run_name })
                  }
                />
              </LayoutPane>
            </Allotment.Pane>
            <Allotment.Pane>
              <LayoutPane>
                {selection?.kind === 'experiment' ? (
                  <ExperimentDashboard experimentName={selection.experiment_name} />
                ) : selection?.kind === 'run' ? (
                  <RunDashboard
                    runId={selection.run_id}
                    runName={selection.run_name}
                    experimentName={selection.experiment_name}
                    jobs={activeRunJobs}
                  />
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
      {pendingDelete !== null && (
        <ConfirmModal
          message={pendingDeleteMessage(pendingDelete)}
          onConfirm={handleConfirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </>
  )
}

export default App
