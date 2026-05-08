import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ExperimentTree } from '../src/components/ExperimentTree'
import type { ExperimentRun, Selection } from '../src/components/ExperimentTree'

const expA: ExperimentRun[] = [
  {
    experiment_name: 'exp-a',
    run_id: 'r1',
    run_name: 'run-1',
    jobs: [{ job_id: 'j1', status: 'running' }],
  },
  { experiment_name: 'exp-a', run_id: 'r2', run_name: 'run-2', jobs: [] },
]

const expB: ExperimentRun[] = [
  {
    experiment_name: 'exp-b',
    run_id: 'r3',
    run_name: 'run-3',
    jobs: [{ job_id: 'j2', status: 'finished' }],
  },
]

const experimentNames = ['exp-a', 'exp-b']
const runsByExperiment = { 'exp-a': expA, 'exp-b': expB }

function renderTree(selection: Selection | null) {
  render(
    <ExperimentTree
      experimentNames={experimentNames}
      runsByExperiment={runsByExperiment}
      selection={selection}
      onSelect={vi.fn()}
    />,
  )
}

function StatefulTree() {
  const [selection, setSelection] = useState<Selection | null>(null)
  return (
    <ExperimentTree
      experimentNames={experimentNames}
      runsByExperiment={runsByExperiment}
      selection={selection}
      onSelect={setSelection}
      onRefresh={vi.fn()}
    />
  )
}

describe('ExperimentTree', () => {
  it('shows experiments when nothing is selected', () => {
    renderTree(null)
    expect(screen.getByText('exp-a')).toBeInTheDocument()
    expect(screen.getByText('exp-b')).toBeInTheDocument()
  })

  it('keeps experiments visible when a job is selected', () => {
    renderTree({ kind: 'job', experiment_name: 'exp-a', run_id: 'r1', job_id: 'j1' })
    expect(screen.getByText('exp-a')).toBeInTheDocument()
    expect(screen.getByText('exp-b')).toBeInTheDocument()
  })

  it("shows the selected experiment's runs", () => {
    renderTree({ kind: 'experiment', experiment_name: 'exp-a' })
    expect(screen.getByText('run-1')).toBeInTheDocument()
    expect(screen.getByText('run-2')).toBeInTheDocument()
  })

  it("shows the selected run's jobs", () => {
    renderTree({ kind: 'run', experiment_name: 'exp-a', run_id: 'r1', run_name: 'run-1' })
    expect(screen.getByText('j1')).toBeInTheDocument()
  })

  it('clicking an experiment reveals its runs', async () => {
    const user = userEvent.setup()
    render(<StatefulTree />)
    await user.click(screen.getByText('exp-a'))
    expect(screen.getByText('run-1')).toBeInTheDocument()
    expect(screen.getByText('run-2')).toBeInTheDocument()
  })

  it('clicking a run reveals its jobs', async () => {
    const user = userEvent.setup()
    render(<StatefulTree />)
    await user.click(screen.getByText('exp-a'))
    await user.click(screen.getByText('run-1'))
    expect(screen.getByText('j1')).toBeInTheDocument()
  })
})
