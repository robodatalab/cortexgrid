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
	cd first_experiment && $(PYTHON) main.py

jobs:
	cd jobs && $(PYTHON) main.py

checkpoints:
	cd checkpoints && $(PYTHON) main.py

all: first-experiment jobs checkpoints
