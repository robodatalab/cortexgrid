import { useMemo, useState } from "react";
import { Trash2 } from "lucide-react";
import "./JobsDashboard.css";

export type JobRow = {
    id: string;
    job_id: string;
    status: string;
    experiment_name: string | null;
    run_id: string | null;
    run_name: string | null;
    ray_job_id: string | null;
    abandoned: boolean;
};

type SortKey = "job" | "status" | "experiment" | "run";
type SortDir = "asc" | "desc";

// Status order for the Status column: what needs attention first, what is
// done last. Alphabetical would scatter running jobs among finished ones.
// `broken` is what the backend reports when Ray cannot say, the same word
// the experiments tree uses for it.
const STATUS_ORDER = [
    "deleting",
    "running",
    "pending",
    "failed",
    "broken",
    "stopped",
    "finished",
];

function statusRank(status: string): number {
    const rank = STATUS_ORDER.indexOf(status);
    return rank === -1 ? STATUS_ORDER.length : rank;
}

function experimentOf(row: JobRow): string {
    return row.experiment_name ?? "";
}

function runOf(row: JobRow): string {
    return row.run_name ?? row.run_id ?? "";
}

function compare(a: JobRow, b: JobRow, key: SortKey): number {
    switch (key) {
        case "job":
            return a.job_id.localeCompare(b.job_id);
        case "status":
            return statusRank(a.status) - statusRank(b.status);
        case "experiment":
            return experimentOf(a).localeCompare(experimentOf(b));
        case "run":
            return runOf(a).localeCompare(runOf(b));
    }
}

const COLUMNS: { key: SortKey; label: string }[] = [
    { key: "job", label: "Job" },
    { key: "status", label: "Status" },
    { key: "experiment", label: "Experiment" },
    { key: "run", label: "Run" },
];

type Props = {
    jobs: JobRow[];
    onOpenJob: (row: JobRow) => void;
    onDeleteJob: (row: JobRow) => void;
};

export function JobsDashboard({ jobs: rows, onOpenJob, onDeleteJob }: Props) {
    const [sortKey, setSortKey] = useState<SortKey>("status");
    const [sortDir, setSortDir] = useState<SortDir>("asc");
    const [hiddenStatuses, setHiddenStatuses] = useState<string[]>([]);

    const countsByStatus = useMemo(() => {
        const counts = new Map<string, number>();
        for (const row of rows) {
            counts.set(row.status, (counts.get(row.status) ?? 0) + 1);
        }
        return counts;
    }, [rows]);

    // A hidden status keeps its chip even once its last job is gone:
    // dropping the chip would leave the filter switched off with no way
    // to switch it back on when such a job next appears.
    const statuses = useMemo(
        () =>
            Array.from(
                new Set([
                    ...STATUS_ORDER,
                    ...countsByStatus.keys(),
                    ...hiddenStatuses,
                ]),
            ).sort((a, b) => statusRank(a) - statusRank(b)),
        [countsByStatus, hiddenStatuses],
    );

    const visibleRows = useMemo(() => {
        const kept = rows.filter((r) => !hiddenStatuses.includes(r.status));
        const direction = sortDir === "asc" ? 1 : -1;
        // job_id breaks every tie, so the order never wobbles between polls.
        return kept.sort(
            (a, b) =>
                direction * compare(a, b, sortKey) ||
                a.job_id.localeCompare(b.job_id),
        );
    }, [rows, hiddenStatuses, sortKey, sortDir]);

    function toggleSort(key: SortKey) {
        if (key === sortKey) {
            setSortDir(sortDir === "asc" ? "desc" : "asc");
            return;
        }
        setSortKey(key);
        setSortDir("asc");
    }

    function toggleStatus(status: string) {
        setHiddenStatuses((prev) =>
            prev.includes(status)
                ? prev.filter((s) => s !== status)
                : [...prev, status],
        );
    }

    return (
        <div className="jobs-dashboard">
            <div className="jobs-dashboard__header">
                <h1>Jobs</h1>
                <div className="jobs-dashboard__count">
                    {visibleRows.length} of {rows.length}
                </div>
            </div>

            <div className="jobs-dashboard__filters">
                {statuses.map((status) => {
                    const hidden = hiddenStatuses.includes(status);
                    return (
                        <button
                            key={status}
                            type="button"
                            className={`jobs-dashboard__filter${hidden ? " jobs-dashboard__filter--off" : ""}`}
                            onClick={() => toggleStatus(status)}
                            aria-pressed={!hidden}
                        >
                            <span
                                className={`jobs-dashboard__status jobs-dashboard__status--${status}`}
                            >
                                {status}
                            </span>
                            <span className="jobs-dashboard__filter-count">
                                {countsByStatus.get(status) ?? 0}
                            </span>
                        </button>
                    );
                })}
            </div>

            <div className="jobs-dashboard__table-wrap">
                <table className="jobs-dashboard__table">
                    <thead>
                        <tr>
                            {COLUMNS.map((column) => (
                                <th key={column.key}>
                                    <button
                                        type="button"
                                        className="jobs-dashboard__sort"
                                        onClick={() => toggleSort(column.key)}
                                        aria-label={`Sort by ${column.label}`}
                                    >
                                        {column.label}
                                        <span className="jobs-dashboard__sort-arrow">
                                            {sortKey === column.key
                                                ? sortDir === "asc"
                                                    ? "▲"
                                                    : "▼"
                                                : ""}
                                        </span>
                                    </button>
                                </th>
                            ))}
                            <th />
                        </tr>
                    </thead>
                    <tbody>
                        {visibleRows.map((row) => {
                            // Deletion is a status the backend reports, not
                            // something this table remembers: the row keeps
                            // saying `deleting` across a reload, and stops
                            // when the control plane has removed the job.
                            const deleting = row.status === "deleting";
                            return (
                                <tr
                                    key={row.id}
                                    className={`jobs-dashboard__row${row.abandoned ? " jobs-dashboard__row--abandoned" : ""}${deleting ? " jobs-dashboard__row--deleting" : ""}`}
                                    onClick={
                                        row.abandoned
                                            ? undefined
                                            : () => onOpenJob(row)
                                    }
                                >
                                    <td className="jobs-dashboard__job">
                                        {row.job_id}
                                    </td>
                                    <td>
                                        <span
                                            className={`jobs-dashboard__status jobs-dashboard__status--${row.status}`}
                                        >
                                            {row.status}
                                        </span>
                                    </td>
                                    <td
                                        title={
                                            row.abandoned
                                                ? "No experiment owns this job any more"
                                                : undefined
                                        }
                                    >
                                        {row.experiment_name ?? "—"}
                                    </td>
                                    <td title={row.run_id ?? undefined}>
                                        {runOf(row) || "—"}
                                    </td>
                                    <td className="jobs-dashboard__actions">
                                        <button
                                            type="button"
                                            className="jobs-dashboard__delete"
                                            aria-label={`Delete job ${row.job_id}`}
                                            // An abandoned job has no record
                                            // to write the request on; the
                                            // control plane clears those.
                                            disabled={deleting || row.abandoned}
                                            title={
                                                row.abandoned
                                                    ? "Nothing owns this job; the control plane clears it"
                                                    : undefined
                                            }
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                onDeleteJob(row);
                                            }}
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
                {visibleRows.length === 0 && (
                    <div className="jobs-dashboard__empty">
                        {rows.length === 0
                            ? "No jobs"
                            : "Every job is filtered out"}
                    </div>
                )}
            </div>
        </div>
    );
}
