import { useEffect, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Brush } from 'recharts'
import './RunDashboard.css'

type MetricPoint = { step: number; value: number }
type Job = { job_id: string; status: string }

const STOPPABLE_STATUSES = new Set(['pending', 'running'])

type Props = {
  runId: string
  runName: string
  experimentName: string
}

export function RunDashboard({ runId, runName, experimentName }: Props) {
  const [params, setParams] = useState<Record<string, string>>({})
  const [metricKeys, setMetricKeys] = useState<string[]>([])
  const [metricData, setMetricData] = useState<Record<string, MetricPoint[]>>({})
  const [artifacts, setArtifacts] = useState<string[]>([])
  const [mlflowUrl, setMlflowUrl] = useState<string | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [stopping, setStopping] = useState(false)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    setStatus('loading')
    const controller = new AbortController()
    const opts = { signal: controller.signal }

    Promise.all([
      fetch(`/api/runs/${runId}/params`, opts).then((r) => r.json() as Promise<Record<string, string>>),
      fetch(`/api/runs/${runId}/metrics`, opts).then((r) => r.json() as Promise<string[]>),
      fetch(`/api/runs/${runId}/artifacts`, opts).then((r) => r.json() as Promise<string[]>),
      fetch(`/api/runs/${runId}/url`, opts).then((r) => r.json() as Promise<{ url: string }>),
      fetch(`/api/runs/${runId}/jobs`, opts).then((r) => r.json() as Promise<Job[]>),
    ])
      .then(([p, m, a, u, j]) => {
        setParams(p)
        setMetricKeys(m)
        setArtifacts(a)
        setMlflowUrl(u.url)
        setJobs(j)
        return Promise.all(
          m.map((key) =>
            fetch(`/api/runs/${runId}/metrics/${encodeURIComponent(key)}`, opts)
              .then((r) => r.json() as Promise<MetricPoint[]>)
              .then((points) => [key, points] as const)
          )
        )
      })
      .then((entries) => {
        setMetricData(Object.fromEntries(entries))
        setStatus('ready')
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setStatus('error')
      })

    return () => controller.abort()
  }, [runId])

  const hasStoppableJobs = jobs.some((j) => STOPPABLE_STATUSES.has(j.status))

  async function handleStop() {
    setStopping(true)
    try {
      await fetch(`/api/runs/${runId}/stop`, { method: 'POST' })
      const res = await fetch(`/api/runs/${runId}/jobs`)
      setJobs(await res.json())
    } finally {
      setStopping(false)
    }
  }

  if (status === 'loading') return <div className="run-dashboard__status">Loading...</div>
  if (status === 'error') return <div className="run-dashboard__status">Failed to load</div>

  return (
    <div className="run-dashboard">
      <div className="run-dashboard__header">
        <div className="run-dashboard__title">{experimentName} / {runName}</div>
        <div className="run-dashboard__actions">
          {hasStoppableJobs && (
            <button
              type="button"
              className="run-dashboard__open-button"
              onClick={handleStop}
              disabled={stopping}
            >
              {stopping ? 'Stopping...' : 'Stop all jobs'}
            </button>
          )}
          {mlflowUrl && (
            <a
              className="run-dashboard__open-button"
              href={mlflowUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open in MLflow
            </a>
          )}
        </div>
      </div>

      {Object.keys(params).length > 0 && (
        <div className="run-dashboard__section">
          <div className="run-dashboard__section-title">Parameters</div>
          <table className="run-dashboard__table">
            <tbody>
              {Object.entries(params).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td>{v}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {metricKeys.length > 0 && (
        <div className="run-dashboard__section">
          <div className="run-dashboard__section-title">Metrics</div>
          {metricKeys.map((key) => (
            <div key={key} className="run-dashboard__chart">
              <div className="run-dashboard__chart-title">{key}</div>
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={metricData[key] ?? []}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="step" />
                  <YAxis />
                  <Tooltip />
                  <Line type="monotone" dataKey="value" stroke="#000" dot={false} />
                  <Brush dataKey="step" height={20} stroke="#999" />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ))}
        </div>
      )}

      {artifacts.length > 0 && (
        <div className="run-dashboard__section">
          <div className="run-dashboard__section-title">Artifacts</div>
          <ul className="run-dashboard__artifacts">
            {artifacts.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
