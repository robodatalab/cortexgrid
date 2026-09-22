import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'
import { JobsDashboard } from '../src/components/JobsDashboard'
import type { JobRow } from '../src/components/JobsDashboard'

function job(over: Partial<JobRow> & { job_id: string }): JobRow {
  const run_id = over.run_id ?? 'run-1'
  return {
    id: `${run_id}/${over.job_id}`,
    status: 'running',
    experiment_name: 'alpha',
    run_id,
    run_name: 'alpha-run',
    started_at: null,
    ended_at: null,
    ...over,
  }
}

function renderTable(jobs: JobRow[], onOpenJob = vi.fn()) {
  render(<JobsDashboard jobs={jobs} onOpenJob={onOpenJob} />)
}

// The order of the job column, top to bottom — what sorting is about.
function jobColumn(): string[] {
  return screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[0].textContent ?? '')
}

describe('JobsDashboard', () => {
  it('shows a row per job with its status, experiment and run', () => {
    renderTable([job({ job_id: 'brave-ox-12', status: 'finished' })])

    const row = screen.getAllByRole('row')[1]
    expect(within(row).getByText('brave-ox-12')).toBeInTheDocument()
    expect(within(row).getByText('finished')).toBeInTheDocument()
    expect(within(row).getByText('alpha')).toBeInTheDocument()
    expect(within(row).getByText('alpha-run')).toBeInTheDocument()
  })

  it('sorts live jobs above finished ones by default', () => {
    renderTable([
      job({ job_id: 'a-done', status: 'finished' }),
      job({ job_id: 'b-live', status: 'running' }),
    ])

    expect(jobColumn()).toEqual(['b-live', 'a-done'])
  })

  it('sorts by the column that was clicked, and reverses on a second click', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'zeta', status: 'running' }),
      job({ job_id: 'alpha', status: 'finished' }),
    ])

    await user.click(screen.getByRole('button', { name: /sort by job/i }))
    expect(jobColumn()).toEqual(['alpha', 'zeta'])

    await user.click(screen.getByRole('button', { name: /sort by job/i }))
    expect(jobColumn()).toEqual(['zeta', 'alpha'])
  })

  it('sorts by experiment when that column is clicked', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'from-zulu', experiment_name: 'zulu' }),
      job({ job_id: 'from-alpha', experiment_name: 'alpha' }),
    ])

    await user.click(screen.getByRole('button', { name: /sort by experiment/i }))

    expect(jobColumn()).toEqual(['from-alpha', 'from-zulu'])
  })

  it('sorts by run when that column is clicked', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'from-zulu', run_id: 'run-9', run_name: 'zulu-run' }),
      job({ job_id: 'from-alpha', run_id: 'run-2', run_name: 'alpha-run' }),
    ])

    await user.click(screen.getByRole('button', { name: /sort by run/i }))

    expect(jobColumn()).toEqual(['from-alpha', 'from-zulu'])
  })

  it('shows a dash for a job that has not ended yet', () => {
    renderTable([job({ job_id: 'still-going', started_at: '2026-09-22T10:00:00+00:00' })])

    const cells = within(screen.getAllByRole('row')[1]).getAllByRole('cell')
    expect(cells[4].textContent).not.toBe('-')
    expect(cells[5].textContent).toBe('-')
  })

  it('sorts by start time, a job that never started last', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'never', started_at: null }),
      job({ job_id: 'later', started_at: '2026-09-22T11:00:00+00:00' }),
      job({ job_id: 'earlier', started_at: '2026-09-22T10:00:00+00:00' }),
    ])

    await user.click(screen.getByRole('button', { name: /sort by started/i }))

    expect(jobColumn()).toEqual(['earlier', 'later', 'never'])
  })

  it('sorts by end time, a job still going last', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'going', ended_at: null }),
      job({ job_id: 'later', ended_at: '2026-09-22T11:00:00+00:00' }),
      job({ job_id: 'earlier', ended_at: '2026-09-22T10:00:00+00:00' }),
    ])

    await user.click(screen.getByRole('button', { name: /sort by ended/i }))

    expect(jobColumn()).toEqual(['earlier', 'later', 'going'])
  })

  it('hides the jobs of a status whose filter is switched off', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'still-going', status: 'running' }),
      job({ job_id: 'all-done', status: 'finished' }),
    ])

    await user.click(screen.getByRole('button', { name: /finished/i }))

    expect(jobColumn()).toEqual(['still-going'])
  })

  it('brings a filtered-out status back when its filter is switched on again', async () => {
    const user = userEvent.setup()
    renderTable([job({ job_id: 'all-done', status: 'finished' })])

    await user.click(screen.getByRole('button', { name: /finished/i }))
    expect(jobColumn()).toEqual([])

    await user.click(screen.getByRole('button', { name: /finished/i }))
    expect(jobColumn()).toEqual(['all-done'])
  })

  it('counts the jobs of every status regardless of the filter', async () => {
    const user = userEvent.setup()
    renderTable([
      job({ job_id: 'one', status: 'failed' }),
      job({ job_id: 'two', status: 'failed' }),
    ])

    await user.click(screen.getByRole('button', { name: /failed/i }))

    expect(
      within(screen.getByRole('button', { name: /failed/i })).getByText('2'),
    ).toBeInTheDocument()
  })

  it('opens the job that was clicked', async () => {
    const user = userEvent.setup()
    const onOpenJob = vi.fn()
    const row = job({ job_id: 'brave-ox-12' })
    renderTable([row], onOpenJob)

    await user.click(screen.getByText('brave-ox-12'))

    expect(onOpenJob).toHaveBeenCalledWith(row)
  })

  it('shows and can filter a job Ray could not speak for', async () => {
    const user = userEvent.setup()
    renderTable([job({ job_id: 'no-answer', status: 'broken' })])

    expect(screen.getAllByText('broken').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: /broken/i }))

    expect(jobColumn()).toEqual([])
  })

  it('says so when there are no jobs at all', () => {
    renderTable([])
    expect(screen.getByText('No jobs')).toBeInTheDocument()
  })

  it('says so when the filters hide every job', async () => {
    const user = userEvent.setup()
    renderTable([job({ job_id: 'all-done', status: 'finished' })])

    await user.click(screen.getByRole('button', { name: /finished/i }))

    expect(screen.getByText('Every job is filtered out')).toBeInTheDocument()
  })
})
