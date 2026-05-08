import { useState } from 'react'
import { Allotment } from 'allotment'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Brush } from 'recharts'
import { RunNotesPanel } from './RunNotesPanel'
import { useStreamList } from '../useStreamList'
import './RunDashboard.css'

type MetricPoint = { step: number; value: number; timestamp: number }

export type Job = { job_id: string; status: string; retry: boolean }

type Param = { id: string; run_name: string; name: string; value: string }
type Metric = { id: string; run_name: string; name: string; history: MetricPoint[] }
type Url = { id: string; run_name: string; url: string }
type DashboardItem = Param | Metric | Url

function isParam(i: DashboardItem): i is Param { return 'value' in i }
function isMetric(i: DashboardItem): i is Metric { return 'history' in i }
function isUrl(i: DashboardItem): i is Url { return 'url' in i }

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
  startedAtMs: number | null
  endedAtMs: number | null
}

export function RunDashboard({ runId, runName, experimentName, jobs, startedAtMs, endedAtMs }: Props) {
  const items = useStreamList<DashboardItem>(
    `/api/runs/${runName}/stream`,
    (i) => i.id,
  )
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

  const allItems = Object.values(items)
  const params = allItems.filter(isParam)
  const metrics = allItems.filter(isMetric)
  const url = allItems.find(isUrl)?.url

  return (
    <Allotment>
      <Allotment.Pane>
        <div className="run-dashboard">
          <div className="run-dashboard__header">
            <div className="run-dashboard__title-block">
              <div className="run-dashboard__title">{experimentName} / {runName}</div>
              <div className="run-dashboard__meta">
                Started {startedAtMs ? new Date(startedAtMs).toLocaleString() : '-'}
                {' | '}
                Ended {endedAtMs ? new Date(endedAtMs).toLocaleString() : '-'}
              </div>
            </div>
            <div className="run-dashboard__actions">
              {hasStoppableJobs && (
                <button
                  type="button"
                  className="btn"
                  onClick={handleStop}
                  disabled={stopping}
                >
                  {stopping ? 'Stopping...' : 'Stop all jobs'}
                </button>
              )}
              {url && (
                <a
                  className="btn"
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in MLflow
                </a>
              )}
            </div>
          </div>

          {params.length > 0 && (
            <div className="run-dashboard__section">
              <div className="run-dashboard__section-title">Parameters</div>
              <table className="run-dashboard__table">
                <tbody>
                  {params.map((p) => (
                    <tr key={p.id}><td>{p.name}</td><td>{p.value}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {metrics.length > 0 && (
            <div className="run-dashboard__section">
              <div className="run-dashboard__section-title">Metrics</div>
              {metrics.map((m) => (
                <div key={m.id} className="run-dashboard__chart">
                  <div className="run-dashboard__chart-title">{m.name}</div>
                  <ResponsiveContainer width="100%" height={200}>
                    <LineChart data={m.history}>
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="step" tick={{ fontSize: 11 }} />
                      <YAxis tick={{ fontSize: 11 }} />
                      <Tooltip wrapperStyle={{ fontSize: 11 }} />
                      <Line type="monotone" dataKey="value" stroke="#000" dot={false} />
                      <Brush dataKey="step" height={20} stroke="#999" />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              ))}
            </div>
          )}
        </div>
      </Allotment.Pane>
      <Allotment.Pane preferredSize={400} minSize={240} maxSize={600}>
        <RunNotesPanel runName={runName} />
      </Allotment.Pane>
    </Allotment>
  )
}
