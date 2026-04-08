# =============================================================================
# RoboLab ML Infrastructure — Makefile
# =============================================================================
# All targets assume the working directory is local-dgx-training/.

SHELL := /bin/bash
.DEFAULT_GOAL := help

# Validate we're in the right directory
check-dir:
	@if [ ! -f docker-compose.yml ]; then \
		echo "Error: Run make from terraform/platform/local-dgx-training/"; \
		exit 1; \
	fi

# ---------------------------------------------------------------------------
# Setup & Teardown
# ---------------------------------------------------------------------------

.PHONY: setup-dgx
setup-dgx: check-dir ## Full DGX setup: provision secrets, pull images, start stack
	@bash scripts/setup-dgx.sh

.PHONY: setup-mac
setup-mac: check-dir ## Full Mac setup: install deps, pull secrets, configure shell
	@bash scripts/setup-mac.sh

.PHONY: teardown-dgx
teardown-dgx: check-dir ## Tear down DGX: stop stack, remove volumes and .env
	@echo "This will stop all services and DELETE all data (volumes)."
	@read -rp "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	docker compose down -v
	rm -f .env
	@echo "DGX teardown complete. Volumes and .env removed."

.PHONY: teardown-mac
teardown-mac: check-dir ## Tear down Mac: remove shell exports, .env, and pip packages
	@echo "Removing shell exports and local config..."
	@SHELL_RC="$$HOME/.zshrc"; \
	if [ "$$SHELL" = "/bin/bash" ]; then SHELL_RC="$$HOME/.bashrc"; fi; \
	for var in RAY_ADDRESS MLFLOW_TRACKING_URI DGX_TAILSCALE_IP; do \
		sed -i '' "/^export $${var}=/d" "$$SHELL_RC" 2>/dev/null || true; \
	done; \
	echo "  Cleaned $$SHELL_RC"
	rm -f .env
	pip uninstall -y ray mlflow boto3 pyyaml python-dotenv 2>/dev/null || true
	@echo "Mac teardown complete. Open a new terminal to apply shell changes."

# ---------------------------------------------------------------------------
# Stack management
# ---------------------------------------------------------------------------

.PHONY: up
up: check-dir ## Start all services
	docker compose up -d --build

.PHONY: down
down: check-dir ## Stop all services
	docker compose down

.PHONY: restart
restart: check-dir ## Restart all services
	docker compose restart

.PHONY: logs
logs: check-dir ## Tail logs from all services
	docker compose logs -f --tail=100

.PHONY: ps
ps: check-dir ## Show running services
	docker compose ps

.PHONY: pull
pull: check-dir ## Pull latest images
	docker compose pull

# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

.PHONY: health
health: check-dir ## Run health check against all services
	@bash scripts/health-check.sh

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

.PHONY: submit
submit: check-dir ## Submit a job (pass ARGS="--script ... --env ...")
	python3 jobs/submit.py $(ARGS)

.PHONY: monitor
monitor: check-dir ## Show cluster and job status
	python3 jobs/monitor.py

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

.PHONY: help
help: ## Show this help
	@echo "RoboLab ML Infrastructure"
	@echo ""
	@echo "Usage: make <target> [ARGS=\"...\"]"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-17s %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make setup-dgx                              # First-time DGX setup"
	@echo "  make setup-mac                              # First-time Mac setup"
	@echo "  make submit ARGS=\"--script t.py --env pytorch --gpus 1\""
	@echo "  make teardown-dgx                           # Full DGX cleanup"
