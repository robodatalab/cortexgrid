import { describe, it, expect } from 'vitest'
import {
  deploymentApiUrl,
  deploymentId,
  modelId,
  serveApplicationName,
} from '../src/ids'

const unconfigured = {
  family: 'Qwen3',
  suffix: '8B',
  run_name: 'imported',
  config_fingerprint: '',
}
const withoutThinking = { ...unconfigured, config_fingerprint: '5f0c1d2e3a4b' }

describe('deployment ids', () => {
  it('names a deployment without config by its model', () => {
    expect(deploymentId(unconfigured)).toBe(modelId(unconfigured))
    expect(serveApplicationName(unconfigured)).toBe('Qwen3__8B__imported')
  })

  it('tells a deployment given a config apart by its config fingerprint', () => {
    expect(deploymentId(withoutThinking)).toBe('Qwen3/8B/imported/5f0c1d2e3a4b')
    expect(serveApplicationName(withoutThinking)).toBe(
      'Qwen3__8B__imported__5f0c1d2e3a4b',
    )
  })

  it('addresses a deployment given a config by its config fingerprint', () => {
    expect(deploymentApiUrl(unconfigured, 'devices')).toBe(
      '/api/deployments/Qwen3/8B/imported/devices',
    )
    expect(deploymentApiUrl(withoutThinking, 'devices')).toBe(
      '/api/deployments/Qwen3/8B/imported/devices?config_fingerprint=5f0c1d2e3a4b',
    )
  })
})
