import { useEffect, useState } from 'react'
import './JobDashboard.css'

type LifecycleEvent = {
  attempt: number
  state: string
  start: string
  end: string | null
  ray_job_id: string | null
  error: string | null
}

type JobDetail = {
  job_id: string
  status: string
  retry: boolean
  stop_requested: boolean
  ray_job_id: string | null
  ray_status: string | null
  ray_url: string | null
  history: LifecycleEvent[]
}

type Props = {
  runId: string
  jobId: string
}

function groupByAttempt(history: LifecycleEvent[]): Map<number, LifecycleEvent[]> {
  const groups = new Map<number, LifecycleEvent[]>()
  for (const event of history) {
    const list = groups.get(event.attempt) ?? []
    list.push(event)
    groups.set(event.attempt, list)
  }
  return groups
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  })
}

function attemptRayJobId(events: LifecycleEvent[]): string | null {
  for (const event of events) {
    if (event.ray_job_id !== null) return event.ray_job_id
  }
  return null
}

function isFailedAttempt(events: LifecycleEvent[]): boolean {
  return events.some((event) => event.state === 'failed' || event.error !== null)
}

function AttemptTimeline({
  history,
  logsByRayJobId,
}: {
  history: LifecycleEvent[]
  logsByRayJobId: Record<string, string>
}) {
  if (history.length === 0) return null
  const groups = groupByAttempt(history)
  const attempts = Array.from(groups.keys()).sort((a, b) => a - b)
  return (
    <div className="job-dashboard__section">
      <div className="job-dashboard__section-title">Timeline</div>
      {attempts.map((attempt) => {
        const events = groups.get(attempt)!
        const failed = isFailedAttempt(events)
        const rayJobId = attemptRayJobId(events)
        const logs = rayJobId ? logsByRayJobId[rayJobId] : undefined
        return (
          <div key={attempt} className="job-dashboard__attempt">
            <div className="job-dashboard__attempt-header">
              Attempt {attempt} — {formatDate(events[0].start)}
            </div>
            <div className="job-dashboard__attempt-row">
              {events.map((event, index) => (
                <div
                  key={index}
                  className={`job-dashboard__event job-dashboard__event--${event.state}`}
                >
                  <div className="job-dashboard__event-state">{event.state}</div>
                  <div className="job-dashboard__event-time">
                    {formatTime(event.start)} – {event.end ? formatTime(event.end) : '…'}
                  </div>
                  {event.error && (
                    <div className="job-dashboard__event-error">{event.error}</div>
                  )}
                </div>
              ))}
            </div>
            {failed && logs !== undefined && (
              <pre className="job-dashboard__logs">{logs || '(no logs)'}</pre>
            )}
          </div>
        )
      })}
    </div>
  )
}

function failedAttemptRayJobIds(history: LifecycleEvent[]): string[] {
  const groups = groupByAttempt(history)
  const ids: string[] = []
  for (const events of groups.values()) {
    if (!isFailedAttempt(events)) continue
    const id = attemptRayJobId(events)
    if (id !== null) ids.push(id)
  }
  return ids
}

export function JobDashboard({ runId, jobId }: Props) {
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [logsByRayJobId, setLogsByRayJobId] = useState<Record<string, string>>({})
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
        for (const rayJobId of failedAttemptRayJobIds(data.history)) {
          fetch(`/api/ray/jobs/${rayJobId}/logs`, { signal: controller.signal })
            .then((r) => (r.ok ? r.json() as Promise<{ logs: string }> : null))
            .then((j) => {
              if (j) setLogsByRayJobId((prev) => ({ ...prev, [rayJobId]: j.logs }))
            })
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

      <AttemptTimeline history={detail.history} logsByRayJobId={logsByRayJobId} />
    </div>
  )
}
