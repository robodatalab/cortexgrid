# VPC isolating RDS. Lambda lives here too and uses a NAT gateway for
# outbound internet access (needed for SES API calls).

resource "aws_vpc" "auth" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "robolab-auth-vpc" }
}

# Two private subnets (different AZs) — required for the RDS subnet group.
resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.auth.id
  cidr_block        = "10.0.1.0/24"
  availability_zone = "${var.aws_region}a"
  tags              = { Name = "robolab-auth-private-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.auth.id
  cidr_block        = "10.0.2.0/24"
  availability_zone = "${var.aws_region}b"
  tags              = { Name = "robolab-auth-private-b" }
}

# One public subnet for the NAT gateway.
resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.auth.id
  cidr_block              = "10.0.0.0/24"
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true
  tags                    = { Name = "robolab-auth-public" }
}

resource "aws_internet_gateway" "auth" {
  vpc_id = aws_vpc.auth.id
  tags   = { Name = "robolab-auth-igw" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "robolab-auth-nat-eip" }
}

resource "aws_nat_gateway" "auth" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
  depends_on    = [aws_internet_gateway.auth]
  tags          = { Name = "robolab-auth-nat" }
}

# Route tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.auth.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.auth.id
  }
  tags = { Name = "robolab-auth-rt-public" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.auth.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.auth.id
  }
  tags = { Name = "robolab-auth-rt-private" }
}

resource "aws_route_table_association" "private_a" {
  subnet_id      = aws_subnet.private_a.id
  route_table_id = aws_route_table.private.id
}

resource "aws_route_table_association" "private_b" {
  subnet_id      = aws_subnet.private_b.id
  route_table_id = aws_route_table.private.id
}
