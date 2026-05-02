# All targets assume the working directory is cortexflow-ui/.

SHELL := /bin/bash
.DEFAULT_GOAL := help

check-dir:
	@if [ ! -d backend ] || [ ! -d frontend ]; then \
		echo "Error: Run make from cortexflow-ui/"; \
		exit 1; \
	fi

.PHONY: install-frontend
install-frontend: check-dir
	@if [ ! -d frontend/node_modules ]; then \
		echo "Installing frontend dependencies..."; \
		cd frontend && npm install; \
	fi

.PHONY: dev
dev: check-dir install-frontend ## Start backend + frontend dev servers (Ctrl+C stops both)
	@trap 'kill 0' EXIT; \
	( cd backend && uv run --group ui uvicorn main:app --reload --host 0.0.0.0 --port 8000 ) & \
	( cd frontend && npm run dev ) & \
	wait

.PHONY: dev-frontend-against
dev-frontend-against: check-dir install-frontend ## Start frontend dev server against a remote backend (BACKEND=http://host:port required)
	@if [ -z "$(BACKEND)" ]; then echo "Error: BACKEND=http://host:port required"; exit 1; fi
	cd frontend && VITE_API_TARGET=$(BACKEND) npm run dev

.PHONY: help
help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-17s %s\n", $$1, $$2}'
	@echo ""
