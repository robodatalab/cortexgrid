import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, it, expect, vi } from 'vitest'
import { DeploymentDashboard } from '../src/components/DeploymentDashboard'
import type { Deployment } from '../src/components/ModelsTree'
import type { BundleUpdate } from '../src/bundleUpdate'

const DEPLOYED_FINGERPRINT = 'a'.repeat(64)
const REGISTERED_FINGERPRINT = 'b'.repeat(64)

const deployment: Deployment = {
  family: 'Qwen2',
  suffix: 'instruct',
  run_name: 'boogey-46',
  url: 'http://ray/r/Qwen2/instruct/boogey-46',
  phase: 'running',
  bundle_fingerprint: DEPLOYED_FINGERPRINT,
  replaced_bundle_fingerprint: '',
}

function renderCard(
  modelInRepository: boolean,
  handlers: Partial<{
    onStop: (d: Deployment) => void
    onNavigateToModel: (id: string) => void
    onRedeploy: (d: Deployment) => Promise<void>
  }> = {},
  d: Deployment = deployment,
  bundleUpdate: BundleUpdate | null = null,
) {
  render(
    <DeploymentDashboard
      deployment={d}
      modelInRepository={modelInRepository}
      bundleUpdate={bundleUpdate}
      onNavigateToModel={handlers.onNavigateToModel ?? vi.fn()}
      onStop={handlers.onStop ?? vi.fn()}
      onRedeploy={handlers.onRedeploy ?? vi.fn(() => Promise.resolve())}
    />,
  )
}

// The card fetches two endpoints; `bodies` maps a url fragment to what that
// one answers, so a test can stub the devices call without also standing in
// for the messages call.
function stubFetch(body: unknown, devices: unknown = []) {
  const fetch = vi.fn((url: string) =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(url.endsWith('/devices') ? devices : body),
    } as Response),
  )
  vi.stubGlobal('fetch', fetch)
  return fetch
}

function urlsFetched(fetch: ReturnType<typeof stubFetch>): string[] {
  return fetch.mock.calls.map(([url]) => url as string)
}

const runningOn = (node: string, healthy = true) => [
  {
    replica_id: 'r1',
    state: 'RUNNING',
    node_ip: '10.0.0.7',
    device: {
      name: 'ray-worker-abcde',
      namespace: 'cortexgrid',
      kind: 'pod',
      node: node,
      pod_ip: '10.0.0.7',
      state: 'Running',
      health: healthy ? 'ready' : 'CrashLoopBackOff',
      healthy,
      logs: null,
    },
  },
]

describe('DeploymentDashboard', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('shows the serving phase', () => {
    renderCard(true)
    expect(screen.getByText('Running')).toBeInTheDocument()
  })

  it('shows the short fingerprint of the code it runs', () => {
    renderCard(true)
    expect(screen.getByText('#aaaaaaa')).toBeInTheDocument()
  })

  it('shows no update notice while it runs the registry code', () => {
    renderCard(true)
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('shows which code it runs and which the registry holds when they differ', () => {
    renderCard(true, {}, deployment, {
      deployedFingerprint: DEPLOYED_FINGERPRINT,
      registeredFingerprint: REGISTERED_FINGERPRINT,
    })
    const notice = screen.getByRole('status')
    expect(notice).toHaveTextContent('Update available')
    expect(notice).toHaveTextContent('runs code #aaaaaaa, the registry holds #bbbbbbb')
  })

  it('shows which code it is moving from and to while redeploying', () => {
    renderCard(true, {}, {
      ...deployment,
      phase: 'deploying',
      replaced_bundle_fingerprint: REGISTERED_FINGERPRINT,
    })
    expect(screen.getByRole('status')).toHaveTextContent(
      'moving from code #bbbbbbb to #aaaaaaa',
    )
    expect(screen.getByText('#bbbbbbb → #aaaaaaa')).toBeInTheDocument()
  })

  it('redeploys the deployment from its update notice', async () => {
    const onRedeploy = vi.fn(() => Promise.resolve())
    renderCard(true, { onRedeploy }, deployment, {
      deployedFingerprint: DEPLOYED_FINGERPRINT,
      registeredFingerprint: REGISTERED_FINGERPRINT,
    })
    await userEvent.click(screen.getByRole('button', { name: 'Redeploy' }))
    expect(onRedeploy).toHaveBeenCalledWith(deployment)
  })

  it('offers no redeploy while the registry holds no signed code to run', () => {
    renderCard(true, {}, { ...deployment, bundle_fingerprint: '' }, {
      deployedFingerprint: '',
      registeredFingerprint: '',
    })
    expect(screen.queryByRole('button', { name: 'Redeploy' })).toBeNull()
  })

  it('warns that code saved before signing may be outdated', () => {
    renderCard(true, {}, { ...deployment, bundle_fingerprint: '' }, {
      deployedFingerprint: '',
      registeredFingerprint: '',
    })
    expect(screen.getByRole('status')).toHaveTextContent('May be outdated')
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

  it('does not fetch messages for a running deployment', async () => {
    const fetch = stubFetch([])
    renderCard(true)
    await screen.findByText('Devices')

    expect(urlsFetched(fetch)).not.toContain(
      '/api/deployments/Qwen2/instruct/boogey-46/messages',
    )
  })

  it('names the device a replica landed on', async () => {
    stubFetch([], runningOn('dgx-spark-01'))
    renderCard(true)

    expect(await screen.findByText('dgx-spark-01')).toBeInTheDocument()
    expect(screen.getByText('replica: RUNNING')).toBeInTheDocument()
  })

  it('shows the health of the device, not just its name', async () => {
    stubFetch([], runningOn('dgx-spark-01', false))
    renderCard(true)

    expect(await screen.findByLabelText('unhealthy')).toBeInTheDocument()
    expect(screen.getByText('health: CrashLoopBackOff')).toBeInTheDocument()
  })

  it('says so when no replica has been placed yet', async () => {
    stubFetch([], [])
    renderCard(true)

    expect(
      await screen.findByText('No replica is running yet.'),
    ).toBeInTheDocument()
  })

  it('still shows a replica whose host the cluster does not know', async () => {
    stubFetch([], [
      { replica_id: 'r1', state: 'STARTING', node_ip: '10.0.0.9', device: null },
    ])
    renderCard(true)

    expect(await screen.findByText('10.0.0.9')).toBeInTheDocument()
    expect(
      screen.getByText('host not found in the cluster'),
    ).toBeInTheDocument()
  })
})
