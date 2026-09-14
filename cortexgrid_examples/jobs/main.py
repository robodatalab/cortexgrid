"""Remote jobs — submits a job to the DGX, both sides log to the same experiment.

The main script (laptop) logs main_metric.
The remote job (DGX) logs job_metric.
After the job completes, both metrics should appear in the same MLflow run.

Requires the full DGX stack: MLflow, Ray, and the jobs control plane.
"""

import time

import cortexgrid


def job_fn():
    cortexgrid.log_metric("job_metric", 42.0)
    print("Logged job_metric=42.0 from the DGX")


def main():
    exp = cortexgrid.Experiment.init("Examples-Jobs")
    print(f"Experiment: {exp.experiment_name}")
    print(f"Run ID:     {exp.run_id}")

    cortexgrid.log_metric("main_metric", 1.0)
    print("Logged main_metric=1.0 from the laptop")

    job_id = cortexgrid.remote(job_fn)
    print(f"Submitted job: {job_id}")

    print("Waiting for the control plane to pick up and run the job...")
    terminal = (cortexgrid.JobStatus.FINISHED, cortexgrid.JobStatus.FAILED)
    while True:
        lifecycle = cortexgrid.JobLifecycle.load_from_mlflow(exp.run_id, job_id)
        ray_job_id = lifecycle.get_ray_job_id()
        status = cortexgrid.get_ray_job_status(ray_job_id)
        print(f"  status: {status.value}")
        if status in terminal:
            break
        time.sleep(5)

    if status == cortexgrid.JobStatus.FINISHED:
        print("\nJob completed. Check MLflow for both main_metric and job_metric.")
    else:
        # Ray-reported errors live in Ray logs, not on the lifecycle.
        # Check the Ray dashboard or use cortexgrid.get_ray_logs(ray_job_id).
        lifecycle = cortexgrid.JobLifecycle.load_from_mlflow(exp.run_id, job_id)
        ray_job_id = lifecycle.get_ray_job_id()
        print(f"\nJob failed. ray_job_id={ray_job_id}")
        for event in lifecycle.history:
            if event.error:
                print(f"Attempt {event.attempt} submission error: {event.error}")


if __name__ == "__main__":
    main()
