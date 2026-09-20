// The one place the UI decides how a job status looks.
//
// The backend names a job's state in a single word (cortexgrid_ui's
// job_status module decides which); everything that shows a job — the
// experiments tree, the jobs table — renders it from here, so the same
// job never appears in two colours or two orders depending on where you
// are looking at it from.

// What needs attention first, what is done last. Alphabetical would
// scatter running jobs among finished ones.
export const JOB_STATUSES = [
    "deleting",
    "running",
    "pending",
    "failed",
    "broken",
    "stopped",
    "finished",
];

export function jobStatusRank(status: string): number {
    const rank = JOB_STATUSES.indexOf(status);
    return rank === -1 ? JOB_STATUSES.length : rank;
}

/** The class pair a status token is rendered with. */
export function jobStatusClass(status: string): string {
    return `job-status job-status--${status}`;
}
