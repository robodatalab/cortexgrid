# Submitting Jobs

## Overview

Jobs are submitted from the Mac to the Ray cluster using `jobs/submit.py`. The script packages your working directory, uploads it to the Ray head node, and starts execution.

## Basic Usage

```bash
python jobs/submit.py \
    --script train.py \
    --working-dir ./experiments/my_experiment \
    --env pytorch \
    --gpus 1 \
    --name "my-training-run"
```

## Arguments

| Argument           | Required | Default | Description                                    |
|-------------------|----------|---------|------------------------------------------------|
| `--script`        | Yes      | —       | Python script to run (relative to working-dir) |
| `--working-dir`   | Yes      | —       | Directory uploaded to the cluster               |
| `--env`           | No       | None    | Runtime env: `pytorch` or `transformers`        |
| `--gpus`          | No       | 0       | GPUs to request                                 |
| `--name`          | No       | None    | Human-readable job name                         |
| `--mlflow-experiment` | No  | None    | MLflow experiment name                          |
| `--extra-pip`     | No       | None    | Additional pip packages                         |
| `--follow`        | No       | False   | Tail logs after submission                      |

## Runtime Environments

Pre-configured environments in `ray/runtime_envs/`:

**`pytorch`** — Core PyTorch stack:
- torch, torchvision, torchaudio, mlflow, boto3

**`transformers`** — HuggingFace + LLM stack:
- torch, transformers, datasets, accelerate, peft, mlflow, boto3

Use `--extra-pip` to add packages on top:
```bash
python jobs/submit.py \
    --script train.py \
    --working-dir . \
    --env pytorch \
    --extra-pip wandb scipy \
    --gpus 1
```

## Environment Variables

The submit script automatically injects these into every job:

| Variable                 | Source           | Purpose                    |
|--------------------------|------------------|----------------------------|
| `MLFLOW_TRACKING_URI`    | `.env`           | MLflow server URL          |
| `MLFLOW_S3_ENDPOINT_URL` | `.env`           | MinIO/S3 endpoint          |
| `AWS_ACCESS_KEY_ID`      | `.env`           | Storage credentials        |
| `AWS_SECRET_ACCESS_KEY`  | `.env`           | Storage credentials        |
| `MLFLOW_EXPERIMENT_NAME` | `--mlflow-experiment` | Experiment name       |

## Examples

### Fine-tune a transformer model

```bash
python jobs/submit.py \
    --script finetune.py \
    --working-dir ./experiments/llama-ft \
    --env transformers \
    --gpus 1 \
    --name "llama-finetune-v3" \
    --mlflow-experiment "llama" \
    --follow
```

### Run evaluation

```bash
python jobs/submit.py \
    --script jobs/examples/eval_example.py \
    --working-dir . \
    --env pytorch \
    --gpus 1 \
    --name "eval-run" \
    -- --run-id abc123
```

### Data generation (no GPU)

```bash
python jobs/submit.py \
    --script jobs/examples/datagen_example.py \
    --working-dir . \
    --env pytorch \
    --name "preprocess-data"
```

## Monitoring

After submission:

```bash
# Check all jobs
python jobs/monitor.py

# Or use Make
make monitor
```

The submit script prints a direct link to the Ray Dashboard job page for each submission.
