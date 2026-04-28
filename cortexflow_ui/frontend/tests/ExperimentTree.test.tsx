import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ExperimentTree } from '../src/components/ExperimentTree'

class MockWebSocket {
  static instances: MockWebSocket[] = []
  url: string
  onmessage: ((e: MessageEvent) => void) | null = null
  onclose: ((e: CloseEvent) => void) | null = null
  onopen: ((e: Event) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  readyState = 1
  sent: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(data: string) {
    this.sent.push(data)
  }

  close() {
    this.readyState = 3
  }

  emit(data: unknown) {
    this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) }))
  }
}

const sample = [
  {
    type: 'added',
    run: {
      experiment_name: 'exp-a',
      run_id: 'r1',
      run_name: 'run-1',
      jobs: [{ job_id: 'j1', status: 'running' }],
    },
  },
  {
    type: 'added',
    run: { experiment_name: 'exp-a', run_id: 'r2', run_name: 'run-2', jobs: [] },
  },
  {
    type: 'added',
    run: {
      experiment_name: 'exp-b',
      run_id: 'r3',
      run_name: 'run-3',
      jobs: [{ job_id: 'j2', status: 'finished' }],
    },
  },
]

function lastWs(): MockWebSocket {
  const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1]
  if (!ws) throw new Error('no WebSocket created')
  return ws
}

function emitSample(ws: MockWebSocket): void {
  act(() => {
    for (const e of sample) ws.emit(e)
  })
}

describe('ExperimentTree', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows experiment names at the top level and no runs or jobs', async () => {
    render(<ExperimentTree onSelect={vi.fn()} />)
    emitSample(lastWs())

    await waitFor(() => expect(screen.getByText('exp-a')).toBeInTheDocument())
    expect(screen.getByText('exp-b')).toBeInTheDocument()
    expect(screen.queryByText('run-1')).not.toBeInTheDocument()
    expect(screen.queryByText('j1')).not.toBeInTheDocument()
  })

  it('drills into runs and shows a back button when an experiment is clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ExperimentTree onSelect={onSelect} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-a'))

    await user.click(screen.getByText('exp-a'))

    expect(screen.getByText('run-1')).toBeInTheDocument()
    expect(screen.getByText('run-2')).toBeInTheDocument()
    expect(screen.queryByText('run-3')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /exp-a/ })).toBeInTheDocument()
    expect(onSelect).toHaveBeenCalledWith({
      kind: 'experiment',
      experiment_name: 'exp-a',
    })
  })

  it('drills into jobs and shows a back button when a run is clicked', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ExperimentTree onSelect={onSelect} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-a'))

    await user.click(screen.getByText('exp-a'))
    await user.click(screen.getByText('run-1'))

    expect(screen.getByText('j1')).toBeInTheDocument()
    expect(screen.queryByText('run-2')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run-1/ })).toBeInTheDocument()
    expect(onSelect).toHaveBeenLastCalledWith({
      kind: 'run',
      experiment_name: 'exp-a',
      run_id: 'r1',
      run_name: 'run-1',
    })
  })

  it('back from runs returns to the experiment list and re-selects the experiment', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ExperimentTree onSelect={onSelect} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-a'))

    await user.click(screen.getByText('exp-a'))
    onSelect.mockClear()
    await user.click(screen.getByRole('button', { name: /exp-a/ }))

    expect(screen.getByText('exp-a')).toBeInTheDocument()
    expect(screen.getByText('exp-b')).toBeInTheDocument()
    expect(screen.queryByText('run-1')).not.toBeInTheDocument()
    expect(onSelect).toHaveBeenCalledWith({
      kind: 'experiment',
      experiment_name: 'exp-a',
    })
  })

  it('back from jobs returns to the runs list and re-selects the run', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ExperimentTree onSelect={onSelect} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-a'))

    await user.click(screen.getByText('exp-a'))
    await user.click(screen.getByText('run-1'))
    onSelect.mockClear()
    await user.click(screen.getByRole('button', { name: /run-1/ }))

    expect(screen.getByText('run-1')).toBeInTheDocument()
    expect(screen.getByText('run-2')).toBeInTheDocument()
    expect(screen.queryByText('j1')).not.toBeInTheDocument()
    expect(onSelect).toHaveBeenCalledWith({
      kind: 'run',
      experiment_name: 'exp-a',
      run_id: 'r1',
      run_name: 'run-1',
    })
  })

  it('clicking a job fires onSelect and stays on the jobs view', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<ExperimentTree onSelect={onSelect} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-a'))

    await user.click(screen.getByText('exp-a'))
    await user.click(screen.getByText('run-1'))
    await user.click(screen.getByText('j1'))

    expect(screen.getByText('j1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /run-1/ })).toBeInTheDocument()
    expect(onSelect).toHaveBeenLastCalledWith({
      kind: 'job',
      experiment_name: 'exp-a',
      run_id: 'r1',
      job_id: 'j1',
    })
  })

  it('refresh sends force_refresh and clears the local list', async () => {
    const user = userEvent.setup()
    render(<ExperimentTree onSelect={vi.fn()} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-a'))

    await user.click(screen.getByRole('button', { name: /refresh/i }))

    expect(screen.queryByText('exp-a')).not.toBeInTheDocument()
    expect(lastWs().sent).toContain(JSON.stringify({ type: 'force_refresh' }))
  })

  it('removed event drops the run from the list', async () => {
    render(<ExperimentTree onSelect={vi.fn()} />)
    emitSample(lastWs())
    await waitFor(() => screen.getByText('exp-b'))

    act(() => {
      lastWs().emit({ type: 'removed', run_id: 'r3' })
    })

    await waitFor(() => expect(screen.queryByText('exp-b')).not.toBeInTheDocument())
    expect(screen.getByText('exp-a')).toBeInTheDocument()
  })
})
