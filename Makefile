SHELL := /bin/bash

.PHONY: head-setup worker-setup worker-teardown node-teardown restart head-aws-apply head-aws-destroy tailnet-dns-apply tailnet-dns-destroy dev install-frontend dev-frontend-against help

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

# On the head's IP, removes only the worker role; on a plain worker, same as node-teardown.
worker-teardown:
	@[ -n "$(IP)" ] || { echo "IP is required (e.g. make worker-teardown IP=100.110.47.88)"; exit 1; }
	uv run python -m k8s.seed.teardown_node --ip=$(IP) --worker $(SSH_USER_FLAG)

node-teardown:
	@[ -n "$(IP)" ] || { echo "IP is required (e.g. make teardown-node IP=100.80.27.32)"; exit 1; }
	uv run python -m k8s.seed.teardown_node --ip=$(IP) $(SSH_USER_FLAG)

restart:
	uv run python -m k8s.seed.restart_nodes

head-aws-apply:
	@[ -f .env.head ] || { echo ".env.head not found (copy .env.head.template)"; exit 1; }
	TAILSCALE_AUTH_KEY="$$(uv run python -c 'from dotenv import dotenv_values; print(dotenv_values(".env.head").get("TAILSCALE_AUTH_KEY") or "")')"; \
	  [ -n "$$TAILSCALE_AUTH_KEY" ] || { echo "TAILSCALE_AUTH_KEY missing from .env.head"; exit 1; }; \
	  cd terraform/platform && \
	  terraform init && \
	  TF_VAR_tailscale_auth_key="$$TAILSCALE_AUTH_KEY" terraform apply -auto-approve
	uv run python -m k8s.seed.update_ssh_config

head-aws-destroy:
	cd terraform/platform && TF_VAR_tailscale_auth_key=_ terraform destroy -auto-approve
	uv run python -m k8s.seed.update_ssh_config --remove

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
