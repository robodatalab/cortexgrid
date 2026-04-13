import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { InfraDashboard } from '../src/components/InfraDashboard'

const sample = {
  overall: false,
  containers: [
    { name: 'robolab-redis', state: 'running', health: 'healthy', healthy: true, logs: null },
    {
      name: 'robolab-ray-head',
      state: 'running',
      health: 'unhealthy',
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

  it('renders a card per container', async () => {
    stubFetch(sample)
    render(<InfraDashboard />)
    await waitFor(() => {
      expect(screen.getByText('robolab-redis')).toBeInTheDocument()
      expect(screen.getByText('robolab-ray-head')).toBeInTheDocument()
    })
  })

  it('shows logs only for unhealthy containers', async () => {
    stubFetch(sample)
    render(<InfraDashboard />)
    await waitFor(() =>
      expect(screen.getByText('robolab-ray-head')).toBeInTheDocument(),
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
