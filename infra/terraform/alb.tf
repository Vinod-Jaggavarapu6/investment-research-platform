# Security group for the ALB — accepts public HTTP/HTTPS traffic
resource "aws_security_group" "alb" {
  name   = "${var.project}-alb-sg"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "main" {
  name               = "${var.project}-alb"
  internal           = false  # Public-facing
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = module.vpc.public_subnets
}

# Target group for backend — ALB forwards API traffic here
resource "aws_lb_target_group" "backend" {
  name        = "${var.project}-backend-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = module.vpc.vpc_id
  target_type = "ip"  # Required for Fargate

  health_check {
    path                = "/health"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 10
  }
}

# Target group for frontend
resource "aws_lb_target_group" "frontend" {
  name        = "${var.project}-frontend-tg"
  port        = 80
  protocol    = "HTTP"
  vpc_id      = module.vpc.vpc_id
  target_type = "ip"

  health_check {
    path              = "/"
    healthy_threshold = 2
    interval          = 30
  }
}

# HTTP listener — routes traffic to the right target group
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # Default: send to frontend
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

# Routing rules — backend API paths (AWS ALB caps path_pattern at 5 values per rule)
resource "aws_lb_listener_rule" "backend" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }

  condition {
    path_pattern {
      values = ["/api/*", "/research/*", "/analyze/*", "/filings/*", "/news/*"]
    }
  }
}

resource "aws_lb_listener_rule" "backend_extra" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 101

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }

  condition {
    path_pattern {
      values = ["/retrieve/*", "/health"]
    }
  }
}

output "alb_dns_name" {
  value       = aws_lb.main.dns_name
  description = "ALB DNS name — use this to access the app"
}