# tailnet-dns

Route 53 CNAME records that map `<service>.robodatalab.com` to the single tailnet `gateway.<tailnet>.ts.net` hostname (Traefik exposed via the Tailscale operator). Traefik then routes requests to the right backend Service based on the Host header and terminates TLS using the wildcard `*.robodatalab.com` cert managed by cert-manager.

## Problem

We want pretty `https://<service>.robodatalab.com` URLs that only work over the tailnet. All traffic enters via one tailnet device (`gateway`); the cluster-internal Traefik handles routing and TLS.

This stack creates one Route 53 CNAME per service, all pointing at the same `gateway.<tailnet>.ts.net` target. The records are public (anyone can resolve them), but the target is a `*.ts.net` name whose IP is non-routable outside the tailnet, so only tailnet members can connect.

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
