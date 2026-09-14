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
# is referenced directly from the Loki Argo App's Helm values; nothing
# else consumes it.
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
