import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import App from '../src/App'

const sampleDashboards = [
  { id: 'mlflow', url: 'http://100.1.2.3:5000' },
  { id: 'ray', url: 'http://100.1.2.3:8265' },
  { id: 'grafana', url: 'http://100.1.2.3:3000' },
]

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(sampleDashboards),
        } as Response),
      ),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the primary nav rail with Experiments, Jobs, Models, and Secrets', () => {
    render(<App />)
    expect(
      screen.getByRole('button', { name: /experiments/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /jobs/i })).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /models/i }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /secrets/i }),
    ).toBeInTheDocument()
  })

  it('opens the jobs table from the nav rail', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: /jobs/i }))

    expect(screen.getByRole('heading', { name: 'Jobs' })).toBeInTheDocument()
  })

  it('renders a link per dashboard pointing at its url', async () => {
    render(<App />)

    for (const d of sampleDashboards) {
      const link = await waitFor(() =>
        screen.getByRole('link', { name: new RegExp(d.id, 'i') }),
      )
      expect(link).toHaveAttribute('href', d.url)
      expect(link).toHaveAttribute('target', '_blank')
    }
  })

  it('still renders the nav rail when dashboards API fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({ ok: false, status: 500 } as Response),
      ),
    )

    render(<App />)
    expect(
      screen.getByRole('button', { name: /experiments/i }),
    ).toBeInTheDocument()
  })
})
