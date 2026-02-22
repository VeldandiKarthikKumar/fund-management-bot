# ── VPC (use default VPC for simplicity; replace with custom for prod) ─────

data "aws_vpc" "default" { default = true }

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# ── Security group for RDS ────────────────────────────────────────────────

resource "aws_security_group" "rds" {
  name   = "${local.name_prefix}-rds"
  vpc_id = data.aws_vpc.default.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
    description     = "Allow ECS tasks to reach RDS"
  }

  tags = local.common_tags
}

resource "aws_security_group" "ecs" {
  name   = "${local.name_prefix}-ecs"
  vpc_id = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

# ── RDS subnet group ──────────────────────────────────────────────────────

resource "aws_db_subnet_group" "fundbot" {
  name       = "${local.name_prefix}-db"
  subnet_ids = data.aws_subnets.default.ids
  tags       = local.common_tags
}

# ── RDS PostgreSQL instance ───────────────────────────────────────────────

resource "aws_db_instance" "postgres" {
  identifier              = "${local.name_prefix}-db"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t3.micro"   # ~$15/mo; upgrade as needed
  allocated_storage       = 20
  max_allocated_storage   = 100             # Auto-scaling storage

  db_name  = "fundbot"
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.fundbot.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 7               # 7-day automated backups
  backup_window           = "02:00-03:00"   # 2–3 AM UTC (7:30–8:30 AM IST)
  maintenance_window      = "sun:03:00-sun:04:00"

  deletion_protection     = true            # Prevent accidental deletion
  skip_final_snapshot     = false
  final_snapshot_identifier = "${local.name_prefix}-final"

  tags = local.common_tags
}

output "rds_endpoint" { value = aws_db_instance.postgres.endpoint }
output "rds_db_name"  { value = aws_db_instance.postgres.db_name }
