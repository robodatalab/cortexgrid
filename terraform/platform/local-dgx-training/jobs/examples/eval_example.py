#!/usr/bin/env python3
"""Example: Fault-tolerant model evaluation job.

Loads a trained model checkpoint from a previous MLflow run and evaluates it,
logging results back to the same run.

Usage:
    python jobs/submit.py \\
        --script jobs/examples/eval_example.py \\
        --working-dir . \\
        --env pytorch \\
        --gpus 1 \\
        --name "eval-example" \\
        -- --run-id <MLFLOW_RUN_ID>
"""

from __future__ import annotations

import argparse
import os

import mlflow
import ray
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset


class SimpleNet(nn.Module):
    def __init__(self, input_dim: int = 128, hidden_dim: int = 256, output_dim: int = 10) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, output_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


@ray.remote(num_gpus=1, max_retries=3, retry_exceptions=True)
def evaluate_model(run_id: str, num_samples: int = 500) -> dict[str, float]:
    """Evaluate a model checkpoint from MLflow. Retries automatically on failure."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)

    client = mlflow.tracking.MlflowClient()

    # Find the latest checkpoint
    artifacts = client.list_artifacts(run_id, "checkpoints")
    if not artifacts:
        raise ValueError(f"No checkpoints found for run {run_id}")

    latest_checkpoint = sorted(artifacts, key=lambda a: a.path)[-1]
    local_path = mlflow.artifacts.download_artifacts(
        run_id=run_id,
        artifact_path=f"{latest_checkpoint.path}/model.pt",
    )

    # Load model
    model = SimpleNet()
    state = torch.load(local_path, map_location="cuda" if torch.cuda.is_available() else "cpu")
    model.load_state_dict(state["model_state_dict"])
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = model.to(device)
    model.eval()

    # Synthetic eval dataset
    x = torch.randn(num_samples, 128)
    y = torch.randint(0, 10, (num_samples,))
    dataset = TensorDataset(x, y)
    dataloader = DataLoader(dataset, batch_size=64)

    criterion = nn.CrossEntropyLoss()
    total_loss = 0.0
    correct = 0
    total = 0

    with torch.no_grad():
        for batch_x, batch_y in dataloader:
            batch_x, batch_y = batch_x.to(device), batch_y.to(device)
            outputs = model(batch_x)
            loss = criterion(outputs, batch_y)
            total_loss += loss.item() * batch_x.size(0)
            _, predicted = outputs.max(1)
            correct += predicted.eq(batch_y).sum().item()
            total += batch_y.size(0)

    eval_loss = total_loss / total
    accuracy = correct / total

    # Log results back to the same MLflow run
    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics({
            "eval_loss": eval_loss,
            "eval_accuracy": accuracy,
        })

    print(f"Evaluation complete — loss: {eval_loss:.4f}, accuracy: {accuracy:.4f}")
    return {"eval_loss": eval_loss, "eval_accuracy": accuracy}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a trained model")
    parser.add_argument("--run-id", required=True, help="MLflow run ID with model checkpoint")
    parser.add_argument("--num-samples", type=int, default=500, help="Number of eval samples")
    args = parser.parse_args()

    ray.init()

    result = ray.get(evaluate_model.remote(args.run_id, args.num_samples))
    print(f"Results: {result}")


if __name__ == "__main__":
    main()
