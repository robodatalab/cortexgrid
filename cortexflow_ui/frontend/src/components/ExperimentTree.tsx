import { useEffect, useState } from 'react'
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
  | { level: 'runs'; experiment: Experiment }
  | { level: 'jobs'; experiment: Experiment; run: Run }

export function ExperimentTree({ onSelect }: ExperimentTreeProps) {
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [view, setView] = useState<View>({ level: 'experiments' })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [refreshTick, setRefreshTick] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setStatus('loading')
    fetch('/api/experiments', { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<ExperimentRun[]>
      })
      .then((items) => {
        setExperiments(groupByExperiment(items))
        setStatus('ready')
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setStatus('error')
      })
    return () => controller.abort()
  }, [refreshTick])

  function pickExperiment(exp: Experiment) {
    setView({ level: 'runs', experiment: exp })
    setSelectedId(exp.experiment_name)
    onSelect({ kind: 'experiment', experiment_name: exp.experiment_name })
  }

  function pickRun(exp: Experiment, run: Run) {
    setView({ level: 'jobs', experiment: exp, run })
    setSelectedId(run.run_id)
    onSelect({
      kind: 'run',
      experiment_name: exp.experiment_name,
      run_id: run.run_id,
      run_name: run.run_name,
    })
  }

  function pickJob(exp: Experiment, run: Run, job: Job) {
    setSelectedId(job.job_id)
    onSelect({
      kind: 'job',
      experiment_name: exp.experiment_name,
      run_id: run.run_id,
      job_id: job.job_id,
    })
  }

  function backToExperiments() {
    if (view.level !== 'runs') return
    const exp = view.experiment
    setView({ level: 'experiments' })
    setSelectedId(exp.experiment_name)
    onSelect({ kind: 'experiment', experiment_name: exp.experiment_name })
  }

  function backToRuns() {
    if (view.level !== 'jobs') return
    const { experiment, run } = view
    setView({ level: 'runs', experiment })
    setSelectedId(run.run_id)
    onSelect({
      kind: 'run',
      experiment_name: experiment.experiment_name,
      run_id: run.run_id,
      run_name: run.run_name,
    })
  }

  return (
    <div className="experiment-tree">
      <div className="experiment-tree__title">
        <span>Experiments</span>
        <button
          type="button"
          className="experiment-tree__refresh"
          onClick={() => setRefreshTick((t) => t + 1)}
          aria-label="Refresh"
        >
          <RefreshCw size={14} />
        </button>
      </div>
      {status === 'loading' && (
        <div className="experiment-tree__status">Loading...</div>
      )}
      {status === 'error' && (
        <div className="experiment-tree__status">Failed to load</div>
      )}
      {status === 'ready' && view.level === 'experiments' && experiments.length === 0 && (
        <div className="experiment-tree__status">No experiments</div>
      )}
      {status === 'ready' && view.level === 'experiments' &&
        experiments.map((exp) => (
          <div
            key={exp.experiment_name}
            className={`experiment-tree__row${selectedId === exp.experiment_name ? ' experiment-tree--selected' : ''}`}
            onClick={() => pickExperiment(exp)}
          >
            <FlaskConical size={16} /> {exp.experiment_name}
          </div>
        ))}
      {status === 'ready' && view.level === 'runs' && (
        <>
          <button
            type="button"
            className="experiment-tree__back"
            onClick={backToExperiments}
          >
            <ArrowLeft size={16} /> {view.experiment.experiment_name}
          </button>
          {view.experiment.runs.map((run) => (
            <div
              key={run.run_id}
              className={`experiment-tree__row${selectedId === run.run_id ? ' experiment-tree--selected' : ''}`}
              onClick={() => pickRun(view.experiment, run)}
            >
              <Play size={16} /> {run.run_name}
            </div>
          ))}
        </>
      )}
      {status === 'ready' && view.level === 'jobs' && (
        <>
          <button
            type="button"
            className="experiment-tree__back"
            onClick={backToRuns}
          >
            <ArrowLeft size={16} /> {view.run.run_name}
          </button>
          {view.run.jobs.map((job) => (
            <div
              key={job.job_id}
              className={`experiment-tree__row${selectedId === job.job_id ? ' experiment-tree--selected' : ''}`}
              onClick={() => pickJob(view.experiment, view.run, job)}
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
