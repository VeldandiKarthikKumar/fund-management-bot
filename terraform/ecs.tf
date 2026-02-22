# ── ECS Cluster ───────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "fundbot" {
  name = local.name_prefix
  tags = local.common_tags
}

resource "aws_ecs_cluster_capacity_providers" "fundbot" {
  cluster_name       = aws_ecs_cluster.fundbot.name
  capacity_providers = ["FARGATE_SPOT"]  # Cost-optimized

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }
}

# ── ECS Task definition (Slack bot — long-running) ────────────────────────

resource "aws_ecs_task_definition" "slack_bot" {
  family                   = "${local.name_prefix}-slack-bot"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_task.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "slack-bot"
    image     = "${aws_ecr_repository.bot.repository_url}:latest"
    command   = ["python", "-m", "src.slack.app"]
    essential = true

    environment = [
      { name = "DATABASE_URL", value = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.postgres.endpoint}/fundbot" }
    ]

    secrets = [
      { name = "ZERODHA_API_KEY",       valueFrom = "${aws_secretsmanager_secret.bot_secrets.arn}:ZERODHA_API_KEY::" },
      { name = "ZERODHA_API_SECRET",    valueFrom = "${aws_secretsmanager_secret.bot_secrets.arn}:ZERODHA_API_SECRET::" },
      { name = "SLACK_BOT_TOKEN",       valueFrom = "${aws_secretsmanager_secret.bot_secrets.arn}:SLACK_BOT_TOKEN::" },
      { name = "SLACK_APP_TOKEN",       valueFrom = "${aws_secretsmanager_secret.bot_secrets.arn}:SLACK_APP_TOKEN::" },
      { name = "SLACK_SIGNING_SECRET",  valueFrom = "${aws_secretsmanager_secret.bot_secrets.arn}:SLACK_SIGNING_SECRET::" },
      { name = "SLACK_TRADING_CHANNEL", valueFrom = "${aws_secretsmanager_secret.bot_secrets.arn}:SLACK_TRADING_CHANNEL::" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.bot.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "slack-bot"
      }
    }
  }])

  tags = local.common_tags
}

# ── ECS Service (keeps the Slack bot always running) ──────────────────────

resource "aws_ecs_service" "slack_bot" {
  name            = "${local.name_prefix}-slack-bot"
  cluster         = aws_ecs_cluster.fundbot.id
  task_definition = aws_ecs_task_definition.slack_bot.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.ecs.id]
    assign_public_ip = true
  }

  tags = local.common_tags
}

output "ecs_cluster_name" { value = aws_ecs_cluster.fundbot.name }
