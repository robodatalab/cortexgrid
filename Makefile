SHELL := /bin/bash

.PHONY: tailnet-dns-apply tailnet-dns-destroy dev install-frontend dev-frontend-against help

# Cluster nodes are managed by ./cg (./cg --help).

tailnet-dns-apply:
	cd terraform/tailnet-dns && terraform init && terraform apply -auto-approve

tailnet-dns-destroy:
	cd terraform/tailnet-dns && terraform destroy -auto-approve

install-frontend:
	@if [ ! -d cortexgrid_ui/frontend/node_modules ]; then \
		echo "Installing frontend dependencies..."; \
		cd cortexgrid_ui/frontend && npm install; \
	fi

dev: install-frontend ## Start backend + frontend dev servers (Ctrl+C stops both; needs CORTEXGRID_HEAD_URL, e.g. in .env)
	@trap 'kill 0' EXIT; \
	set -a; [ ! -f .env ] || . ./.env; set +a; \
	( cd cortexgrid_ui/backend && uv run --group ui uvicorn main:app --reload --host 0.0.0.0 --port 8000 ) & \
	( cd cortexgrid_ui/frontend && npm run dev ) & \
	wait

dev-frontend-against: install-frontend ## Start frontend dev server against a remote backend (BACKEND=http://host:port required)
	@[ -n "$(BACKEND)" ] || { echo "Error: BACKEND=http://host:port required"; exit 1; }
	cd cortexgrid_ui/frontend && VITE_API_TARGET=$(BACKEND) npm run dev

help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-25s %s\n", $$1, $$2}'
	@echo ""
