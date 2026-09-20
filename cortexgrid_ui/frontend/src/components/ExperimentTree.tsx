import { Fragment } from "react";
import { FlaskConical, Play, Cog, Trash2 } from "lucide-react";
import { jobStatusClass } from "../jobStatus";
import "../jobStatus.css";
import "./ExperimentTree.css";

export type ExperimentRun = {
    experiment_name: string;
    run_id: string;
    run_name: string;
    jobs: { job_id: string; status: string }[];
    started_at_ms: number | null;
    ended_at_ms: number | null;
};

export type Selection =
    | { kind: "experiment"; experiment_name: string }
    | { kind: "run"; experiment_name: string; run_id: string; run_name: string }
    | { kind: "job"; experiment_name: string; run_id: string; job_id: string };

type ExperimentTreeProps = {
    experimentNames: string[];
    runsByExperiment: Record<string, ExperimentRun[]>;
    selection: Selection | null;
    onSelect: (selection: Selection) => void;
    onDeleteExperiment: (experimentName: string) => void;
    onDeleteRun: (runId: string, runName: string) => void;
};

function rowClass(isAncestor: boolean, isLeaf: boolean): string {
    const parts = ["experiment-tree__row"];
    if (isLeaf) parts.push("experiment-tree__row--leaf");
    else if (isAncestor) parts.push("experiment-tree__row--ancestor");
    return parts.join(" ");
}

export function ExperimentTree({
    experimentNames,
    runsByExperiment,
    selection,
    onSelect,
    onDeleteExperiment,
    onDeleteRun,
}: ExperimentTreeProps) {
    const selectedExperimentName =
        selection && "experiment_name" in selection
            ? selection.experiment_name
            : null;
    const selectedRunId =
        selection && (selection.kind === "run" || selection.kind === "job")
            ? selection.run_id
            : null;
    const selectedJobId = selection?.kind === "job" ? selection.job_id : null;

    return (
        <div className="experiment-tree">
            <div className="experiment-tree__title">
                <span>Experiments</span>
            </div>
            <div className="experiment-tree__list">
                {experimentNames.length === 0 && (
                    <div className="experiment-tree__status">
                        No experiments
                    </div>
                )}
                {experimentNames.map((name) => {
                    const isExpSel = selectedExperimentName === name;
                    const isExpLeaf =
                        isExpSel && selection?.kind === "experiment";
                    const runs = runsByExperiment[name] ?? [];
                    return (
                        <Fragment key={name}>
                            <div
                                className={rowClass(isExpSel, isExpLeaf)}
                                onClick={() =>
                                    onSelect({
                                        kind: "experiment",
                                        experiment_name: name,
                                    })
                                }
                            >
                                <FlaskConical
                                    size={16}
                                    className="experiment-tree__icon"
                                />
                                <span className="experiment-tree__label">
                                    {name}
                                </span>
                                <button
                                    type="button"
                                    className="experiment-tree__delete"
                                    aria-label={`Delete experiment ${name}`}
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onDeleteExperiment(name);
                                    }}
                                >
                                    <Trash2 size={14} />
                                </button>
                            </div>
                            {isExpSel && (
                                <div className="experiment-tree__drawer">
                                    {runs.length === 0 && (
                                        <div className="experiment-tree__status">
                                            No runs
                                        </div>
                                    )}
                                    {runs.map((run) => {
                                        const isRunSel =
                                            selectedRunId === run.run_id;
                                        const isRunLeaf =
                                            isRunSel &&
                                            selection?.kind === "run";
                                        const runJobs = [...run.jobs].sort(
                                            (a, b) =>
                                                a.job_id.localeCompare(
                                                    b.job_id,
                                                ),
                                        );
                                        return (
                                            <Fragment key={run.run_id}>
                                                <div
                                                    className={rowClass(
                                                        isRunSel,
                                                        isRunLeaf,
                                                    )}
                                                    onClick={() =>
                                                        onSelect({
                                                            kind: "run",
                                                            experiment_name:
                                                                name,
                                                            run_id: run.run_id,
                                                            run_name:
                                                                run.run_name,
                                                        })
                                                    }
                                                >
                                                    <Play
                                                        size={16}
                                                        className="experiment-tree__icon"
                                                    />
                                                    <span className="experiment-tree__label">
                                                        {run.run_name}
                                                    </span>
                                                    <button
                                                        type="button"
                                                        className="experiment-tree__delete"
                                                        aria-label={`Delete run ${run.run_name}`}
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            onDeleteRun(
                                                                run.run_id,
                                                                run.run_name,
                                                            );
                                                        }}
                                                    >
                                                        <Trash2 size={14} />
                                                    </button>
                                                </div>
                                                {isRunSel && (
                                                    <div className="experiment-tree__drawer">
                                                        {runJobs.length ===
                                                            0 && (
                                                            <div className="experiment-tree__status">
                                                                No jobs
                                                            </div>
                                                        )}
                                                        {runJobs.map((job) => {
                                                            const isJobLeaf =
                                                                selectedJobId ===
                                                                job.job_id;
                                                            return (
                                                                <div
                                                                    key={
                                                                        job.job_id
                                                                    }
                                                                    className={rowClass(
                                                                        false,
                                                                        isJobLeaf,
                                                                    )}
                                                                    onClick={() =>
                                                                        onSelect(
                                                                            {
                                                                                kind: "job",
                                                                                experiment_name:
                                                                                    name,
                                                                                run_id: run.run_id,
                                                                                job_id: job.job_id,
                                                                            },
                                                                        )
                                                                    }
                                                                >
                                                                    <Cog
                                                                        size={
                                                                            16
                                                                        }
                                                                        className="experiment-tree__icon"
                                                                    />
                                                                    <span className="experiment-tree__label">
                                                                        {
                                                                            job.job_id
                                                                        }
                                                                    </span>
                                                                    <span
                                                                        className={`experiment-tree__job-status ${jobStatusClass(job.status)}`}
                                                                    >
                                                                        {
                                                                            job.status
                                                                        }
                                                                    </span>
                                                                </div>
                                                            );
                                                        })}
                                                    </div>
                                                )}
                                            </Fragment>
                                        );
                                    })}
                                </div>
                            )}
                        </Fragment>
                    );
                })}
            </div>
        </div>
    );
}
