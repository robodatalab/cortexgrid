# =============================================================================
# AWS Secrets Manager — IAM and access control
# =============================================================================
#
# Secret VALUES are managed by scripts/push-secrets.sh (not Terraform).
# This module only creates IAM users, roles, and policies for accessing them.
#
# Namespace layout:
#   robolab/auth/*     — website/investor auth (JWT, DB password)
#   robolab/infra/*    — DGX training infrastructure (MLflow, MinIO, Grafana)
