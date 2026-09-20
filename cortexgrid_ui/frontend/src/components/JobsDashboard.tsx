import { useMemo, useState } from "react";
import "./JobsDashboard.css";

export type JobRow = {
    id: string;
    job_id: string;
    status: string;
    experiment_name: string;
    run_id: string;
    run_name: string;
};

type SortKey = "job" | "status" | "experiment" | "run";
type SortDir = "asc" | "desc";

// Status order for the Status column: what needs attention first, what is
// done last. Alphabetical would scatter running jobs among finished ones.
// `broken` is what the backend reports when Ray cannot say, the same word
// the experiments tree uses for it.
const STATUS_ORDER = [
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

function compare(a: JobRow, b: JobRow, key: SortKey): number {
    switch (key) {
        case "job":
            return a.job_id.localeCompare(b.job_id);
        case "status":
            return statusRank(a.status) - statusRank(b.status);
        case "experiment":
            return a.experiment_name.localeCompare(b.experiment_name);
        case "run":
            return a.run_name.localeCompare(b.run_name);
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
};

export function JobsDashboard({ jobs: rows, onOpenJob }: Props) {
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
                        </tr>
                    </thead>
                    <tbody>
                        {visibleRows.map((row) => (
                            <tr
                                key={row.id}
                                className="jobs-dashboard__row"
                                onClick={() => onOpenJob(row)}
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
                                <td>{row.experiment_name}</td>
                                <td title={row.run_id}>{row.run_name}</td>
                            </tr>
                        ))}
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
