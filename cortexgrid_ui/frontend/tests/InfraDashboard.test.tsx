import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { InfraDashboard } from '../src/components/InfraDashboard'

const sample = {
  overall: false,
  pods: [
    {
      name: 'redis-0',
      namespace: 'redis',
      state: 'Running',
      health: 'ready',
      healthy: true,
      logs: null,
    },
    {
      name: 'ray-head-0',
      namespace: 'ray',
      state: 'Running',
      health: 'not-ready',
      healthy: false,
      logs: 'boom\nstacktrace',
    },
  ],
}

function stubFetch(body: unknown, ok = true, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({
        ok,
        status,
        json: () => Promise.resolve(body),
      } as Response),
    ),
  )
}

describe('InfraDashboard', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders a card per pod grouped by namespace', async () => {
    stubFetch(sample)
    render(<InfraDashboard />)
    await waitFor(() => {
      expect(screen.getByText('redis-0')).toBeInTheDocument()
      expect(screen.getByText('ray-head-0')).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: 'redis' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { level: 2, name: 'ray' })).toBeInTheDocument()
    })
  })

  it('shows logs only for unhealthy pods', async () => {
    stubFetch(sample)
    render(<InfraDashboard />)
    await waitFor(() =>
      expect(screen.getByText('ray-head-0')).toBeInTheDocument(),
    )
    const summaries = screen.getAllByText('Recent logs')
    expect(summaries).toHaveLength(1)
  })

  it('shows an error state when the request fails', async () => {
    stubFetch(null, false, 500)
    render(<InfraDashboard />)
    await waitFor(() =>
      expect(screen.getByText(/error:/i)).toBeInTheDocument(),
    )
  })
})
