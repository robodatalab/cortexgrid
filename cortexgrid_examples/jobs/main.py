"""Remote jobs — submits a job to the DGX, both sides log to the same experiment.

The main script (laptop) logs main_metric.
The remote job (DGX) logs job_metric and returns a value.
After the job completes, both metrics should appear in the same MLflow run.

`remote` hands back a JobFuture immediately; `result()` blocks until the job
reaches a terminal state and then behaves exactly like a local call — it
returns what the function returned, or raises what it raised.

Requires the full DGX stack: MLflow, Ray, and the jobs control plane.
"""

import cortexgrid


def job_fn(scale: float) -> dict[str, float]:
    cortexgrid.log_metric("job_metric", 42.0)
    print("Logged job_metric=42.0 from the DGX")
    return {"scaled": 42.0 * scale}


def main():
    exp = cortexgrid.Experiment.init("Examples-Jobs")
    print(f"Experiment: {exp.experiment_name}")
    print(f"Run ID:     {exp.run_id}")

    cortexgrid.log_metric("main_metric", 1.0)
    print("Logged main_metric=1.0 from the laptop")

    job = cortexgrid.remote(job_fn, 2.0)
    print(f"Submitted job: {job.job_id} (status: {job.status().value})")

    print("Waiting for the control plane to pick up and run the job...")
    try:
        result = job.result()
    except cortexgrid.JobFailed as exc:
        # The job never got as far as returning: submission failed, or the
        # driver died. Ray-reported errors live in the Ray logs.
        print(f"\nJob did not run: {exc}")
        return
    except Exception as exc:
        # Whatever job_fn raised on the DGX, re-raised here. The chained cause
        # carries the remote traceback.
        print(f"\nJob raised {type(exc).__name__}: {exc}")
        return

    print(f"\nJob returned: {result}")
    print("Check MLflow for both main_metric and job_metric.")


if __name__ == "__main__":
    main()
