data "aws_vpc" "default" {
  default = true
}

data "aws_route_tables" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Gateway endpoint: free, in-VPC routing for s3.eu-west-2.amazonaws.com from
# anything inside the default VPC. Keeps EC2 head <-> S3 traffic off public
# internet (no NAT cost, no egress meter).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = data.aws_vpc.default.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = data.aws_route_tables.default.ids

  tags = {
    Project   = "robolab"
    Component = "s3"
  }
}
