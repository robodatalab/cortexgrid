import { useEffect, useState } from 'react'
import { FlaskConical, Play } from 'lucide-react'
import './ExperimentsSidebar.css'

type ExperimentRun = {
  experiment_name: string
  run_id: string
  run_name: string
}

type Run = {
  run_id: string
  run_name: string
}

type TreeNode = {
  experiment_name: string
  runs: Run[]
  expanded: boolean
}

function groupByExperiment(items: ExperimentRun[]): TreeNode[] {
  const map = new Map<string, Run[]>()
  for (const r of items) {
    const list = map.get(r.experiment_name) ?? []
    list.push({ run_id: r.run_id, run_name: r.run_name })
    map.set(r.experiment_name, list)
  }
  return Array.from(map, ([experiment_name, runs]) => ({
    experiment_name,
    runs,
    expanded: false,
  }))
}

export function ExperimentsSidebar() {
  const [nodes, setNodes] = useState<TreeNode[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/experiments', { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<ExperimentRun[]>
      })
      .then((runs) => {
        setNodes(groupByExperiment(runs))
        setStatus('ready')
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setStatus('error')
      })
    return () => controller.abort()
  }, [])

  function toggle(index: number) {
    setNodes((prev) =>
      prev.map((n, i) => (i === index ? { ...n, expanded: !n.expanded } : n))
    )
  }

  return (
    <aside className="sidebar">
      <div className="sidebar__title">Experiments</div>
      {status === 'loading' && (
        <div className="sidebar__status">Loading...</div>
      )}
      {status === 'error' && (
        <div className="sidebar__status">Failed to load</div>
      )}
      {status === 'ready' && nodes.length === 0 && (
        <div className="sidebar__status">No experiments</div>
      )}
      {nodes.map((node, i) => (
        <div key={node.experiment_name}>
          <div className="sidebar__experiment" onClick={() => toggle(i)}>
            <FlaskConical size={14} /> {node.experiment_name}
          </div>
          {node.expanded &&
            node.runs.map((run) => (
              <div key={run.run_id} className="sidebar__run">
                <Play size={12} /> {run.run_name}
              </div>
            ))}
        </div>
      ))}
    </aside>
  )
}
