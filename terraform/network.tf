# ── Static outbound IP via NAT Gateway ───────────────────────────────────
#
# Angel One SmartAPI whitelists by IP. Fargate tasks with assign_public_ip=true
# get a random ephemeral IP on every restart — that breaks the whitelist.
# Solution: put ECS tasks in private subnets; all outbound traffic exits via
# a NAT Gateway that has a fixed Elastic IP. Register that EIP with Angel One.
#
# The default VPC already has an IGW and public subnets — we reuse them here.

data "aws_internet_gateway" "default" {
  filter {
    name   = "attachment.vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# One private subnet per AZ (up to 2) for ECS tasks
data "aws_availability_zones" "available" { state = "available" }

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = data.aws_vpc.default.id
  cidr_block        = "172.31.${96 + count.index * 16}.0/20"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-private-${count.index + 1}" })
}

# Elastic IP — this is the static IP to register in Angel One app settings
resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-nat-eip" })
}

# NAT Gateway lives in the first default (public) subnet
data "aws_subnet" "first_public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
  availability_zone = data.aws_availability_zones.available.names[0]
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = data.aws_subnet.first_public.id
  tags          = merge(local.common_tags, { Name = "${local.name_prefix}-nat" })

  depends_on = [data.aws_internet_gateway.default]
}

# Route table for private subnets → NAT Gateway
resource "aws_route_table" "private" {
  vpc_id = data.aws_vpc.default.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-private-rt" })
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ── Outputs ───────────────────────────────────────────────────────────────

output "nat_static_ip" {
  value       = aws_eip.nat.public_ip
  description = "Register this IP in Angel One app settings → Primary Static IP"
}
