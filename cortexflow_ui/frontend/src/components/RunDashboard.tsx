import { useState } from 'react'
import { Allotment } from 'allotment'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Brush } from 'recharts'
import { TitledFrame } from './TitledFrame'
import { RunNotesPanel } from './RunNotesPanel'
import { useStreamState } from '../useStreamState'
import './RunDashboard.css'

type MetricPoint = { step: number; value: number }

export type Job = { job_id: string; status: string; retry: boolean }

type DashboardData = {
  params: Record<string, string>
  metrics: Record<string, MetricPoint[]>
  artifacts: string[]
  url: string
}

const STOPPABLE_STATUSES = new Set(['pending', 'running'])

function isStoppable(job: Job): boolean {
  if (STOPPABLE_STATUSES.has(job.status)) return true
  return job.status === 'failed' && job.retry
}

type Props = {
  runId: string
  runName: string
  experimentName: string
  jobs: Job[] | null
}

export function RunDashboard({ runId, runName, experimentName, jobs }: Props) {
  const data = useStreamState<DashboardData>(`/api/runs/${runId}/stream`)
  const [stopping, setStopping] = useState(false)

  const hasStoppableJobs = jobs?.some(isStoppable) ?? false

  async function handleStop() {
    setStopping(true)
    try {
      await fetch(`/api/runs/${runId}/stop`, { method: 'POST' })
    } finally {
      setStopping(false)
    }
  }

  const metricKeys = data ? Object.keys(data.metrics) : []

  return (
    <Allotment>
      <Allotment.Pane>
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
              {data && (
                <a
                  className="run-dashboard__open-button"
                  href={data.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in MLflow
                </a>
              )}
            </div>
          </div>

          {data && Object.keys(data.params).length > 0 && (
            <div className="run-dashboard__section">
              <div className="run-dashboard__section-title">Parameters</div>
              <table className="run-dashboard__table">
                <tbody>
                  {Object.entries(data.params).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td>{v}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {metricKeys.length > 0 && data && (
            <div className="run-dashboard__section">
              <div className="run-dashboard__section-title">Metrics</div>
              {metricKeys.map((key) => (
                <div key={key} className="run-dashboard__chart">
                  <div className="run-dashboard__chart-title">{key}</div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={data.metrics[key]}>
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

          {data && data.artifacts.length > 0 && (
            <div className="run-dashboard__section">
              <TitledFrame title="Artifacts">
                <ul className="run-dashboard__artifacts">
                  {data.artifacts.map((a) => (
                    <li key={a}>{a}</li>
                  ))}
                </ul>
              </TitledFrame>
            </div>
          )}
        </div>
      </Allotment.Pane>
      <Allotment.Pane preferredSize={400} minSize={240} maxSize={600}>
        <RunNotesPanel runId={runId} />
      </Allotment.Pane>
    </Allotment>
  )
}
