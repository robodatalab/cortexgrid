import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { ModelDashboard } from '../src/components/ModelDashboard'
import type {
  Deployment,
  Model,
  ModelConfig,
  ModelRequirements,
} from '../src/components/ModelsTree'

const NO_REQUIREMENTS: ModelRequirements = {
  num_gpus: 0,
  ram_gb: 0,
  vram_gb: 0,
}

function makeModel(
  phase: string,
  requirements: ModelRequirements = NO_REQUIREMENTS,
  config: ModelConfig = {},
): Model {
  return {
    id: 'Qwen2/instruct/boogey-46',
    family: 'Qwen2',
    suffix: 'instruct',
    run_name: 'boogey-46',
    created_at: '2026-05-21T00:00:00Z',
    data_blob_path: 's3://b/models/boogey-46/Qwen2/instruct/weights/',
    size_bytes: 100,
    phase,
    requirements,
    config,
    bundle_fingerprint: 'b'.repeat(64),
  }
}

const deployment: Deployment = {
  family: 'Qwen2',
  suffix: 'instruct',
  run_name: 'boogey-46',
  url: 'http://ray/r/Qwen2/instruct/boogey-46',
  phase: 'running',
  bundle_fingerprint: 'b'.repeat(64),
}

function renderCard(
  model: Model,
  deploy: Deployment | null,
  handlers: Partial<{
    onDeploy: (m: Model) => void
    onNavigateToDeployment: (id: string) => void
    onSaveRequirements: (
      m: Model,
      requirements: ModelRequirements,
    ) => Promise<void>
    onSaveConfig: (m: Model, config: ModelConfig) => Promise<void>
  }> = {},
) {
  render(
    <ModelDashboard
      model={model}
      deployment={deploy}
      onNavigateToRun={vi.fn()}
      onNavigateToDeployment={handlers.onNavigateToDeployment ?? vi.fn()}
      onDeploy={handlers.onDeploy ?? vi.fn()}
      onSaveRequirements={
        handlers.onSaveRequirements ?? vi.fn().mockResolvedValue(undefined)
      }
      onSaveConfig={
        handlers.onSaveConfig ?? vi.fn().mockResolvedValue(undefined)
      }
    />,
  )
}

function requirementField(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement
}

function configField(which: 'key' | 'value', row: number): HTMLInputElement {
  const label = which === 'key' ? 'Config key' : 'Config value'
  return screen.getByLabelText(`${label} ${row}`) as HTMLInputElement
}

