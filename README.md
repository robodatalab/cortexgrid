# RoboLab Infrastructure

Cloud infrastructure and deployment orchestration for RoboLab. All resources run on AWS (`us-east-1`) and are provisioned with Terraform.

## Architecture

```
                   +-----------+
                   |  Route53  |
                   | (DNS)     |
                   +-----+-----+
                         |
          +--------------+--------------+
          |                             |
  robodatalab.com              (default domain)
  www.robodatalab.com
          |                             |
  +-------v--------+          +--------v-------+
  |   CloudFront   |          |   CloudFront   |
  |   (website)    |          |   (platform)   |
  +-------+--------+          +--------+-------+
          |                             |
  +-------v--------+          +--------v-------+
  |   S3 Bucket    |          |   S3 Bucket    |
  |  (static SPA)  |          |  (static SPA)  |
  +----------------+          +----------------+
          ^
          |
  +-------+--------+
  | GitHub Actions  |
  | (OIDC deploy)   |
  +----------------+
```

### Folder → repository mapping

| Folder                    | Owns infrastructure for          |
|---------------------------|----------------------------------|
| `terraform/website/`      | `robolabwebsite` (marketing site)|
| `terraform/platform/`     | `robolab-platform` (platform SPA)|
| `lambda/auth/`            | Auth Lambda source (deployed by `terraform/website/`) |

Each folder contains all infrastructure for that repository. Do not create new top-level Terraform modules for features that belong to an existing repository — extend the relevant module instead.

### Terraform modules

#### `terraform/website/` — Marketing website + investor auth

Serves the marketing site at **robodatalab.com** and the investor authentication backend.
Source: `robolabwebsite` repo.

| Resource              | Purpose                                                |
|-----------------------|--------------------------------------------------------|
| S3 bucket             | Hosts built static assets (private, OAC-gated)         |
| CloudFront            | CDN; routes `/api/*` to API Gateway, `/*` to S3        |
| ACM certificate       | TLS for `robodatalab.com` + `www`, DNS-validated        |
| Route53 records       | A-record aliases for apex and `www`                     |
| OIDC provider + role  | GitHub Actions deploys via `AssumeRoleWithWebIdentity`  |
| VPC + NAT gateway     | Isolates RDS; Lambda uses NAT for outbound SES calls    |
| RDS PostgreSQL        | Investor whitelist + OTP codes (`db.t4g.micro`)         |
| Lambda (Node 22)      | Auth API — OTP email flow, JWT issuance, admin approval |
| API Gateway v2        | HTTPS endpoint for the Lambda                           |
| SES email identity    | Sends OTP and approval emails from `noreply@robodatalab.com` |
| Secrets Manager       | JWT signing secret + DB password                        |

State backend: `s3://robolab-terraform-state/website/terraform.tfstate`

**First-deploy order:**
1. `terraform apply` (with `api_gateway_domain = ""`)
2. Copy `api_gateway_invoke_url` output → set `api_gateway_domain` in `terraform.tfvars`
3. `terraform apply` again (wires up CloudFront `/api/*` behavior)
4. `psql -h <rds_endpoint> ... -f ../../lambda/auth/schema.sql` to create tables
5. Verify SES sender in AWS Console; request production access to lift sandbox

#### `terraform/platform/` — Platform frontend

Serves the platform SPA via CloudFront (no custom domain yet).
Source: `robolab-platform` repo.

| Resource              | Purpose                                              |
|-----------------------|------------------------------------------------------|
| S3 bucket             | Hosts built frontend assets (private, OAC-gated)     |
| CloudFront            | CDN with HTTP/2+3, Brotli/gzip, SPA fallback         |

State backend: S3 (currently commented out, using local state)

### Security posture

Both distributions enforce:
- HTTPS-only (HTTP -> HTTPS redirect)
- TLS 1.2+ (website), default CloudFront cert (platform)
- HSTS with preload (1 year max-age, includeSubDomains)
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- S3 buckets are fully private (all public access blocked, CloudFront OAC only)

### CI/CD

The website module provisions a GitHub Actions OIDC integration:
- An `aws_iam_openid_connect_provider` trusts `token.actions.githubusercontent.com`
- The `robolab-website-deploy` IAM role is assumable only from `paksas/robolabwebsite` on the `main` branch
- The role has least-privilege permissions: `s3:PutObject/GetObject/DeleteObject/ListBucket` and `cloudfront:CreateInvalidation`

## Related repos

| Repo                   | Description                          |
|------------------------|--------------------------------------|
| `robolab-platform`     | Platform frontend and backend        |
| `robolab-sims-unity`   | Unity simulation projects            |
| `robolab-sims-unreal`  | Unreal Engine simulation projects    |
| `robolabwebsite`       | Marketing website source             |

## Prerequisites

- Terraform >= 1.5
- AWS CLI configured with appropriate credentials
- Route53 hosted zone for `robodatalab.com` (website module)

## Usage

```bash
# Website
cd terraform/website
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init
terraform plan
terraform apply

# Platform
cd terraform/platform
cp terraform.tfvars.example terraform.tfvars   # edit values
terraform init
terraform plan
terraform apply
```

## TODO

- [ ] Deploy ArgoCD + Kargo to manage all deployments
- [ ] Add custom domain and ACM certificate for the platform frontend
- [ ] Enable S3 backend for platform Terraform state
- [ ] Add CI/CD (GitHub Actions OIDC) for platform deployments
- [ ] Set up Kubernetes manifests (`k8s/`) for simulation workloads
- [ ] Build base Docker images (`docker-base/`) for simulation runtimes
