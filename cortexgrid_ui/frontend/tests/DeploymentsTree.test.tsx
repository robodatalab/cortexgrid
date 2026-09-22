import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { DeploymentsTree } from '../src/components/DeploymentsTree'
import type { Deployment } from '../src/components/ModelsTree'

const deployments: Deployment[] = [
  {
    family: 'Qwen2',
    suffix: 'instruct',
    run_name: 'boogey-46',
    url: 'http://ray/r/Qwen2/instruct/boogey-46',
    phase: 'running',
    bundle_fingerprint: '',
  },
  {
    family: 'DeepSeek3',
    suffix: 'chat',
    run_name: 'snake-12',
    url: 'http://ray/r/DeepSeek3/chat/snake-12',
    phase: 'failed',
    bundle_fingerprint: '',
  },
]

describe('DeploymentsTree', () => {
  it('renders a row per deployment', () => {
    render(
      <DeploymentsTree
        deployments={deployments}
        loads={{}}
        bundleUpdates={{}}
        selection={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText('Qwen2/instruct')).toBeInTheDocument()
    expect(screen.getByText('boogey-46')).toBeInTheDocument()
    expect(screen.getByText('DeepSeek3/chat')).toBeInTheDocument()
    expect(screen.getByText('snake-12')).toBeInTheDocument()
  })

  it('shows an empty state when there are no deployments', () => {
    render(
      <DeploymentsTree
        deployments={[]}
        loads={{}}
        bundleUpdates={{}}
        bundleUpdates={{}}
        selection={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByText('No deployments')).toBeInTheDocument()
  })

  it('selects a deployment by its shared id when clicked', async () => {
    const onSelect = vi.fn()
    render(
      <DeploymentsTree
        deployments={deployments}
        loads={{}}
        bundleUpdates={{}}
        selection={null}
        onSelect={onSelect}
      />,
    )
    await userEvent.click(screen.getByText('Qwen2/instruct'))
    expect(onSelect).toHaveBeenCalledWith({
      kind: 'deployment',
      id: 'Qwen2/instruct/boogey-46',
    })
  })

  it('shows one bar per load level next to each deployment it has a load for', () => {
    render(
      <DeploymentsTree
        deployments={deployments}
        loads={{ 'Qwen2/instruct/boogey-46': 0.9, 'DeepSeek3/chat/snake-12': 0.1 }}
        bundleUpdates={{}}
        selection={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getByRole('img', { name: 'Load: busy' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Load: idle' })).toBeInTheDocument()
  })

  it('marks the deployments the registry holds newer code for', () => {
    render(
      <DeploymentsTree
        deployments={deployments}
        loads={{}}
        bundleUpdates={{
          'Qwen2/instruct/boogey-46': {
            deployedFingerprint: 'a'.repeat(64),
            registeredFingerprint: 'b'.repeat(64),
          },
        }}
        selection={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.getAllByText('update')).toHaveLength(1)
  })

  it('shows no load bars for a deployment without a load', () => {
    render(
      <DeploymentsTree
        deployments={deployments}
        loads={{}}
        bundleUpdates={{}}
        selection={null}
        onSelect={vi.fn()}
      />,
    )
    expect(screen.queryByRole('img', { name: /^Load:/ })).not.toBeInTheDocument()
  })
})
