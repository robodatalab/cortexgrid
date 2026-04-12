# Cortexflow examples — run from the repo root's .venv
#
# Prerequisites:
#   - DGX stack running (MLflow, Ray, MinIO, jobs control plane)
#   - cortexflow installed in the active venv (pip install -e .)
#
# Usage:
#   make -C cortexflow_examples first-experiment   # laptop only, needs MLflow
#   make -C cortexflow_examples jobs               # needs full stack
#   make -C cortexflow_examples checkpoints        # needs full stack + retry

PYTHON ?= python

.PHONY: first-experiment jobs checkpoints all

first-experiment:
	$(PYTHON) first_experiment/main.py

jobs:
	$(PYTHON) jobs/main.py

checkpoints:
	$(PYTHON) checkpoints/main.py

all: first-experiment jobs checkpoints
