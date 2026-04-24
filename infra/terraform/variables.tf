variable "aws_region" {
  default = "us-east-1"
}

variable "project" {
  default = "investment-research"
}

variable "db_username" {
  default = "postgres"
}

variable "db_password" {
  description = "RDS master password"
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Anthropic API key"
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  sensitive   = true
}

variable "langsmith_api_key" {
  description = "LangSmith API key"
  sensitive   = true
}

variable "finnhub_api_key" {
  description = "Finnhub API key"
  sensitive   = true
}