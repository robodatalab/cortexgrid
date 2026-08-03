SHELL := /bin/bash

.PHONY: head-setup worker-setup node-teardown restart head-aws-apply head-aws-destroy core-aws-setup core-aws-destroy tailnet-dns-apply tailnet-dns-destroy env dev install-frontend dev-frontend-against help

# Optional SSH_USER; defaults to the laptop user if not passed.
SSH_USER_FLAG = $(if $(SSH_USER),--ssh-user=$(SSH_USER))

head-setup:
	@[ -n "$(IP)" ]           || { echo "IP is required (e.g. make head-setup IP=100.110.47.89 STORAGE_PATH=... PROFILE=onprem)"; exit 1; }
	@[ -n "$(STORAGE_PATH)" ] || { echo "STORAGE_PATH is required (path to the HDD mount used for PVCs)"; exit 1; }
	@[ -n "$(PROFILE)" ]      || { echo "PROFILE is required (aws|onprem)"; exit 1; }
	uv run python -m k8s.seed.setup_node --type=head --ip=$(IP) --storage-path=$(STORAGE_PATH) --profile=$(PROFILE) $(SSH_USER_FLAG)

worker-setup:
	@[ -n "$(IP)" ]      || { echo "IP is required (e.g. make worker-setup IP=100.80.27.32 PROFILE=onprem)"; exit 1; }
	@[ -n "$(PROFILE)" ] || { echo "PROFILE is required (aws|onprem)"; exit 1; }
	uv run python -m k8s.seed.setup_node --type=worker --ip=$(IP) --profile=$(PROFILE) $(SSH_USER_FLAG)

node-teardown:
	@[ -n "$(IP)" ] || { echo "IP is required (e.g. make teardown-node IP=100.80.27.32)"; exit 1; }
	uv run python -m k8s.seed.teardown_node --ip=$(IP) $(SSH_USER_FLAG)

restart:
	uv run python -m k8s.seed.restart_nodes

head-aws-apply:
	@[ -f .env ] || { echo ".env not found"; exit 1; }
	. ./.env; \
	  [ -n "$$TAILSCALE_AUTH_KEY" ] || { echo "TAILSCALE_AUTH_KEY missing from .env"; exit 1; }; \
	  cd terraform/platform && \
	  terraform init && \
	  TF_VAR_tailscale_auth_key="$$TAILSCALE_AUTH_KEY" terraform apply -auto-approve
	uv run python -m k8s.seed.update_ssh_config

head-aws-destroy:
	cd terraform/platform && TF_VAR_tailscale_auth_key=_ terraform destroy -auto-approve
	uv run python -m k8s.seed.update_ssh_config --remove

core-aws-setup:
	cd terraform/platform/secrets && terraform init && terraform apply -auto-approve

core-aws-destroy:
	cd terraform/platform/secrets && terraform init && terraform destroy -auto-approve

tailnet-dns-apply:
	cd terraform/tailnet-dns && terraform init && terraform apply -auto-approve

tailnet-dns-destroy:
	cd terraform/tailnet-dns && terraform destroy -auto-approve

env:
	uv run python scripts/refresh_env.py

install-frontend:
	@if [ ! -d cortexflow_ui/frontend/node_modules ]; then \
		echo "Installing frontend dependencies..."; \
		cd cortexflow_ui/frontend && npm install; \
	fi

dev: env install-frontend ## Start backend + frontend dev servers (Ctrl+C stops both)
	@trap 'kill 0' EXIT; \
	set -a; . ./.env; set +a; \
	( cd cortexflow_ui/backend && uv run --group ui uvicorn main:app --reload --host 0.0.0.0 --port 8000 ) & \
	( cd cortexflow_ui/frontend && npm run dev ) & \
	wait

dev-frontend-against: install-frontend ## Start frontend dev server against a remote backend (BACKEND=http://host:port required)
	@[ -n "$(BACKEND)" ] || { echo "Error: BACKEND=http://host:port required"; exit 1; }
	cd cortexflow_ui/frontend && VITE_API_TARGET=$(BACKEND) npm run dev

help:
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-25s %s\n", $$1, $$2}'
	@echo ""
