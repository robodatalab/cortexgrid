import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import App from '../src/App'

const sampleDashboards = [
  { id: 'mlflow', url: 'http://100.1.2.3:5000' },
  { id: 'ray', url: 'http://100.1.2.3:8265' },
  { id: 'minio', url: 'http://100.1.2.3:9001' },
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

  it('renders the CortexFlow landing header', () => {
    render(<App />)
    expect(
      screen.getByRole('heading', { level: 1, name: /cortexflow/i }),
    ).toBeInTheDocument()
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

  it('shows an error state when the API fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() =>
        Promise.resolve({ ok: false, status: 500 } as Response),
      ),
    )

    render(<App />)
    await waitFor(() =>
      expect(screen.getByText(/failed to load dashboards/i)).toBeInTheDocument(),
    )
  })
})
