# S3 Gateway endpoint — free, attached to the private route table so anything
# in the private subnets reaches s3.eu-west-2.amazonaws.com without traversing
# NAT (no egress charge, no NAT data processing).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]

  tags = {
    Name      = "robolab-s3"
    Project   = "robolab"
    Component = "s3-endpoint"
  }
}
