#!/usr/bin/env python3
"""Example: Fault-tolerant PyTorch training job with MLflow tracking.

This demonstrates:
- ray.train.TorchTrainer with FailureConfig for automatic worker restart
- Checkpoint-aware training loop using MLflow artifact storage
- Resume from a previous run via --run-id

Usage:
    python jobs/submit.py \\
        --script jobs/examples/train_example.py \\
        --working-dir . \\
        --env pytorch \\
        --gpus 1 \\
        --name "train-example" \\
        --mlflow-experiment "examples"

Resume a failed run:
    python jobs/examples/train_example.py --run-id <MLFLOW_RUN_ID>
"""

from __future__ import annotations

import argparse
import os
import tempfile
from typing import Any

import mlflow
import ray
import ray.train
import torch
import torch.nn as nn
import torch.optim as optim
from ray.train import CheckpointConfig, FailureConfig, RunConfig, ScalingConfig
from ray.train.torch import TorchTrainer
from torch.utils.data import DataLoader, TensorDataset


# ---------------------------------------------------------------------------
# Model — simple feedforward net for demonstration
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Training function (runs inside Ray Train worker)
# ---------------------------------------------------------------------------
def train_func(config: dict[str, Any]) -> None:
    """Training loop executed by each Ray Train worker."""
    epochs: int = config.get("epochs", 20)
    lr: float = config.get("lr", 1e-3)
    batch_size: int = config.get("batch_size", 64)
    checkpoint_every: int = config.get("checkpoint_every", 5)
    mlflow_run_id: str | None = config.get("mlflow_run_id")

    # Set up MLflow
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "")
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)

    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "default")
    mlflow.set_experiment(experiment_name)

    # Synthetic dataset for demonstration
    x = torch.randn(1000, 128)
    y = torch.randint(0, 10, (1000,))
    dataset = TensorDataset(x, y)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    model = SimpleNet()
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=lr)

    # Resume from Ray Train checkpoint if available
    start_epoch = 0
    checkpoint = ray.train.get_checkpoint()
    if checkpoint:
        with checkpoint.as_directory() as checkpoint_dir:
            state = torch.load(os.path.join(checkpoint_dir, "model.pt"))
            model.load_state_dict(state["model_state_dict"])
            optimizer.load_state_dict(state["optimizer_state_dict"])
            start_epoch = state["epoch"] + 1
            print(f"Resumed from checkpoint at epoch {start_epoch}")

    # Wrap model for distributed training
    model = ray.train.torch.prepare_model(model)
    dataloader = ray.train.torch.prepare_data_loader(dataloader)

    # Start or resume MLflow run
    mlflow_ctx = mlflow.start_run(run_id=mlflow_run_id) if mlflow_run_id else mlflow.start_run()

    with mlflow_ctx as run:
        mlflow.log_params({"epochs": epochs, "lr": lr, "batch_size": batch_size})

        for epoch in range(start_epoch, epochs):
            model.train()
            total_loss = 0.0
            num_batches = 0

            for batch_x, batch_y in dataloader:
                optimizer.zero_grad()
                outputs = model(batch_x)
                loss = criterion(outputs, batch_y)
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
                num_batches += 1

            avg_loss = total_loss / max(num_batches, 1)
            mlflow.log_metric("train_loss", avg_loss, step=epoch)
            print(f"Epoch {epoch}/{epochs} — loss: {avg_loss:.4f}")

            # Checkpoint every N epochs
            if epoch % checkpoint_every == 0 or epoch == epochs - 1:
                with tempfile.TemporaryDirectory() as tmpdir:
                    state = {
                        "epoch": epoch,
                        "model_state_dict": model.module.state_dict()
                        if hasattr(model, "module")
                        else model.state_dict(),
                        "optimizer_state_dict": optimizer.state_dict(),
                    }
                    torch.save(state, os.path.join(tmpdir, "model.pt"))

                    # Save checkpoint to MLflow as well
                    mlflow.log_artifact(
                        os.path.join(tmpdir, "model.pt"),
                        artifact_path=f"checkpoints/epoch_{epoch}",
                    )

                    # Report to Ray Train (enables fault-tolerant resume)
                    ray.train.report(
                        metrics={"train_loss": avg_loss, "epoch": epoch},
                        checkpoint=ray.train.Checkpoint.from_directory(tmpdir),
                    )

        print(f"Training complete. MLflow run ID: {run.info.run_id}")


# ---------------------------------------------------------------------------
# Main — set up Ray TorchTrainer with fault tolerance
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Fault-tolerant training example")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--checkpoint-every", type=int, default=5)
    parser.add_argument("--run-id", default=None, help="MLflow run ID to resume")
    parser.add_argument("--num-workers", type=int, default=1)
    args = parser.parse_args()

    ray.init()

    train_config = {
        "epochs": args.epochs,
        "lr": args.lr,
        "batch_size": args.batch_size,
        "checkpoint_every": args.checkpoint_every,
        "mlflow_run_id": args.run_id,
    }

    trainer = TorchTrainer(
        train_func,
        train_loop_config=train_config,
        scaling_config=ScalingConfig(
            num_workers=args.num_workers,
            use_gpu=True,
        ),
        run_config=RunConfig(
            name="train-example",
            failure_config=FailureConfig(max_failures=3),
            checkpoint_config=CheckpointConfig(
                num_to_keep=2,
                checkpoint_score_attribute="train_loss",
                checkpoint_score_order="min",
            ),
        ),
    )

    result = trainer.fit()
    print(f"Training finished. Best checkpoint: {result.checkpoint}")


if __name__ == "__main__":
    main()
