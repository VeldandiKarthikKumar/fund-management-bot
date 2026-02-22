terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  # Remote state — uncomment after creating the bucket
  # backend "s3" {
  #   bucket = "fundbot-terraform-state"
  #   key    = "prod/terraform.tfstate"
  #   region = "ap-south-1"
  # }
}

provider "aws" {
  region = var.aws_region
}

locals {
  name_prefix = "fundbot-${var.environment}"
  common_tags = {
    Project     = "fund-management-bot"
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ── ECR — Docker image registry ───────────────────────────────────────────

resource "aws_ecr_repository" "bot" {
  name                 = var.ecr_repo_name
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
  tags = local.common_tags
}

# ── S3 — Token storage ────────────────────────────────────────────────────

resource "aws_s3_bucket" "tokens" {
  bucket = "${local.name_prefix}-tokens"
  tags   = local.common_tags
}

resource "aws_s3_bucket_versioning" "tokens" {
  bucket = aws_s3_bucket.tokens.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tokens" {
  bucket = aws_s3_bucket.tokens.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "tokens" {
  bucket                  = aws_s3_bucket.tokens.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Secrets Manager — all credentials ────────────────────────────────────

resource "aws_secretsmanager_secret" "bot_secrets" {
  name = "${local.name_prefix}/credentials"
  tags = local.common_tags
}

resource "aws_secretsmanager_secret_version" "bot_secrets" {
  secret_id = aws_secretsmanager_secret.bot_secrets.id
  secret_string = jsonencode({
    ZERODHA_API_KEY       = var.zerodha_api_key
    ZERODHA_API_SECRET    = var.zerodha_api_secret
    ZERODHA_REDIRECT_URL  = var.zerodha_redirect_url
    SLACK_BOT_TOKEN       = var.slack_bot_token
    SLACK_APP_TOKEN       = var.slack_app_token
    SLACK_SIGNING_SECRET  = var.slack_signing_secret
    SLACK_TRADING_CHANNEL = var.slack_trading_channel
    DB_PASSWORD           = var.db_password
    FUND_SIZE_INR         = var.fund_size_inr
  })
}

# ── IAM — ECS task role ───────────────────────────────────────────────────

resource "aws_iam_role" "ecs_task" {
  name = "${local.name_prefix}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "ecs_task" {
  name = "${local.name_prefix}-ecs-task-policy"
  role = aws_iam_role.ecs_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.tokens.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.bot_secrets.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      }
    ]
  })
}

# ── CloudWatch Logs ───────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "bot" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ── Outputs ───────────────────────────────────────────────────────────────

output "ecr_repository_url"    { value = aws_ecr_repository.bot.repository_url }
output "s3_bucket_name"        { value = aws_s3_bucket.tokens.bucket }
output "secrets_arn"           { value = aws_secretsmanager_secret.bot_secrets.arn }
