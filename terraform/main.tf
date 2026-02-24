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
    BROKER                 = var.broker
    ANGEL_ONE_API_KEY      = var.angel_one_api_key
    ANGEL_ONE_CLIENT_ID    = var.angel_one_client_id
    ANGEL_ONE_PASSWORD     = var.angel_one_password
    ANGEL_ONE_TOTP_SECRET  = var.angel_one_totp_secret
    ZERODHA_API_KEY        = var.zerodha_api_key
    ZERODHA_API_SECRET     = var.zerodha_api_secret
    ZERODHA_REDIRECT_URL   = var.zerodha_redirect_url
    SLACK_BOT_TOKEN        = var.slack_bot_token
    SLACK_APP_TOKEN        = var.slack_app_token
    SLACK_SIGNING_SECRET   = var.slack_signing_secret
    SLACK_TRADING_CHANNEL  = var.slack_trading_channel
    DB_PASSWORD            = var.db_password
    FUND_SIZE_INR          = var.fund_size_inr
  })
}

# ── IAM — ECS execution role (pulls image + injects secrets) ─────────────

resource "aws_iam_role" "ecs_execution" {
  name = "${local.name_prefix}-ecs-execution"
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

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "${local.name_prefix}-ecs-execution-secrets"
  role = aws_iam_role.ecs_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.bot_secrets.arn
    }]
  })
}

# ── IAM — ECS task role (runtime permissions for the container) ───────────

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

# ── IAM — GitHub Actions OIDC role (CI/CD deploy) ────────────────────────

resource "aws_iam_role" "github_actions" {
  name = "${local.name_prefix}-github-actions"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = "repo:VeldandiKarthikKumar/fund-management-bot:*"
        }
      }
    }]
  })
  tags = local.common_tags
}

resource "aws_iam_role_policy" "github_actions" {
  name = "${local.name_prefix}-github-actions-policy"
  role = aws_iam_role.github_actions.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ECRAuth"
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "ECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
        ]
        Resource = aws_ecr_repository.bot.arn
      },
      {
        Sid    = "ECSdeploy"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition",
        ]
        Resource = "*"
      },
      {
        Sid    = "PassExecutionRole"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_task.arn,
          aws_iam_role.ecs_execution.arn,
        ]
      }
    ]
  })
}

data "aws_caller_identity" "current" {}

# ── Outputs ───────────────────────────────────────────────────────────────

output "ecr_repository_url"       { value = aws_ecr_repository.bot.repository_url }
output "s3_bucket_name"           { value = aws_s3_bucket.tokens.bucket }
output "secrets_arn"              { value = aws_secretsmanager_secret.bot_secrets.arn }
output "github_actions_role_arn"  { value = aws_iam_role.github_actions.arn }
