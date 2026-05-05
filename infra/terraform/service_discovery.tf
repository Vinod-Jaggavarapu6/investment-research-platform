# AWS Cloud Map — private DNS so ECS services find each other by name
# DNS pattern: <service>.investment-research.local

resource "aws_service_discovery_private_dns_namespace" "main" {
  name        = "investment-research.local"
  description = "Internal DNS for investment-research ECS services"
  vpc         = module.vpc.vpc_id
}

# backend.investment-research.local — Prometheus scrapes this
resource "aws_service_discovery_service" "backend" {
  name = "backend"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {}
}

# jaeger.investment-research.local — backend sends OTLP traces here
resource "aws_service_discovery_service" "jaeger" {
  name = "jaeger"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {}
}

# prometheus.investment-research.local — Grafana queries metrics here
resource "aws_service_discovery_service" "prometheus" {
  name = "prometheus"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.main.id
    dns_records {
      ttl  = 10
      type = "A"
    }
    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {}
}
