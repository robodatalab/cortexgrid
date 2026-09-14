import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ModelDashboard } from '../src/components/ModelDashboard'
import type { Deployment, Model } from '../src/components/ModelsTree'

function makeModel(phase: string): Model {
  return {
    id: 'Qwen2/instruct/boogey-46',
    family: 'Qwen2',
    suffix: 'instruct',
    run_name: 'boogey-46',
    created_at: '2026-05-21T00:00:00Z',
    data_blob_path: 's3://b/models/boogey-46/Qwen2/instruct/weights/',
    size_bytes: 100,
    phase,
  }
}

const deployment: Deployment = {
  family: 'Qwen2',
  suffix: 'instruct',
  run_name: 'boogey-46',
  url: 'http://ray/r/Qwen2/instruct/boogey-46',
  phase: 'running',
}

function renderCard(
  model: Model,
  deploy: Deployment | null,
  handlers: Partial<{
    onDeploy: (m: Model) => void
    onNavigateToDeployment: (id: string) => void
  }> = {},
) {
  render(
    <ModelDashboard
      model={model}
      deployment={deploy}
      onNavigateToRun={vi.fn()}
      onNavigateToDeployment={handlers.onNavigateToDeployment ?? vi.fn()}
      onDeploy={handlers.onDeploy ?? vi.fn()}
    />,
  )
}

describe('ModelDashboard', () => {
  it('enables Deploy for a ready, undeployed model', () => {
    renderCard(makeModel('ready'), null)
    expect(screen.getByRole('button', { name: 'Deploy' })).not.toBeDisabled()
  })

  it('disables Deploy while the model is still uploading', () => {
    renderCard(makeModel('uploading'), null)
    expect(screen.getByRole('button', { name: 'Deploy' })).toBeDisabled()
  })

  it('disables Deploy when the model is already deployed', () => {
    renderCard(makeModel('ready'), deployment)
    expect(screen.getByRole('button', { name: 'Deploy' })).toBeDisabled()
  })

  it('shows the registry phase', () => {
    renderCard(makeModel('uploading'), null)
    expect(screen.getByText('Uploading')).toBeInTheDocument()
  })

  it('shows "Not deployed" and no deployment link when undeployed', () => {
    renderCard(makeModel('ready'), null)
    expect(screen.getByText('Not deployed')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'View deployment' }),
    ).toBeNull()
  })

  it('links to the deployment by the shared id when deployed', async () => {
    const onNavigateToDeployment = vi.fn()
    renderCard(makeModel('ready'), deployment, { onNavigateToDeployment })
    await userEvent.click(
      screen.getByRole('button', { name: 'View deployment' }),
    )
    expect(onNavigateToDeployment).toHaveBeenCalledWith('Qwen2/instruct/boogey-46')
  })

  it('invokes onDeploy when Deploy is clicked', async () => {
    const onDeploy = vi.fn()
    const model = makeModel('ready')
    renderCard(model, null, { onDeploy })
    await userEvent.click(screen.getByRole('button', { name: 'Deploy' }))
    expect(onDeploy).toHaveBeenCalledWith(model)
  })
})
