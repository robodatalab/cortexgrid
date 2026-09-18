"""Checkpoint + retry — the job crashes on first run, succeeds on second.

The remote job keeps an execution counter via cortexgrid.checkpoint().
First execution: saves counter=1 to checkpoint, then raises.
Second execution (retry): resumes from checkpoint, sees counter=1, logs success.

Requires the full DGX stack: MLflow, Ray, and the jobs control plane.
"""

import time

import cortexgrid
from cortexgrid.checkpoint import checkpoint, resume


def crashy_job():
    ckpt = resume()
    if ckpt:
        print(f"Resumed from checkpoint: execution_count={ckpt.execution_count}")
        cortexgrid.log_metric("final_execution_count", ckpt.execution_count + 1)
        print("Second run succeeded!")
        return

    print("First run — saving checkpoint and crashing")
    with checkpoint() as ckpt:
        ckpt.execution_count = 1

    raise RuntimeError("Intentional crash to test retry")


def _wait_for_terminal(run_id: str, job_id: str) -> cortexgrid.JobStatus:
    """Poll the lifecycle until Ray reports a terminal state."""
    while True:
        lifecycle = cortexgrid.JobLifecycle.load_from_mlflow(run_id, job_id)
        ray_job_id = lifecycle.get_ray_job_id()
        status = cortexgrid.get_ray_job_status(ray_job_id)
        print(f"  status: {status.value}  ray_job: {ray_job_id or 'not yet scheduled'}")
        if status in (cortexgrid.JobStatus.FINISHED, cortexgrid.JobStatus.FAILED):
            return status
        time.sleep(5)


def main():
    exp = cortexgrid.Experiment.init("Examples-Checkpoints")
    print(f"Experiment: {exp.experiment_name}")
    print(f"Run ID:     {exp.run_id}")

    # Attempt 1 — expected to crash and save a checkpoint.
    # Automatic retry by the control plane is not currently implemented, so
    # this example drives the retry from the client side. The checkpoint is
    # scoped to the experiment run, so the second job sees it on resume.
    first_job = cortexgrid.remote(crashy_job).job_id
    print(f"Submitted first job: {first_job}")
    first_status = _wait_for_terminal(exp.run_id, first_job)
    if first_status != cortexgrid.JobStatus.FAILED:
        print(f"\nExpected first attempt to fail, got {first_status.value}.")
        return
    print(
        "\nFirst attempt failed as expected; re-submitting to resume from checkpoint."
    )

    # Attempt 2 — cortexgrid.resume() should return the saved checkpoint.
    second_job = cortexgrid.remote(crashy_job).job_id
    print(f"Submitted second job: {second_job}")
    second_status = _wait_for_terminal(exp.run_id, second_job)

    if second_status == cortexgrid.JobStatus.FINISHED:
        print("\nJob survived the crash and completed on re-submission!")
        print("Check MLflow for final_execution_count=2.")
    else:
        print(f"\nUnexpected final status: {second_status.value}")


if __name__ == "__main__":
    main()
