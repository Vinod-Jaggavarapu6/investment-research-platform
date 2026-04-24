
resource "aws_secretsmanager_secret" "anthropic_key" {
  name = "${var.project}/anthropic-api-key"
}

resource "aws_secretsmanager_secret_version" "anthropic_key" {
  secret_id     = aws_secretsmanager_secret.anthropic_key.id
  secret_string = var.anthropic_api_key
}

resource "aws_secretsmanager_secret" "openai_key" {
  name = "${var.project}/openai-api-key"
}

resource "aws_secretsmanager_secret_version" "openai_key" {
  secret_id     = aws_secretsmanager_secret.openai_key.id
  secret_string = var.openai_api_key
}

resource "aws_secretsmanager_secret" "langsmith_key" {
  name = "${var.project}/langsmith-api-key"
}

resource "aws_secretsmanager_secret_version" "langsmith_key" {
  secret_id     = aws_secretsmanager_secret.langsmith_key.id
  secret_string = var.langsmith_api_key
}

resource "aws_secretsmanager_secret" "finnhub_key" {
  name = "${var.project}/finnhub-api-key"
}

resource "aws_secretsmanager_secret_version" "finnhub_key" {
  secret_id     = aws_secretsmanager_secret.finnhub_key.id
  secret_string = var.finnhub_api_key
}