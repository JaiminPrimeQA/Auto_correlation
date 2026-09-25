# Dedicated VPC with three subnet tiers per AZ:
#   public   - load balancer and NAT gateway(s)
#   private  - API, frontend and worker tasks
#   runner   - one-shot Newman runner tasks (untrusted collection scripts)
#
# The runner tier's network ACL is the network-level egress control of spec §8:
# it denies every private, carrier-grade NAT, loopback and link-local range, so
# neither a collection script nor a DNS answer rebound to a private address can
# reach anything inside the VPC or peered networks. Only https to public
# addresses leaves the subnet.
#
# NACLs never filter the Amazon DNS resolver or the ECS task-metadata/credential
# endpoints. The runner task is safe from the latter because it has NO task
# role: the credential endpoint has nothing to hand out.

locals {
  name = "b11-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
  # The Amazon-provided DNS resolver: the VPC base address + 2.
  vpc_resolver = "${cidrhost(var.vpc_cidr, 2)}/32"
  blocked_ranges = {
    110 = "10.0.0.0/8"
    120 = "172.16.0.0/12"
    130 = "192.168.0.0/16"
    140 = "100.64.0.0/10"
    150 = "169.254.0.0/16"
    160 = "127.0.0.0/8"
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.main.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = false
  tags                    = { Name = "${local.name}-public-${local.azs[count.index]}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  tags              = { Name = "${local.name}-private-${local.azs[count.index]}", Tier = "private" }
}

resource "aws_subnet" "runner" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 20 + count.index)
  tags              = { Name = "${local.name}-runner-${local.azs[count.index]}", Tier = "runner" }
}

resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : length(local.azs)
  domain = "vpc"
  tags   = { Name = "${local.name}-nat-${count.index}" }
}

resource "aws_nat_gateway" "main" {
  count         = length(aws_eip.nat)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${local.name}-nat-${count.index}" }
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  count  = length(local.azs)
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[var.single_nat_gateway ? 0 : count.index].id
  }
  tags = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "runner" {
  count          = length(aws_subnet.runner)
  subnet_id      = aws_subnet.runner[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# S3 traffic stays on the AWS network (and needs no NAT data processing).
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id
  tags              = { Name = "${local.name}-s3" }
}

resource "aws_network_acl" "runner" {
  vpc_id     = aws_vpc.main.id
  subnet_ids = aws_subnet.runner[*].id
  tags       = { Name = "${local.name}-runner" }

  # --- egress ---
  egress {
    rule_no    = 100
    action     = "allow"
    protocol   = "udp"
    cidr_block = local.vpc_resolver
    from_port  = 53
    to_port    = 53
  }
  egress {
    rule_no    = 101
    action     = "allow"
    protocol   = "tcp"
    cidr_block = local.vpc_resolver
    from_port  = 53
    to_port    = 53
  }
  dynamic "egress" {
    for_each = local.blocked_ranges
    content {
      rule_no    = egress.key
      action     = "deny"
      protocol   = "-1"
      cidr_block = egress.value
      from_port  = 0
      to_port    = 0
    }
  }
  egress {
    rule_no    = 200
    action     = "allow"
    protocol   = "tcp"
    cidr_block = "0.0.0.0/0"
    from_port  = 443
    to_port    = 443
  }

  # --- ingress (stateless: return traffic only) ---
  ingress {
    rule_no    = 100
    action     = "allow"
    protocol   = "udp"
    cidr_block = local.vpc_resolver
    from_port  = 1024
    to_port    = 65535
  }
  ingress {
    rule_no    = 101
    action     = "allow"
    protocol   = "tcp"
    cidr_block = local.vpc_resolver
    from_port  = 1024
    to_port    = 65535
  }
  dynamic "ingress" {
    for_each = local.blocked_ranges
    content {
      rule_no    = ingress.key
      action     = "deny"
      protocol   = "-1"
      cidr_block = ingress.value
      from_port  = 0
      to_port    = 0
    }
  }
  ingress {
    rule_no    = 200
    action     = "allow"
    protocol   = "tcp"
    cidr_block = "0.0.0.0/0"
    from_port  = 1024
    to_port    = 65535
  }
}

# --- security groups ---

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTPS entry point"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_http_redirect" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "alb_to_frontend" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.frontend.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "API service: from the ALB only; https out (AWS APIs, OIDC JWKS)"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_security_group" "frontend" {
  name        = "${local.name}-frontend"
  description = "Web UI: from the ALB only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "frontend_from_alb" {
  security_group_id            = aws_security_group.frontend.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}

resource "aws_security_group" "worker" {
  name        = "${local.name}-worker"
  description = "Execution worker: no inbound; https out (AWS APIs, DNS checks)"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "runner" {
  name        = "${local.name}-runner"
  description = "Newman runner tasks: no inbound; https out only (NACL blocks private ranges)"
  vpc_id      = aws_vpc.main.id
}

# https egress for every service tier. Accepted exception to "no egress to
# 0.0.0.0/0": the runner must reach arbitrary public https targets (that is the
# product; its NACL still blocks private ranges), the API fetches the OIDC
# provider's JWKS, and every Fargate task pulls its image and ships logs through
# its own ENI. Only port 443 is open.
#trivy:ignore:AWS-0104
resource "aws_vpc_security_group_egress_rule" "https_out" {
  for_each = {
    api      = aws_security_group.api.id
    frontend = aws_security_group.frontend.id
    worker   = aws_security_group.worker.id
    runner   = aws_security_group.runner.id
  }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
