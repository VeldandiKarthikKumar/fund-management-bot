variable "aws_region"           { default = "ap-south-1" }
variable "environment"          { default = "prod" }
variable "zerodha_api_key"      { sensitive = true }
variable "zerodha_api_secret"   { sensitive = true }
variable "zerodha_redirect_url" {}
variable "slack_bot_token"      { sensitive = true }
variable "slack_app_token"      { sensitive = true }
variable "slack_signing_secret" { sensitive = true }
variable "slack_trading_channel"{}
variable "db_username"          { default = "fundbot" }
variable "db_password"          { sensitive = true }
variable "fund_size_inr"        { default = "500000" }
variable "ecr_repo_name"        { default = "fund-management-bot" }
