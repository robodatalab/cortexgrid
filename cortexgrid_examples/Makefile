# Cortexgrid examples — run from the repo root's .venv
#
# Prerequisites:
#   - DGX stack running (MLflow, Ray, MinIO, jobs control plane)
#   - cortexgrid installed in the active venv (pip install -e .)
#
# Usage:
#   make -C cortexgrid_examples first-experiment   # laptop only, needs MLflow
#   make -C cortexgrid_examples jobs               # needs full stack
#   make -C cortexgrid_examples checkpoints        # needs full stack + retry
#   make -C cortexgrid_examples model-dependencies # needs full stack + Ray Serve

PYTHON ?= python

.PHONY: first-experiment jobs checkpoints model-dependencies all

first-experiment:
	cd first_experiment && $(PYTHON) main.py

jobs:
	cd jobs && $(PYTHON) main.py

checkpoints:
	cd checkpoints && $(PYTHON) main.py

model-dependencies:
	cd model_dependencies && $(PYTHON) main.py

all: first-experiment jobs checkpoints model-dependencies
