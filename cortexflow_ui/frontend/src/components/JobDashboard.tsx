import { useEffect, useState } from 'react'
import './JobDashboard.css'

type JobDetail = {
  job_id: string
  status: string
  error: string | null
  retry: boolean
  stop_requested: boolean
  ray_job_id: string | null
  ray_status: string | null
  ray_url: string | null
}

type Props = {
  runId: string
  jobId: string
}

export function JobDashboard({ runId, jobId }: Props) {
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [logs, setLogs] = useState<string | null>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    setStatus('loading')
    const controller = new AbortController()
    fetch(`/api/runs/${runId}/jobs/${jobId}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<JobDetail>
      })
      .then((data) => {
        setDetail(data)
        setStatus('ready')
        if (data.ray_job_id) {
          fetch(`/api/ray/jobs/${data.ray_job_id}/logs`, { signal: controller.signal })
            .then((r) => (r.ok ? r.json() as Promise<{ logs: string }> : null))
            .then((j) => { if (j) setLogs(j.logs) })
            .catch((err: unknown) => {
              if (err instanceof DOMException && err.name === 'AbortError') return
            })
        }
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setStatus('error')
      })
    return () => controller.abort()
  }, [runId, jobId])

  if (status === 'loading') return <div className="job-dashboard__status">Loading...</div>
  if (status === 'error' || !detail) return <div className="job-dashboard__status">Failed to load</div>

  return (
    <div className="job-dashboard">
      <div className="job-dashboard__header">
        <div className="job-dashboard__title">Job: {detail.job_id}</div>
        {detail.ray_url && (
          <a
            className="job-dashboard__open-button"
            href={detail.ray_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open in Ray
          </a>
        )}
      </div>

      <div className="job-dashboard__section">
        <div className="job-dashboard__section-title">Lifecycle</div>
        <table className="job-dashboard__table">
          <tbody>
            <tr><td>Status</td><td>{detail.status}</td></tr>
            <tr><td>Stop requested</td><td>{detail.stop_requested ? 'yes' : 'no'}</td></tr>
            <tr><td>Retry</td><td>{detail.retry ? 'yes' : 'no'}</td></tr>
            <tr><td>Ray job ID</td><td>{detail.ray_job_id ?? '—'}</td></tr>
          </tbody>
        </table>
      </div>

      <div className="job-dashboard__section">
        <div className="job-dashboard__section-title">Ray</div>
        <table className="job-dashboard__table">
          <tbody>
            <tr><td>Status</td><td>{detail.ray_status ?? '—'}</td></tr>
          </tbody>
        </table>
      </div>

      {detail.error && (
        <div className="job-dashboard__section">
          <div className="job-dashboard__section-title">Error</div>
          <div className="job-dashboard__error">{detail.error}</div>
        </div>
      )}

      {logs !== null && (
        <div className="job-dashboard__section">
          <div className="job-dashboard__section-title">Ray logs</div>
          <pre className="job-dashboard__logs">{logs || '(no logs yet)'}</pre>
        </div>
      )}
    </div>
  )
}
