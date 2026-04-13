import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { InfraStatusIndicator } from '../src/components/InfraStatusIndicator'

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

describe('InfraStatusIndicator', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders green when overall is true', async () => {
    stubFetch({ overall: true, containers: [] })
    const { container } = render(<InfraStatusIndicator onClick={() => {}} />)
    await waitFor(() =>
      expect(container.querySelector('.infra-indicator--ok')).toBeTruthy(),
    )
  })

  it('renders red when overall is false', async () => {
    stubFetch({ overall: false, containers: [] })
    const { container } = render(<InfraStatusIndicator onClick={() => {}} />)
    await waitFor(() =>
      expect(container.querySelector('.infra-indicator--bad')).toBeTruthy(),
    )
  })

  it('renders red when the request fails', async () => {
    stubFetch(null, false, 500)
    const { container } = render(<InfraStatusIndicator onClick={() => {}} />)
    await waitFor(() =>
      expect(container.querySelector('.infra-indicator--bad')).toBeTruthy(),
    )
  })

  it('fires onClick when the circle is clicked', async () => {
    stubFetch({ overall: true, containers: [] })
    const onClick = vi.fn()
    render(<InfraStatusIndicator onClick={onClick} />)
    const btn = await screen.findByRole('button', { name: /status: ok/i })
    await userEvent.click(btn)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('sets the tooltip to "Status: OK" when healthy', async () => {
    stubFetch({ overall: true, containers: [] })
    render(<InfraStatusIndicator onClick={() => {}} />)
    const btn = await screen.findByRole('button', { name: /status: ok/i })
    expect(btn).toHaveAttribute('data-tooltip', 'Status: OK')
  })

  it('sets the tooltip to "Status: Error" when unhealthy', async () => {
    stubFetch({ overall: false, containers: [] })
    render(<InfraStatusIndicator onClick={() => {}} />)
    const btn = await screen.findByRole('button', { name: /status: error/i })
    expect(btn).toHaveAttribute('data-tooltip', 'Status: Error')
  })
})
