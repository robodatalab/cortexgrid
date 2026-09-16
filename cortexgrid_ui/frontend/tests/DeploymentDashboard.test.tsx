import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, it, expect, vi } from 'vitest'
import { DeploymentDashboard } from '../src/components/DeploymentDashboard'
import type { Deployment } from '../src/components/ModelsTree'

const deployment: Deployment = {
  family: 'Qwen2',
  suffix: 'instruct',
  run_name: 'boogey-46',
  url: 'http://ray/r/Qwen2/instruct/boogey-46',
  phase: 'running',
}

function renderCard(
  modelInRepository: boolean,
  handlers: Partial<{
    onStop: (d: Deployment) => void
    onNavigateToModel: (id: string) => void
  }> = {},
  d: Deployment = deployment,
) {
  render(
    <DeploymentDashboard
      deployment={d}
      modelInRepository={modelInRepository}
      onNavigateToModel={handlers.onNavigateToModel ?? vi.fn()}
      onStop={handlers.onStop ?? vi.fn()}
    />,
  )
}

function stubFetch(body: unknown) {
  const fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(body),
    } as Response),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('DeploymentDashboard', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the serving phase', () => {
    renderCard(true)
    expect(screen.getByText('Running')).toBeInTheDocument()
  })

  it('invokes onStop with the deployment when Stop is clicked', async () => {
    const onStop = vi.fn()
    renderCard(true, { onStop })
    await userEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledWith(deployment)
  })

  it('links back to the model by the shared id when it is in the repository', async () => {
    const onNavigateToModel = vi.fn()
    renderCard(true, { onNavigateToModel })
    await userEvent.click(
      screen.getByRole('button', { name: 'View in repository' }),
    )
    expect(onNavigateToModel).toHaveBeenCalledWith('Qwen2/instruct/boogey-46')
  })

  it('shows no back-link when the model is not in the repository', () => {
    renderCard(false)
    expect(screen.getByText('Not in repository')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'View in repository' }),
    ).toBeNull()
  })

  it('shows the Ray messages of a failed deployment', async () => {
    const fetch = stubFetch([
      { source: 'application', status: 'DEPLOY_FAILED', message: 'Traceback: boom' },
      { source: 'Model', status: 'UNHEALTHY', message: 'replica crashed' },
    ])
    renderCard(true, {}, { ...deployment, phase: 'failed' })

    expect(await screen.findByText('Traceback: boom')).toBeInTheDocument()
    expect(screen.getByText('application · DEPLOY_FAILED')).toBeInTheDocument()
    expect(screen.getByText('replica crashed')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith(
      '/api/deployments/Qwen2/instruct/boogey-46/messages',
      expect.anything(),
    )
  })

  it('does not fetch messages for a running deployment', () => {
    const fetch = stubFetch([])
    renderCard(true)
    expect(fetch).not.toHaveBeenCalled()
  })
})
