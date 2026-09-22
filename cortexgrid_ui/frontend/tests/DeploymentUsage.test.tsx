import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { DeploymentUsageCharts } from '../src/components/DeploymentUsage'
import { formatBytes, formatPercent, formatRate } from '../src/usageFormat'
import type { DeploymentMetrics } from '../src/components/DeploymentUsage'

const noData: DeploymentMetrics = {
  requests_per_second: [],
  succeeded_responses_per_second: [],
  failed_responses_per_second: [],
  cpu_percent: [],
  memory_bytes: [],
  gpu_percent: [],
  gpu_memory_bytes: [],
}

describe('DeploymentUsageCharts', () => {
  it('says a measure is not reported when it has no samples', () => {
    render(<DeploymentUsageCharts metrics={noData} />)
    expect(screen.getAllByText('Not reported')).toHaveLength(6)
  })

  it('shows the latest value of each measure it has samples for', () => {
    render(
      <DeploymentUsageCharts
        metrics={{
          ...noData,
          cpu_percent: [
            [3600, 10],
            [3630, 42],
          ],
          succeeded_responses_per_second: [[3630, 1.5]],
          failed_responses_per_second: [[3630, 0.25]],
        }}
      />,
    )
    expect(screen.getByText('42%')).toBeInTheDocument()
    expect(screen.getByText('succeeded 1.50/s')).toBeInTheDocument()
    expect(screen.getByText('failed 0.25/s')).toBeInTheDocument()
    expect(screen.getAllByText('Not reported')).toHaveLength(4)
  })
})

describe('usage formatting', () => {
  it('formats rates, percentages and memory sizes', () => {
    expect(formatRate(0.0222)).toBe('0.02/s')
    expect(formatRate(12.34)).toBe('12.3/s')
    expect(formatPercent(99.6)).toBe('100%')
    expect(formatBytes(512 * 1024 * 1024)).toBe('512 MiB')
    expect(formatBytes(3 * 1024 * 1024 * 1024)).toBe('3.0 GiB')
  })
})
