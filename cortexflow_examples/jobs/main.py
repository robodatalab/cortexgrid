"""Remote jobs — submits a job to the DGX, both sides log to the same experiment.

The main script (laptop) logs main_metric.
The remote job (DGX) logs job_metric.
After the job completes, both metrics should appear in the same MLflow run.

Requires the full DGX stack: MLflow, Ray, and the jobs control plane.
"""

import time

import cortexflow


def job_fn():
    import cortexflow

    cortexflow.log_metric("job_metric", 42.0)
    print("Logged job_metric=42.0 from the DGX")


def main():
    exp = cortexflow.Experiment.init("Examples-Jobs")
    print(f"Experiment: {exp.experiment_name}")
    print(f"Run ID:     {exp.run_id}")

    cortexflow.log_metric("main_metric", 1.0)
    print("Logged main_metric=1.0 from the laptop")

    job_id = cortexflow.remote(job_fn)
    print(f"Submitted job: {job_id}")

    print("Waiting for the control plane to pick up and run the job...")
    while True:
        lifecycle = cortexflow.get_job_status(exp.run_id, job_id)
        print(f"  status: {lifecycle.status.value}")
        if lifecycle.status in (
            cortexflow.JobStatus.FINISHED,
            cortexflow.JobStatus.FAILED,
        ):
            break
        time.sleep(5)

    if lifecycle.status == cortexflow.JobStatus.FINISHED:
        print("\nJob completed. Check MLflow for both main_metric and job_metric.")
    else:
        print(f"\nJob failed: {lifecycle.error}")


if __name__ == "__main__":
    main()
