import { useEffect, useState } from 'react'
import { FlaskConical, Play } from 'lucide-react'
import './ExperimentTree.css'

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

export type Selection =
  | { kind: 'experiment'; experiment_name: string }
  | { kind: 'run'; experiment_name: string; run_id: string; run_name: string }

type ExperimentTreeProps = {
  onSelect: (selection: Selection) => void
}

export function ExperimentTree({ onSelect }: ExperimentTreeProps) {
  const [nodes, setNodes] = useState<TreeNode[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [selected, setSelected] = useState<string | null>(null)

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
    <div className="experiment-tree">
      <div className="experiment-tree__title">Experiments</div>
      {status === 'loading' && (
        <div className="experiment-tree__status">Loading...</div>
      )}
      {status === 'error' && (
        <div className="experiment-tree__status">Failed to load</div>
      )}
      {status === 'ready' && nodes.length === 0 && (
        <div className="experiment-tree__status">No experiments</div>
      )}
      {nodes.map((node, i) => (
        <div key={node.experiment_name}>
          <div
            className={`experiment-tree__experiment${selected === node.experiment_name ? ' experiment-tree--selected' : ''}`}
            onClick={() => {
              toggle(i)
              setSelected(node.experiment_name)
              onSelect({ kind: 'experiment', experiment_name: node.experiment_name })
            }}
          >
            <FlaskConical size={14} /> {node.experiment_name}
          </div>
          {node.expanded &&
            node.runs.map((run) => (
              <div
                key={run.run_id}
                className={`experiment-tree__run${selected === run.run_id ? ' experiment-tree--selected' : ''}`}
                onClick={() => {
                  setSelected(run.run_id)
                  onSelect({ kind: 'run', experiment_name: node.experiment_name, run_id: run.run_id, run_name: run.run_name })
                }}
              >
                <Play size={12} /> {run.run_name}
              </div>
            ))}
        </div>
      ))}
    </div>
  )
}
