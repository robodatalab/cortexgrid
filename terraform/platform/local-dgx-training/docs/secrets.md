# Secrets Management

All configuration and secrets live in **AWS Secrets Manager** under the
`robolab/infra/*` namespace. It functions as the Configuration
Management Service — everything that isn't the bootstrap identity is
fetched from it at runtime.

## Architecture

```
AWS Secrets Manager (source of truth)
    │
    │  managed via the Platform UI or `aws secretsmanager` CLI
    │
    ├── robolab/infra/AWS_ACCESS_KEY_ID       ┐ bootstrap creds
    ├── robolab/infra/AWS_SECRET_ACCESS_KEY   ┘  (robolab-dgx IAM user)
    ├── robolab/infra/DGX_TAILSCALE_IP
    └── robolab/infra/GH_TOKEN
         │
         │  `make setup-dgx` injects bootstrap creds as env vars into
         │  the SSH session that runs `docker compose up` — never
         │  persisted to disk on the DGX
         ▼
    Only containers running our Python code hold AWS creds and fetch
    from the CMS at runtime via cortexflow.secrets.get_secret():
      - jobs-control-plane
      - ray-head (entrypoint fetches DGX_TAILSCALE_IP)
    OSS services (postgres, minio, mlflow, grafana) use hardcoded
    admin/admin creds — the DGX is an isolated single-tenant machine,
    so those aren't secrets.
```

## First-Time Setup

### 1. Create IAM resources (once)

```bash
cd terraform/platform/secrets
terraform init
terraform apply
```

This provisions:
- `robolab-dgx` IAM user (containers use this to read Secrets Manager)
- `robolab-github-actions` IAM role (CI assumes this via OIDC)
- GitHub OIDC provider

Grab the outputs:
- `dgx_user_access_key_id` and `dgx_user_secret_access_key`
- `github_actions_role_arn`

### 2. Seed Secrets Manager

Using the Platform UI or the AWS CLI, create these secrets under
`robolab/infra/`:

- `AWS_ACCESS_KEY_ID` — the `robolab-dgx` access key from step 1
- `AWS_SECRET_ACCESS_KEY` — the `robolab-dgx` secret access key from step 1
- `DGX_TAILSCALE_IP` — run `tailscale ip -4` on the DGX
- `GH_TOKEN` — a GitHub personal access token for private pip installs

### 3. Deploy the stack

From your Mac (needs AWS creds that can read `robolab/infra/*`):

```bash
cd terraform/platform/local-dgx-training
make setup-mac   # verifies prereqs
make setup-dgx   # SSHes to the DGX, injects bootstrap creds, runs compose up
```

`setup-dgx.sh` fetches `DGX_TAILSCALE_IP` and the bootstrap creds from
Secrets Manager and injects the creds as env vars into the SSH session
that runs `docker compose up`. The creds are interpolated into the
`${AWS_ACCESS_KEY_ID}` / `${AWS_SECRET_ACCESS_KEY}` references in
compose and land in the `jobs-control-plane` and `ray-head` containers.
From there, those services fetch any further values they need via
`cortexflow.secrets.get_secret()` at runtime.

### 4. Configure GitHub Actions (for CI)

The `robolab-github-actions` IAM role trusts GitHub's OIDC provider for
all repos in `var.github_repos`. CI workflows that need AWS access
(e.g. ECR pushes) assume it:

```yaml
permissions:
  id-token: write
  contents: read

steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: arn:aws:iam::517906913330:role/robolab-github-actions
      aws-region: us-east-1
```

## Rotating Secrets

Update the value in Secrets Manager (via UI or CLI). Services that fetch
at process startup (`ray-head`'s entrypoint) pick up the new value on
`docker compose restart <service>`. Services that fetch lazily on each
call (`jobs-control-plane` via `cortexflow.secrets`) pick it up on the
next call without a restart.

## IAM Permissions

| Principal | Secrets Access |
|-----------|---------------|
| `robolab-dgx` IAM user | Read `robolab/infra/*` only |
| `robolab-github-actions` IAM role | Read all `robolab/*` |
| Website Lambda role | Read `robolab/auth/*` only |
