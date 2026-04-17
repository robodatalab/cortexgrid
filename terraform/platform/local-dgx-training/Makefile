# All targets assume the working directory is local-dgx-training/.

SHELL := /bin/bash
.DEFAULT_GOAL := help

check-dir:
	@if [ ! -f docker-compose.yml ]; then \
		echo "Error: Run make from terraform/platform/local-dgx-training/"; \
		exit 1; \
	fi

.PHONY: setup-dgx
setup-dgx: check-dir ## Full DGX setup (from Mac): sync files, inject creds from CMS, start stack
	@bash scripts/setup-dgx.sh

.PHONY: setup-mac
setup-mac: check-dir ## Mac setup: configure secrets and shell env vars
	@bash scripts/setup-mac.sh

.PHONY: teardown-dgx
teardown-dgx: check-dir ## Tear down DGX (from Mac): stop stack, remove volumes
	@bash scripts/teardown-dgx.sh

.PHONY: up
up: check-dir ## Start all services on DGX
	docker compose --profile monitoring up -d --build

.PHONY: down
down: check-dir ## Stop all services on DGX
	docker compose --profile monitoring down

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

.PHONY: health
health: check-dir ## Run health check against all services
	@bash scripts/health-check.sh

.PHONY: help
help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-17s %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make setup-mac                              # Verify Mac prereqs"
	@echo "  make setup-dgx                              # Deploy stack to DGX"
	@echo "  make health                                 # Check all services"
