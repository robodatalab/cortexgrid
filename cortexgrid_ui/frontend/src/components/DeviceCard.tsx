// One machine's slate: the health verdict the backend already computes for a
// pod, rendered the same way wherever it appears. Shared so the deployment
// card shows a device exactly as the Infrastructure Status tab shows it -
// two renderings of the same state would drift.
import type { ReactNode } from 'react'
import { TitledFrame } from './TitledFrame'
import './DeviceCard.css'

export type PodStatus = {
  name: string
  namespace: string
  kind: string
  node: string | null
  pod_ip: string | null
  state: string
  health: string
  healthy: boolean
  logs: string | null
}

type Props = {
  pod: PodStatus
  // Defaults to the pod's own name, which is what the infrastructure tab
  // lists by; the deployment card titles by the machine instead.
  title?: string
  // Rendered in the meta row before the pod's own fields, for context the pod
  // itself does not carry (e.g. which replica landed here).
  leading?: ReactNode
}

export function DeviceCard({ pod, title, leading }: Props) {
  const heartClass = pod.healthy ? 'infra-card__heart--ok' : 'infra-card__heart--bad'
  return (
    <TitledFrame title={title ?? pod.name}>
      <div className="infra-card__meta">
        <span
          className={`infra-card__heart ${heartClass}`}
          aria-label={pod.healthy ? 'healthy' : 'unhealthy'}
        >
          ♥
        </span>
        {leading}
        <span>state: {pod.state}</span>
        <span>health: {pod.health}</span>
        <span>node: {pod.node ?? '—'}</span>
      </div>
      {pod.logs && (
        <details className="infra-card__logs">
          <summary>Recent logs</summary>
          <pre>{pod.logs}</pre>
        </details>
      )}
      <div className="infra-card__footer">
        <span className="infra-card__kind">{pod.kind}</span>
      </div>
    </TitledFrame>
  )
}
