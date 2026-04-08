# Secrets Management

All secrets are stored in **AWS Secrets Manager** as the single source of truth. Local `.env` files are either pushed to or pulled from AWS SM — never left only on disk.

## Architecture

```
.env (on your Mac)
    │
    │  make setup-mac (first time)
    │  push-secrets.sh
    ▼
AWS Secrets Manager
    │
    ├── robolab/infra/dgx-tailscale-ip
    ├── robolab/infra/mlflow-postgres-password
    ├── robolab/infra/minio-root-password
    └── robolab/infra/grafana-admin-password
         │
         │  make setup-dgx
         │  pull-secrets.sh
         ▼
    .env (on the DGX) → Docker Compose
```

## First-Time Setup

### 1. Create IAM resources (once)

```bash
cd terraform/platform/secrets
terraform init
terraform apply
```

This creates:
- `robolab-dgx` IAM user (for the DGX to pull secrets)
- `robolab-github-actions` IAM role (for CI to read secrets)
- GitHub OIDC provider

Note the outputs:
- `dgx_user_access_key_id` — save this for step 3
- `dgx_user_secret_access_key` — save this for step 3
- `github_actions_role_arn` — save this for step 4

### 2. Initialize secrets from your Mac

```bash
cd terraform/platform/local-dgx-training
cp .env.example .env
```

Fill in your values:
```bash
DGX_TAILSCALE_IP=100.x.x.x           # run `tailscale ip -4` on the DGX
POSTGRES_PASSWORD=$(openssl rand -base64 24)
MINIO_ROOT_PASSWORD=$(openssl rand -base64 24)
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24)
```

Then run setup — it detects this is the first time, pushes secrets to AWS SM, and configures your Mac:
```bash
make setup-mac
```

### 3. Bootstrap the DGX

On the DGX, configure AWS CLI with the IAM user from step 1:
```bash
aws configure
# Access Key ID:     <dgx_user_access_key_id>
# Secret Access Key: <dgx_user_secret_access_key>
# Region:            us-east-1
```

Then run setup — it pulls secrets from AWS SM and starts the stack:
```bash
cd terraform/platform/local-dgx-training
make setup-dgx
```

### 4. Configure GitHub Actions (for CI)

In your GitHub repo settings, add this repository variable:
```
AWS_ROLE_ARN = <github_actions_role_arn from terraform output>
```

In your workflow:
```yaml
permissions:
  id-token: write
  contents: read

steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: ${{ vars.AWS_ROLE_ARN }}
      aws-region: us-east-1

  - name: Fetch secrets
    run: |
      DB_PASSWORD=$(aws secretsmanager get-secret-value \
        --secret-id robolab/auth/db-password \
        --query SecretString --output text)
      echo "::add-mask::$DB_PASSWORD"
      echo "DB_PASSWORD=$DB_PASSWORD" >> $GITHUB_ENV
```

## Rotating Secrets

1. Update the value in `.env`
2. Push to AWS SM:
   ```bash
   bash scripts/push-secrets.sh
   ```
3. Re-pull on the DGX and restart:
   ```bash
   bash scripts/pull-secrets.sh
   docker compose restart
   ```

## Scripts

| Script | Direction | Purpose |
|--------|-----------|---------|
| `scripts/push-secrets.sh` | `.env` → AWS SM | Create or update secrets from local values |
| `scripts/pull-secrets.sh` | AWS SM → `.env` | Generate `.env` from AWS SM |

## IAM Permissions

| Principal | Secrets Access |
|-----------|---------------|
| `robolab-dgx` IAM user | Read `robolab/infra/*` only |
| `robolab-github-actions` IAM role | Read all `robolab/*` |
| Website Lambda role | Read `robolab/auth/*` only |
| Your personal AWS credentials | Full access (used for push) |
