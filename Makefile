.PHONY: setup-head setup-worker teardown-node head-apply head-destroy

# Optional SSH_USER; defaults to the laptop user if not passed.
SSH_USER_FLAG = $(if $(SSH_USER),--ssh-user=$(SSH_USER))

setup-head:
	@[ -n "$(IP)" ]           || { echo "IP is required (e.g. make setup-head IP=100.110.47.89 STORAGE_PATH=...)"; exit 1; }
	@[ -n "$(STORAGE_PATH)" ] || { echo "STORAGE_PATH is required (path to the HDD mount used for PVCs)"; exit 1; }
	uv run python -m k8s.seed.setup_node --type=head --ip=$(IP) --storage-path=$(STORAGE_PATH) $(SSH_USER_FLAG)

setup-worker:
	@[ -n "$(IP)" ] || { echo "IP is required (e.g. make setup-worker IP=100.80.27.32)"; exit 1; }
	uv run python -m k8s.seed.setup_node --type=worker --ip=$(IP) $(SSH_USER_FLAG)

teardown-node:
	@[ -n "$(IP)" ] || { echo "IP is required (e.g. make teardown-node IP=100.80.27.32)"; exit 1; }
	uv run python -m k8s.seed.teardown_node --ip=$(IP) $(SSH_USER_FLAG)

head-apply:
	@[ -f .env ] || { echo ".env not found"; exit 1; }
	. ./.env; \
	  [ -n "$$TAILSCALE_AUTH_KEY" ] || { echo "TAILSCALE_AUTH_KEY missing from .env"; exit 1; }; \
	  cd terraform/platform/head && \
	  terraform init && \
	  TF_VAR_tailscale_auth_key="$$TAILSCALE_AUTH_KEY" terraform apply -auto-approve
	uv run python -m k8s.seed.update_ssh_config

head-destroy:
	cd terraform/platform/head && TF_VAR_tailscale_auth_key=_ terraform destroy -auto-approve
	uv run python -m k8s.seed.update_ssh_config --remove
