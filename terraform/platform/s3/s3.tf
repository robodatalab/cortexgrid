resource "aws_s3_bucket" "main" {
  bucket        = var.bucket_name
  force_destroy = true

  tags = {
    Project   = "robolab"
    Component = "s3"
  }
}

resource "aws_s3_bucket_public_access_block" "main" {
  bucket = aws_s3_bucket.main.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Loki keeps its chunks in a dedicated bucket so its retention and
# lifecycle policy stay independent of the shared data bucket. The name
# is referenced directly from the Loki Argo App's Helm values; no SM
# entry because nothing else consumes it.
resource "aws_s3_bucket" "loki_chunks" {
  bucket        = "robolab-loki-chunks"
  force_destroy = true

  tags = {
    Project   = "robolab"
    Component = "loki"
  }
}

resource "aws_s3_bucket_public_access_block" "loki_chunks" {
  bucket = aws_s3_bucket.loki_chunks.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "loki_chunks" {
  bucket = aws_s3_bucket.loki_chunks.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Surface the bucket name in SM so apps can read it via ESO instead of
# hardcoding it into manifests.

resource "aws_secretsmanager_secret" "bucket_name" {
  name = "robolab/infra/S3_BUCKET_NAME"
  # Purge immediately on destroy. Default 30-day recovery window blocks
  # subsequent apply with "scheduled for deletion" -- bad for dev infra that
  # cycles destroy/apply. Matches cortexflow.secrets.delete_secret which
  # uses ForceDeleteWithoutRecovery=True.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "bucket_name" {
  secret_id     = aws_secretsmanager_secret.bucket_name.id
  secret_string = aws_s3_bucket.main.id
}

# Regional S3 endpoint URL. boto3 with this URL hits real S3 the same as if
# no endpoint were passed for this region; storing it explicitly means
# cortexflow can read a single SM key regardless of profile (on-prem
# overwrites it with the in-cluster MinIO URL).
resource "aws_secretsmanager_secret" "s3_endpoint_url" {
  name                    = "robolab/infra/AWS_S3_ENDPOINT_URL"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "s3_endpoint_url" {
  secret_id     = aws_secretsmanager_secret.s3_endpoint_url.id
  secret_string = "https://s3.${var.aws_region}.amazonaws.com"
}
