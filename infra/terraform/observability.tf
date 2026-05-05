# ── ECR repositories for custom observability images ──────────────────────────

resource "aws_ecr_repository" "prometheus" {
  name                 = "${var.project}-prometheus"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_repository" "grafana" {
  name                 = "${var.project}-grafana"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

# ── Security group for observability services ─────────────────────────────────

resource "aws_security_group" "observability" {
  name   = "${var.project}-observability-sg"
  vpc_id = module.vpc.vpc_id

  # Grafana UI — accessed through ALB
  ingress {
    from_port       = 3000
    to_port         = 3000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  # OTLP gRPC — backend sends traces to Jaeger
  ingress {
    from_port       = 4317
    to_port         = 4317
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
  }

  # Inter-service communication (Grafana → Prometheus/Jaeger, Prometheus → Jaeger)
  ingress {
    from_port = 0
    to_port   = 0
    protocol  = "-1"
    self      = true
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Allow Prometheus (in observability SG) to scrape backend /metrics
resource "aws_security_group_rule" "backend_from_prometheus" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.backend.id
  source_security_group_id = aws_security_group.observability.id
  description              = "Prometheus scrape"
}

# ── CloudWatch log groups ─────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "jaeger" {
  name              = "/ecs/${var.project}-jaeger"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "prometheus" {
  name              = "/ecs/${var.project}-prometheus"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "grafana" {
  name              = "/ecs/${var.project}-grafana"
  retention_in_days = 7
}

# ── Jaeger all-in-one — receives OTLP traces, Grafana proxies the UI ──────────

resource "aws_ecs_task_definition" "jaeger" {
  family                   = "${var.project}-jaeger"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name  = "jaeger"
    image = "jaegertracing/all-in-one:1.58"

    portMappings = [
      { containerPort = 16686, protocol = "tcp" },
      { containerPort = 4317,  protocol = "tcp" },
      { containerPort = 4318,  protocol = "tcp" },
    ]

    environment = [
      { name = "COLLECTOR_OTLP_ENABLED", value = "true" },
      { name = "SPAN_STORAGE_TYPE",      value = "memory" },
      { name = "MEMORY_MAX_TRACES",      value = "10000" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.jaeger.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "jaeger"
      }
    }
  }])
}

resource "aws_ecs_service" "jaeger" {
  name            = "${var.project}-jaeger"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.jaeger.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.observability.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.jaeger.arn
  }
}

# ── Prometheus — scrapes backend metrics, Grafana queries it ──────────────────

resource "aws_ecs_task_definition" "prometheus" {
  family                   = "${var.project}-prometheus"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn

  container_definitions = jsonencode([{
    name  = "prometheus"
    image = "${aws_ecr_repository.prometheus.repository_url}:latest"

    portMappings = [
      { containerPort = 9090, protocol = "tcp" }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.prometheus.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "prometheus"
      }
    }
  }])
}

resource "aws_ecs_service" "prometheus" {
  name            = "${var.project}-prometheus"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.prometheus.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.observability.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.prometheus.arn
  }
}

# ── Grafana — visualization; accessible via ALB at /grafana ──────────────────

resource "aws_ecs_task_definition" "grafana" {
  family                   = "${var.project}-grafana"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name  = "grafana"
    image = "${aws_ecr_repository.grafana.repository_url}:latest"

    portMappings = [
      { containerPort = 3000, protocol = "tcp" }
    ]

    environment = [
      { name = "GF_SERVER_ROOT_URL",            value = "http://${aws_lb.main.dns_name}/grafana" },
      { name = "GF_SERVER_SERVE_FROM_SUB_PATH", value = "true" },
      { name = "GF_AUTH_ANONYMOUS_ENABLED",     value = "true" },
      { name = "GF_AUTH_ANONYMOUS_ORG_ROLE",    value = "Admin" },
      { name = "GF_AUTH_DISABLE_LOGIN_FORM",    value = "true" },
      { name = "GF_LOG_LEVEL",                  value = "warn" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.grafana.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "grafana"
      }
    }
  }])
}

resource "aws_ecs_service" "grafana" {
  name            = "${var.project}-grafana"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.grafana.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = module.vpc.private_subnets
    security_groups  = [aws_security_group.observability.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.grafana.arn
    container_name   = "grafana"
    container_port   = 3000
  }

  depends_on = [aws_lb_listener_rule.grafana]
}
