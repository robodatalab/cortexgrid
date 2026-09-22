import { describe, it, expect } from 'vitest'
import { loadLevel } from '../src/deploymentLoad'

describe('loadLevel', () => {
  it('reads below 20% of the p99 as idle', () => {
    expect(loadLevel(0)).toBe('idle')
    expect(loadLevel(0.19)).toBe('idle')
  })

  it('reads 20% to 80% of the p99 as moderate', () => {
    expect(loadLevel(0.2)).toBe('moderate')
    expect(loadLevel(0.8)).toBe('moderate')
  })

  it('reads above 80% of the p99 as busy', () => {
    expect(loadLevel(0.81)).toBe('busy')
    expect(loadLevel(1.5)).toBe('busy')
  })
})
