import { useEffect, useMemo, useRef, useState } from 'react'
import { FlaskConical, Play, Cog, RefreshCw, ArrowLeft } from 'lucide-react'
import './ExperimentTree.css'

type Job = {
  job_id: string
  status: string
}

type ExperimentRun = {
  experiment_name: string
  run_id: string
  run_name: string
  jobs: Job[]
}

type Run = {
  run_id: string
  run_name: string
  jobs: Job[]
}

type Experiment = {
  experiment_name: string
  runs: Run[]
}

type StreamEvent =
  | { type: 'added'; item: ExperimentRun }
  | { type: 'updated'; item: ExperimentRun }
  | { type: 'removed'; id: string }

function groupByExperiment(items: ExperimentRun[]): Experiment[] {
  const map = new Map<string, Run[]>()
  for (const r of items) {
    const list = map.get(r.experiment_name) ?? []
    list.push({ run_id: r.run_id, run_name: r.run_name, jobs: r.jobs })
    map.set(r.experiment_name, list)
  }
  return Array.from(map, ([experiment_name, runs]) => ({ experiment_name, runs }))
}

export type Selection =
  | { kind: 'experiment'; experiment_name: string }
  | { kind: 'run'; experiment_name: string; run_id: string; run_name: string }
  | { kind: 'job'; experiment_name: string; run_id: string; job_id: string }

type ExperimentTreeProps = {
  onSelect: (selection: Selection) => void
}

type View =
  | { level: 'experiments' }
  | { level: 'runs'; experimentName: string }
  | { level: 'jobs'; experimentName: string; runId: string }

export function ExperimentTree({ onSelect }: ExperimentTreeProps) {
  const [runsById, setRunsById] = useState<Record<string, ExperimentRun>>({})
  const [view, setView] = useState<View>({ level: 'experiments' })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const url = `${protocol}//${window.location.host}/api/experiments/stream`
      socket = new WebSocket(url)
      wsRef.current = socket
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
        wsRef.current = null
        if (!cancelled) setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      cancelled = true
      socket?.close()
      wsRef.current = null
    }
  }, [])

  const experiments = useMemo(
    () => groupByExperiment(Object.values(runsById)),
    [runsById],
  )

  // Drop back to a valid level if the experiment/run we were drilled into vanished.
  useEffect(() => {
    if (view.level === 'experiments') return
    const exp = experiments.find((e) => e.experiment_name === view.experimentName)
    if (!exp) {
      setView({ level: 'experiments' })
      return
    }
    if (view.level === 'jobs' && !exp.runs.some((r) => r.run_id === view.runId)) {
      setView({ level: 'runs', experimentName: exp.experiment_name })
    }
  }, [experiments, view])

  function pickExperiment(exp: Experiment) {
    setView({ level: 'runs', experimentName: exp.experiment_name })
    setSelectedId(exp.experiment_name)
    onSelect({ kind: 'experiment', experiment_name: exp.experiment_name })
  }

  function pickRun(experimentName: string, run: Run) {
    setView({ level: 'jobs', experimentName, runId: run.run_id })
    setSelectedId(run.run_id)
    onSelect({
      kind: 'run',
      experiment_name: experimentName,
      run_id: run.run_id,
      run_name: run.run_name,
    })
  }

  function pickJob(experimentName: string, runId: string, job: Job) {
    setSelectedId(job.job_id)
    onSelect({
      kind: 'job',
      experiment_name: experimentName,
      run_id: runId,
      job_id: job.job_id,
    })
  }

  function backToExperiments() {
    if (view.level !== 'runs') return
    const expName = view.experimentName
    setView({ level: 'experiments' })
    setSelectedId(expName)
    onSelect({ kind: 'experiment', experiment_name: expName })
  }

  function backToRuns() {
    if (view.level !== 'jobs') return
    const expName = view.experimentName
    const runId = view.runId
    setView({ level: 'runs', experimentName: expName })
    setSelectedId(runId)
    const run = experiments
      .find((e) => e.experiment_name === expName)
      ?.runs.find((r) => r.run_id === runId)
    if (run) {
      onSelect({
        kind: 'run',
        experiment_name: expName,
        run_id: runId,
        run_name: run.run_name,
      })
    }
  }

  function handleRefresh() {
    setRunsById({})
    wsRef.current?.send(JSON.stringify({ type: 'force_refresh' }))
  }

  const currentExperiment =
    view.level !== 'experiments'
      ? experiments.find((e) => e.experiment_name === view.experimentName)
      : undefined
  const currentRun =
    view.level === 'jobs' && currentExperiment
      ? currentExperiment.runs.find((r) => r.run_id === view.runId)
      : undefined

  return (
    <div className="experiment-tree">
      <div className="experiment-tree__title">
        <span>Experiments</span>
        <button
          type="button"
          className="experiment-tree__refresh"
          onClick={handleRefresh}
          aria-label="Refresh"
        >
          <RefreshCw size={14} />
        </button>
      </div>
      {view.level === 'experiments' && experiments.length === 0 && (
        <div className="experiment-tree__status">No experiments</div>
      )}
      {view.level === 'experiments' &&
        experiments.map((exp) => (
          <div
            key={exp.experiment_name}
            className={`experiment-tree__row${selectedId === exp.experiment_name ? ' experiment-tree--selected' : ''}`}
            onClick={() => pickExperiment(exp)}
          >
            <FlaskConical size={16} /> {exp.experiment_name}
          </div>
        ))}
      {view.level === 'runs' && currentExperiment && (
        <>
          <button
            type="button"
            className="experiment-tree__back"
            onClick={backToExperiments}
          >
            <ArrowLeft size={16} /> {currentExperiment.experiment_name}
          </button>
          {currentExperiment.runs.map((run) => (
            <div
              key={run.run_id}
              className={`experiment-tree__row experiment-tree__row--nested${selectedId === run.run_id ? ' experiment-tree--selected' : ''}`}
              onClick={() => pickRun(currentExperiment.experiment_name, run)}
            >
              <Play size={16} /> {run.run_name}
            </div>
          ))}
        </>
      )}
      {view.level === 'jobs' && currentExperiment && currentRun && (
        <>
          <button
            type="button"
            className="experiment-tree__back"
            onClick={backToRuns}
          >
            <ArrowLeft size={16} /> {currentRun.run_name}
          </button>
          {currentRun.jobs.map((job) => (
            <div
              key={job.job_id}
              className={`experiment-tree__row experiment-tree__row--nested${selectedId === job.job_id ? ' experiment-tree--selected' : ''}`}
              onClick={() =>
                pickJob(currentExperiment.experiment_name, currentRun.run_id, job)
              }
            >
              <Cog size={16} /> {job.job_id}
              <span className={`experiment-tree__job-status experiment-tree__job-status--${job.status}`}>
                {job.status}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
