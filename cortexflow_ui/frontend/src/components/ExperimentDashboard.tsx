import { useEffect, useState } from 'react'
import './ExperimentDashboard.css'

type ExperimentMeta = {
  experiment_name: string
  experiment_id: string
  lifecycle_stage: string
  creation_time: number
  last_update_time: number
  run_count: number
  tags: Record<string, string>
}

type Props = {
  experimentName: string
}

export function ExperimentDashboard({ experimentName }: Props) {
  const [meta, setMeta] = useState<ExperimentMeta | null>(null)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    setStatus('loading')
    const controller = new AbortController()
    fetch(`/api/experiments/${encodeURIComponent(experimentName)}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<ExperimentMeta>
      })
      .then((data) => {
        setMeta(data)
        setStatus('ready')
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setStatus('error')
      })
    return () => controller.abort()
  }, [experimentName])

  if (status === 'loading') return <div className="experiment-dashboard__status">Loading...</div>
  if (status === 'error' || !meta) return <div className="experiment-dashboard__status">Failed to load</div>

  return (
    <div className="experiment-dashboard">
      <div className="experiment-dashboard__title">{meta.experiment_name}</div>
      <table className="experiment-dashboard__table">
        <tbody>
          <tr><td>ID</td><td>{meta.experiment_id}</td></tr>
          <tr><td>Stage</td><td>{meta.lifecycle_stage}</td></tr>
          <tr><td>Runs</td><td>{meta.run_count}</td></tr>
          <tr><td>Created</td><td>{new Date(meta.creation_time).toLocaleString()}</td></tr>
          <tr><td>Updated</td><td>{new Date(meta.last_update_time).toLocaleString()}</td></tr>
          {Object.entries(meta.tags).map(([k, v]) => (
            <tr key={k}><td>{k}</td><td>{v}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
