import { useEffect, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Brush } from 'recharts'
import { TitledFrame } from './TitledFrame'
import './RunDashboard.css'

type MetricPoint = { step: number; value: number }
type Job = { job_id: string; status: string; retry: boolean }

const STOPPABLE_STATUSES = new Set(['pending', 'running'])

function isStoppable(job: Job): boolean {
  if (STOPPABLE_STATUSES.has(job.status)) return true
  return job.status === 'failed' && job.retry
}

function ignoreAbort(err: unknown): void {
  if (err instanceof DOMException && err.name === 'AbortError') return
}

type Props = {
  runId: string
  runName: string
  experimentName: string
}

export function RunDashboard({ runId, runName, experimentName }: Props) {
  const [params, setParams] = useState<Record<string, string> | null>(null)
  const [metricKeys, setMetricKeys] = useState<string[] | null>(null)
  const [metricData, setMetricData] = useState<Record<string, MetricPoint[]>>({})
  const [artifacts, setArtifacts] = useState<string[] | null>(null)
  const [mlflowUrl, setMlflowUrl] = useState<string | null>(null)
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [stopping, setStopping] = useState(false)

  useEffect(() => {
    setParams(null)
    setMetricKeys(null)
    setMetricData({})
    setArtifacts(null)
    setMlflowUrl(null)
    setJobs(null)
    const c = new AbortController()
    const opts = { signal: c.signal }

    fetch(`/api/runs/${runId}/params`, opts)
      .then((r) => r.json() as Promise<Record<string, string>>)
      .then(setParams)
      .catch(ignoreAbort)

    fetch(`/api/runs/${runId}/metrics`, opts)
      .then((r) => r.json() as Promise<string[]>)
      .then(setMetricKeys)
      .catch(ignoreAbort)

    fetch(`/api/runs/${runId}/artifacts`, opts)
      .then((r) => r.json() as Promise<string[]>)
      .then(setArtifacts)
      .catch(ignoreAbort)

    fetch(`/api/runs/${runId}/url`, opts)
      .then((r) => r.json() as Promise<{ url: string }>)
      .then((d) => setMlflowUrl(d.url))
      .catch(ignoreAbort)

    fetch(`/api/runs/${runId}/jobs`, opts)
      .then((r) => r.json() as Promise<Job[]>)
      .then(setJobs)
      .catch(ignoreAbort)

    return () => c.abort()
  }, [runId])

  useEffect(() => {
    if (metricKeys === null) return
    const c = new AbortController()
    metricKeys.forEach((key) => {
      fetch(`/api/runs/${runId}/metrics/${encodeURIComponent(key)}`, {
        signal: c.signal,
      })
        .then((r) => r.json() as Promise<MetricPoint[]>)
        .then((points) => setMetricData((prev) => ({ ...prev, [key]: points })))
        .catch(ignoreAbort)
    })
    return () => c.abort()
  }, [runId, metricKeys])

  const hasStoppableJobs = jobs?.some(isStoppable) ?? false

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

      {params !== null && Object.keys(params).length > 0 && (
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

      {metricKeys !== null && metricKeys.length > 0 && (
        <div className="run-dashboard__section">
          <div className="run-dashboard__section-title">Metrics</div>
          {metricKeys.map((key) => (
            <div key={key} className="run-dashboard__chart">
              <div className="run-dashboard__chart-title">{key}</div>
              {metricData[key] === undefined ? (
                <div className="run-dashboard__chart-loading">Loading...</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={metricData[key]}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="step" />
                    <YAxis />
                    <Tooltip />
                    <Line type="monotone" dataKey="value" stroke="#000" dot={false} />
                    <Brush dataKey="step" height={20} stroke="#999" />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          ))}
        </div>
      )}

      {artifacts !== null && artifacts.length > 0 && (
        <div className="run-dashboard__section">
          <TitledFrame title="Artifacts">
            <ul className="run-dashboard__artifacts">
              {artifacts.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          </TitledFrame>
        </div>
      )}
    </div>
  )
}
