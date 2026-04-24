# Security group for backend containers
resource "aws_security_group" "backend" {
  name   = "${var.project}-backend-sg"
  vpc_id = module.vpc.vpc_id

  # Only accept traffic from the ALB
  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Security group for frontend containers
resource "aws_security_group" "frontend" {
  name   = "${var.project}-frontend-sg"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "${var.project}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# CloudWatch log groups — where container logs go
resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${var.project}-backend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/ecs/${var.project}-frontend"
  retention_in_days = 30
}

# ── Backend Task Definition ───────────────────────────────────────────────────
resource "aws_ecs_task_definition" "backend" {
  family                   = "${var.project}-backend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"  # 1 vCPU
  memory                   = "2048"  # 2 GB
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = "backend"
    image = "${aws_ecr_repository.backend.repository_url}:latest"

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    # Non-secret config — injected as plain environment variables
    environment = [
      { name = "REDIS_PORT",                   value = "6379" },
      { name = "REDIS_MAX_CONNECTIONS",         value = "20" },
      { name = "LANGSMITH_TRACING",             value = "true" },
      { name = "LANGSMITH_PROJECT",             value = "investment-research" },
      { name = "FINANCIAL_AGENT_MODEL",         value = "claude-opus-4-5" },
      { name = "FILINGS_AGENT_MODEL",           value = "claude-opus-4-5" },
      { name = "NEWS_AGENT_MODEL",              value = "claude-opus-4-5" },
      { name = "ROUTER_AGENT_MODEL",            value = "gpt-4o-mini" },
      { name = "SYNTHESIZER_MODEL",             value = "gpt-4o-mini" },
      { name = "NEWS_MAX_ARTICLES",             value = "30" },
      { name = "NEWS_BATCH_SIZE",               value = "15" },
      { name = "NEWS_DAYS_LOOKBACK",            value = "7" },
      { name = "NEWS_AGENT_MAX_TOKENS",         value = "2000" },
      { name = "FINANCIAL_AGENT_MAX_TOKENS",    value = "1500" },
      { name = "FILINGS_AGENT_MAX_TOKENS",      value = "1024" },
      { name = "FILINGS_RETRIEVAL_K",           value = "5" },
      { name = "SYNTHESIZER_MAX_TOKENS",        value = "1024" },
      # These reference Terraform outputs — filled in automatically
      { name = "REDIS_HOST",    value = aws_elasticache_cluster.redis.cache_nodes[0].address },
      { name = "DATABASE_URL",  value = "postgresql+asyncpg://postgres:${var.db_password}@${aws_db_instance.postgres.endpoint}/investment_research" },
      # In aws_ecs_task_definition.backend, add to environment block:
      { name = "S3_BUCKET",  value = "investment-research-app-data-244689413519" },
      { name = "AWS_REGION", value = "us-east-1" },
    ]

    # Secret config — pulled from Secrets Manager at container start
    secrets = [
      { name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.anthropic_key.arn },
      { name = "OPENAI_API_KEY",    valueFrom = aws_secretsmanager_secret.openai_key.arn },
      { name = "LANGSMITH_API_KEY", valueFrom = aws_secretsmanager_secret.langsmith_key.arn },
      { name = "FINNHUB_API_KEY",   valueFrom = aws_secretsmanager_secret.finnhub_key.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "backend"
      }
    }

    healthCheck = {
  command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/health')\""]
  interval    = 30
  timeout     = 10
  retries     = 3
  startPeriod = 60
}
  }])
}

# ── Frontend Task Definition ──────────────────────────────────────────────────
resource "aws_ecs_task_definition" "frontend" {
  family                   = "${var.project}-frontend"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name  = "frontend"
    image = "${aws_ecr_repository.frontend.repository_url}:latest"

    portMappings = [{
      containerPort = 80
      protocol      = "tcp"
    }]

    environment = [
      # In prod, frontend talks to backend via the ALB
      { name = "VITE_API_URL", value = "http://${aws_lb.main.dns_name}" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.frontend.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "frontend"
      }
    }
  }])
}

# ── Indexer Task Definition ───────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "indexer" {
  name              = "/ecs/${var.project}-indexer"
  retention_in_days = 30
}

resource "aws_ecs_task_definition" "indexer" {
  family                   = "${var.project}-indexer"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "2048"   # 2 vCPU — embedding is CPU intensive
  memory                   = "8192"   # 8 GB — FAISS + vectors in RAM
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = "indexer"
    image = "${aws_ecr_repository.indexer.repository_url}:latest"

    environment = [
      { name = "DATABASE_URL", value = "postgresql+asyncpg://postgres:${var.db_password}@${aws_db_instance.postgres.endpoint}/investment_research" },
      { name = "S3_BUCKET",    value = "investment-research-app-data-244689413519" },
      { name = "AWS_REGION",   value = "us-east-1" },
    ]

    secrets = [
      { name = "OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai_key.arn },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/${var.project}-indexer"
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "indexer"
      }
    }
  }])
}

# ── ECS Services ─────────────────────────────────────────────────────────────
resource "aws_ecs_service" "backend" {
  name            = "${var.project}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = 1  # Start with 1, scale up after smoke test
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.backend.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8000
  }

  # Auto rollback if new deployment fails health checks
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http, aws_lb_listener_rule.backend, aws_lb_listener_rule.backend_extra]
}

resource "aws_ecs_service" "frontend" {
  name            = "${var.project}-frontend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.frontend.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.frontend.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.frontend.arn
    container_name   = "frontend"
    container_port   = 80
  }

  depends_on = [aws_lb_listener.http]
}

resource "aws_iam_role_policy" "ecs_s3" {
  name = "${var.project}-ecs-s3"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:GetObject",   # backend needs this to download index on startup
        "s3:PutObject",   # indexer needs this to upload new index after build
      ]
      Resource = "arn:aws:s3:::investment-research-app-data-244689413519/faiss/*"
    }]
  })
}