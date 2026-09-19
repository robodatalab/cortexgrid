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
    ray_job_id: `${run_id}-${over.job_id}-0`,
    abandoned: false,
    ...over,
  }
}

const abandoned = job({
  job_id: 'lost-fox-77',
  status: 'failed',
  experiment_name: null,
  run_name: null,
  abandoned: true,
})

function renderTable(
  jobs: JobRow[],
  handlers: Partial<{
    onOpenJob: (row: JobRow) => void
    onDeleteJob: (row: JobRow) => void
  }> = {},
  deletingIds: string[] = [],
) {
  render(
    <JobsDashboard
      jobs={jobs}
      deletingIds={deletingIds}
      onOpenJob={handlers.onOpenJob ?? vi.fn()}
      onDeleteJob={handlers.onDeleteJob ?? vi.fn()}
    />,
  )
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
    renderTable([row], { onOpenJob })

    await user.click(screen.getByText('brave-ox-12'))

    expect(onOpenJob).toHaveBeenCalledWith(row)
  })

  it('does not open an abandoned job: there is no run to show it in', async () => {
    const user = userEvent.setup()
    const onOpenJob = vi.fn()
    renderTable([abandoned], { onOpenJob })

    await user.click(screen.getByText('lost-fox-77'))

    expect(onOpenJob).not.toHaveBeenCalled()
  })

  it('leaves the owner columns of an abandoned job empty', () => {
    renderTable([abandoned])

    const cells = within(screen.getAllByRole('row')[1]).getAllByRole('cell')
    expect(cells[2]).toHaveTextContent('—')
    expect(cells[3]).toHaveTextContent('run-1')
  })

  it('asks to delete the job of the row whose delete button was pressed', async () => {
    const user = userEvent.setup()
    const onDeleteJob = vi.fn()
    const keep = job({ job_id: 'keep-me' })
    const drop = job({ job_id: 'drop-me' })
    renderTable([keep, drop], { onDeleteJob })

    await user.click(screen.getByRole('button', { name: /delete job drop-me/i }))

    expect(onDeleteJob).toHaveBeenCalledWith(drop)
  })

  it('does not open a job when its delete button is pressed', async () => {
    const user = userEvent.setup()
    const onOpenJob = vi.fn()
    renderTable([job({ job_id: 'drop-me' })], { onOpenJob })

    await user.click(screen.getByRole('button', { name: /delete job drop-me/i }))

    expect(onOpenJob).not.toHaveBeenCalled()
  })

  it('disables the delete button of a job that is already being deleted', () => {
    const row = job({ job_id: 'going-away' })
    renderTable([row], {}, [row.id])

    expect(
      screen.getByRole('button', { name: /delete job going-away/i }),
    ).toBeDisabled()
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
