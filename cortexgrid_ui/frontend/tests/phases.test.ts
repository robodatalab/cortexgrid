import { describe, it, expect } from 'vitest'
import {
  servingTier,
  servingLabel,
  registryTier,
  registryLabel,
  worstTier,
} from '../src/phases'

describe('servingTier', () => {
  it('maps running to ok', () => expect(servingTier('running')).toBe('ok'))
  it('maps failed to error', () => expect(servingTier('failed')).toBe('error'))
  it('maps unhealthy to error', () =>
    expect(servingTier('unhealthy')).toBe('error'))
  it('maps deploying to warn', () =>
    expect(servingTier('deploying')).toBe('warn'))
  it('maps not_deployed to warn', () =>
    expect(servingTier('not_deployed')).toBe('warn'))
})

describe('registryTier', () => {
  it('maps ready to ok', () => expect(registryTier('ready')).toBe('ok'))
  it('maps broken to error', () => expect(registryTier('broken')).toBe('error'))
  it('maps upload_failed to error', () =>
    expect(registryTier('upload_failed')).toBe('error'))
  it('maps uploading to warn', () =>
    expect(registryTier('uploading')).toBe('warn'))
})

describe('labels', () => {
  it('servingLabel maps failed to "Deploy failed"', () =>
    expect(servingLabel('failed')).toBe('Deploy failed'))
  it('registryLabel maps broken to "Broken"', () =>
    expect(registryLabel('broken')).toBe('Broken'))
  it('registryLabel maps upload_failed to "Upload failed"', () =>
    expect(registryLabel('upload_failed')).toBe('Upload failed'))
})

describe('worstTier', () => {
  it('returns error when any tier is error', () =>
    expect(worstTier(['ok', 'warn', 'error'])).toBe('error'))
  it('returns warn over ok', () => expect(worstTier(['ok', 'warn'])).toBe('warn'))
  it('returns null when empty', () => expect(worstTier([])).toBeNull())
})
