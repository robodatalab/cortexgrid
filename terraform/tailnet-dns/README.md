# tailnet-dns

Route 53 CNAME records that map `<service>.robodatalab.com` to the matching `<service>.<tailnet>.ts.net` hostname created by the Tailscale Kubernetes Operator.

## Problem

The Tailscale operator exposes annotated k8s Services as tailnet devices reachable at `<hostname>.<tailnet>.ts.net`. Those names are ugly and tailnet-specific. We want `cortexflow.robodatalab.com`, `mlflow.robodatalab.com`, etc.

This stack creates one Route 53 CNAME per service. The records are public (anyone can resolve them), but the targets are `*.ts.net` names whose IPs are non-routable outside the tailnet, so only tailnet members can connect.

## Dependencies

- AWS credentials with Route 53 write access
- The `robodatalab.com` hosted zone exists in Route 53 (managed by `terraform/website`)
- The `robolab-terraform-state` S3 bucket exists (state backend)
- The Tailscale Kubernetes Operator is installed and the corresponding Services are annotated with `tailscale.com/expose: "true"` + `tailscale.com/hostname: <label>` (otherwise the CNAME targets do not resolve)

## Usage

```
make tailnet-dns-apply
```

Edit `terraform.tfvars` to add or remove `hostnames` entries, then re-run `make tailnet-dns-apply`. To remove all records: `make tailnet-dns-destroy`.

## Files

- `main.tf` - terraform/provider/backend
- `variables.tf` - `domain_name`, `tailnet`, `hostnames`
- `dns.tf` - zone data source + CNAME records (`for_each` over `hostnames`)
- `terraform.tfvars` - the actual hostname list
