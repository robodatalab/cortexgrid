"""Checkpoint + retry — the job crashes on first run, succeeds on second.

The remote job keeps an execution counter via cortexflow.checkpoint().
First execution: saves counter=1 to checkpoint, then raises.
Second execution (retry): resumes from checkpoint, sees counter=1, logs success.

Requires the full DGX stack: MLflow, Ray, and the jobs control plane.
"""

import time

import cortexflow


def crashy_job():
    import cortexflow

    ckpt = cortexflow.resume()
    if ckpt:
        print(f"Resumed from checkpoint: execution_count={ckpt.execution_count}")
        cortexflow.log_metric("final_execution_count", ckpt.execution_count + 1)
        print("Second run succeeded!")
        return

    print("First run — saving checkpoint and crashing")
    with cortexflow.checkpoint() as ckpt:
        ckpt.execution_count = 1

    raise RuntimeError("Intentional crash to test retry")


def main():
    exp = cortexflow.Experiment.init("Examples-Checkpoints")
    print(f"Experiment: {exp.experiment_name}")
    print(f"Run ID:     {exp.run_id}")

    job_id = cortexflow.remote(crashy_job, retry=True)
    print(f"Submitted job: {job_id} (retry=True)")

    print("Waiting for the job to crash, retry, and succeed...")
    while True:
        lifecycle = cortexflow.get_job_status(exp.run_id, job_id)
        status = lifecycle.status.value
        ray_id = lifecycle.ray_job_id or "not yet scheduled"
        print(f"  status: {status}  ray_job: {ray_id}")
        if lifecycle.status == cortexflow.JobStatus.FINISHED:
            break
        if lifecycle.status == cortexflow.JobStatus.FAILED and not lifecycle.retry:
            print(f"\nJob failed permanently: {lifecycle.error}")
            break
        time.sleep(5)

    if lifecycle.status == cortexflow.JobStatus.FINISHED:
        print("\nJob survived the crash and completed on retry!")
        print("Check MLflow for final_execution_count=2.")


if __name__ == "__main__":
    main()