function saveConfigButton(): HTMLButtonElement {
  return screen.getByRole('button', { name: 'Save config' }) as HTMLButtonElement
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

  it('shows the short fingerprint of the code the registry holds', () => {
    renderCard(makeModel('ready'), null)
    expect(screen.getByText('#bbbbbbb')).toBeInTheDocument()
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

  it('shows the requirements stored on the model', () => {
    renderCard(makeModel('ready', { num_gpus: 1, ram_gb: 16, vram_gb: 24 }), null)
    expect(requirementField('GPUs').value).toBe('1')
    expect(requirementField('RAM (GiB)').value).toBe('16')
    expect(requirementField('VRAM (GiB)').value).toBe('24')
  })

  it('saves edited requirements', async () => {
    const onSaveRequirements = vi.fn().mockResolvedValue(undefined)
    const model = makeModel('ready', { num_gpus: 1, ram_gb: 16, vram_gb: 24 })
    renderCard(model, null, { onSaveRequirements })

    await userEvent.clear(requirementField('RAM (GiB)'))
    await userEvent.type(requirementField('RAM (GiB)'), '32')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(onSaveRequirements).toHaveBeenCalledWith(model, {
      num_gpus: 1,
      ram_gb: 32,
      vram_gb: 24,
    })
  })

  it('accepts a fraction of a GPU so models can share a card', async () => {
    const onSaveRequirements = vi.fn().mockResolvedValue(undefined)
    const model = makeModel('ready', { num_gpus: 1, ram_gb: 16, vram_gb: 24 })
    renderCard(model, null, { onSaveRequirements })

    await userEvent.clear(requirementField('GPUs'))
    await userEvent.type(requirementField('GPUs'), '0.25')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.queryByRole('alert')).toBeNull()
    expect(onSaveRequirements).toHaveBeenCalledWith(model, {
      num_gpus: 0.25,
      ram_gb: 16,
      vram_gb: 24,
    })
  })

  it('keeps Save disabled until a requirement changes', async () => {
    renderCard(makeModel('ready', { num_gpus: 1, ram_gb: 16, vram_gb: 24 }), null)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()

    await userEvent.clear(requirementField('GPUs'))
    await userEvent.type(requirementField('GPUs'), '2')

    expect(screen.getByRole('button', { name: 'Save' })).not.toBeDisabled()
  })

  it('refuses VRAM without a GPU and explains why', async () => {
    const onSaveRequirements = vi.fn().mockResolvedValue(undefined)
    renderCard(makeModel('ready'), null, { onSaveRequirements })

    await userEvent.clear(requirementField('VRAM (GiB)'))
    await userEvent.type(requirementField('VRAM (GiB)'), '24')

    expect(screen.getByRole('alert')).toHaveTextContent(
      'VRAM needs a GPU: set GPUs above 0.',
    )
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
    expect(onSaveRequirements).not.toHaveBeenCalled()
  })

  it('reports a failed save', async () => {
    const onSaveRequirements = vi
      .fn()
      .mockRejectedValue(new Error('HTTP 404: No model'))
    renderCard(makeModel('ready'), null, { onSaveRequirements })

    await userEvent.clear(requirementField('RAM (GiB)'))
    await userEvent.type(requirementField('RAM (GiB)'), '8')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 404: No model')
  })

  it('shows the config stored on the model', () => {
    renderCard(
      makeModel('ready', NO_REQUIREMENTS, { model: 'claude-opus-5' }),
      null,
    )
    expect(configField('key', 1).value).toBe('model')
    expect(configField('value', 1).value).toBe('claude-opus-5')
  })

  it('saves an added config entry', async () => {
    const onSaveConfig = vi.fn().mockResolvedValue(undefined)
    const model = makeModel('ready', NO_REQUIREMENTS, {
      model: 'claude-opus-5',
    })
    renderCard(model, null, { onSaveConfig })

    await userEvent.click(screen.getByRole('button', { name: 'Add config' }))
    await userEvent.type(configField('key', 2), 'api_key_secret')
    await userEvent.type(configField('value', 2), 'anthropic-api-key')
    await userEvent.click(saveConfigButton())

    expect(onSaveConfig).toHaveBeenCalledWith(model, {
      model: 'claude-opus-5',
      api_key_secret: 'anthropic-api-key',
    })
  })

  it('saves the mapping without a removed entry', async () => {
    const onSaveConfig = vi.fn().mockResolvedValue(undefined)
    const model = makeModel('ready', NO_REQUIREMENTS, {
      model: 'claude-opus-5',
      region: 'eu',
    })
    renderCard(model, null, { onSaveConfig })

    await userEvent.click(
      screen.getByRole('button', { name: 'Remove config region' }),
    )
    await userEvent.click(saveConfigButton())

    expect(onSaveConfig).toHaveBeenCalledWith(model, {
      model: 'claude-opus-5',
    })
  })

  it('keeps Save disabled until the config changes', async () => {
    renderCard(
      makeModel('ready', NO_REQUIREMENTS, { model: 'claude-opus-5' }),
      null,
    )
    expect(saveConfigButton()).toBeDisabled()

    await userEvent.type(configField('value', 1), '-x')

    expect(saveConfigButton()).not.toBeDisabled()
  })

  it('refuses a blank config key and explains why', async () => {
    const onSaveConfig = vi.fn().mockResolvedValue(undefined)
    renderCard(makeModel('ready'), null, { onSaveConfig })

    await userEvent.click(screen.getByRole('button', { name: 'Add config' }))

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Config keys cannot be blank.',
    )
    expect(saveConfigButton()).toBeDisabled()
    expect(onSaveConfig).not.toHaveBeenCalled()
  })

  it('refuses two config entries with the same key', async () => {
    renderCard(
      makeModel('ready', NO_REQUIREMENTS, { model: 'claude-opus-5' }),
      null,
    )

    await userEvent.click(screen.getByRole('button', { name: 'Add config' }))
    await userEvent.type(configField('key', 2), 'model')

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Config keys must be unique.',
    )
    expect(saveConfigButton()).toBeDisabled()
  })

  it('reports a failed config save', async () => {
    const onSaveConfig = vi
      .fn()
      .mockRejectedValue(new Error('HTTP 404: No model'))
    renderCard(
      makeModel('ready', NO_REQUIREMENTS, { model: 'claude-opus-5' }),
      null,
      { onSaveConfig },
    )

    await userEvent.type(configField('value', 1), '-x')
    await userEvent.click(saveConfigButton())

    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 404: No model')
  })
})
