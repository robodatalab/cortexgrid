"""First experiment — runs locally on the laptop, no remote jobs.

Creates an MLflow experiment, logs a few metrics and params, then exits.
Requires MLflow to be running on the DGX.
"""

import cortexgrid

def main():
    exp = cortexgrid.Experiment.init("Examples-FirstExperiment")
    print(f"Experiment: {exp.experiment_name}")
    print(f"Run ID:     {exp.run_id}")

    cortexgrid.log_params({
        "learning_rate": 0.001,
        "batch_size": 32,
        "epochs": 10,
    })

    for step in range(1, 11):
        accuracy = 0.5 + step * 0.04
        loss = 1.0 - step * 0.08
        cortexgrid.log_metrics({"accuracy": accuracy, "loss": loss}, step=step)
        print(f"  step {step:2d}  accuracy={accuracy:.2f}  loss={loss:.2f}")

    print("\nDone. Check the MLflow UI for experiment 'Examples-FirstExperiment'.")


if __name__ == "__main__":
    main()
