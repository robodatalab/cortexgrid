import { describe, it, expect } from 'vitest'
import { pendingBundleUpdate, shortFingerprint } from '../src/bundleUpdate'
import type { Deployment, Model } from '../src/components/ModelsTree'

const DEPLOYED_FINGERPRINT = 'a'.repeat(64)
const REGISTERED_FINGERPRINT = 'b'.repeat(64)

function deploymentRunning(bundleFingerprint: string): Deployment {
  return {
    family: 'Qwen2',
    suffix: 'instruct',
    run_name: 'boogey-46',
    url: 'http://ray/r/Qwen2/instruct/boogey-46',
    phase: 'running',
    bundle_fingerprint: bundleFingerprint,
    replaced_bundle_fingerprint: '',
  }
}

function modelHolding(bundleFingerprint: string): Model {
  return {
    id: 'Qwen2/instruct/boogey-46',
    family: 'Qwen2',
    suffix: 'instruct',
    run_name: 'boogey-46',
    created_at: '2026-05-21T00:00:00Z',
    data_blob_path: 's3://b/models/boogey-46/Qwen2/instruct/weights/',
    size_bytes: 100,
    phase: 'ready',
    requirements: { num_gpus: 0, ram_gb: 0, vram_gb: 0 },
    config: {},
    bundle_fingerprint: bundleFingerprint,
  }
}

describe('pendingBundleUpdate', () => {
  it('is none while the deployment runs the code the registry holds', () => {
    expect(
      pendingBundleUpdate(
        deploymentRunning(DEPLOYED_FINGERPRINT),
        modelHolding(DEPLOYED_FINGERPRINT),
      ),
    ).toBeNull()
  })

  it('names both codes once the registry holds different code', () => {
    expect(
      pendingBundleUpdate(
        deploymentRunning(DEPLOYED_FINGERPRINT),
        modelHolding(REGISTERED_FINGERPRINT),
      ),
    ).toEqual({
      deployedFingerprint: DEPLOYED_FINGERPRINT,
      registeredFingerprint: REGISTERED_FINGERPRINT,
    })
  })

  it('is pending for code deployed before signing, even if the registry is unsigned too', () => {
    expect(pendingBundleUpdate(deploymentRunning(''), modelHolding(''))).toEqual({
      deployedFingerprint: '',
      registeredFingerprint: '',
    })
  })

  it('is none for a deployment whose model left the registry', () => {
    expect(pendingBundleUpdate(deploymentRunning(''), undefined)).toBeNull()
  })
})

describe('shortFingerprint', () => {
  it('shortens a fingerprint the way git shortens a commit hash', () => {
    expect(shortFingerprint(DEPLOYED_FINGERPRINT)).toBe('#aaaaaaa')
  })

  it('calls code saved before signing unsigned', () => {
    expect(shortFingerprint('')).toBe('unsigned')
  })
})
